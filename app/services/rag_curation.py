"""Curadoria de conhecimento a partir das conversas dos vendedores.

Lê as conversas recentes (cliente + consultor), usa a IA para extrair pares
pergunta→resposta e objeções→resposta REUTILIZÁVEIS, remove preço/data/PII, e
grava como PROPOSTAS pendentes. Um humano aprova no /admin → a proposta é
indexada no vetor (RAG). É a etapa "gera conhecimento" do ciclo de aprendizado.
"""

import re
import json
import asyncio
import logging
import sqlite3
import unicodedata

from app.core.database import DB_PATH
from app.services import rag
from app.services.ai_engine import client, _FORBID_PRICE, _FORBID_DATE

logger = logging.getLogger(__name__)

_CURATION_MODEL = "claude-haiku-4-5-20251001"

_PII_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PII_CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_PII_PHONE = re.compile(r"\b(?:55)?\s?\(?\d{2}\)?\s?9?\d{4}[\s-]?\d{4}\b")
_NOISE = re.compile(r"mensagem (textual|n[aã]o)|receveid|image message|audio message|https?://", re.I)


def _strip_pii(text: str) -> str:
    text = _PII_EMAIL.sub("[email]", text or "")
    text = _PII_CPF.sub("[documento]", text)
    text = _PII_PHONE.sub("[telefone]", text)
    return text


def _has_forbidden(text: str) -> bool:
    return bool(_FORBID_PRICE.search(text or "") or _FORBID_DATE.search(text or ""))


# ── Tabela de propostas ───────────────────────────────────────────────────
def _init_sync():
    db = sqlite3.connect(DB_PATH)
    try:
        db.execute(
            """CREATE TABLE IF NOT EXISTS rag_proposals(
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 tipo TEXT DEFAULT 'qa',
                 pergunta TEXT NOT NULL,
                 resposta TEXT NOT NULL,
                 source_phone TEXT DEFAULT '',
                 status TEXT DEFAULT 'pending',
                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 reviewed_at TIMESTAMP)"""
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_prop_status ON rag_proposals(status)")
        db.commit()
    finally:
        db.close()


# Dedup por SIMILARIDADE (não só texto idêntico): normaliza em conjunto de palavras-chave
# (sem acento/pontuação/stopwords) e considera duplicada se uma pergunta "contém" a outra
# (>=85% das palavras da menor). Mantém 'Módulo I' ≠ 'Módulo II' (i/ii são preservados).
_DUP_STOP = set(
    "o a e é u os as de do da dos das no na nos nas em um uma uns umas para por com sem "
    "que qual quais qual quem como onde quando quanto quanta se ao aos sobre são sao é ser "
    "voce voces você vocês pela pelo pelas pelos tem teria ha há sua seu suas seus meu minha".split()
)


def _norm_tokens(s: str) -> set:
    s = unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return {t for t in s.split() if t and t not in _DUP_STOP}


def _dup_exists_sync(pergunta: str) -> bool:
    nova = _norm_tokens(pergunta)
    if not nova:
        return False
    db = sqlite3.connect(DB_PATH)
    try:
        # dedup contra TUDO já visto (aprovada, pendente OU rejeitada) — nada que o
        # gestor já decidiu volta a aparecer.
        for (q,) in db.execute("SELECT pergunta FROM rag_proposals"):
            ex = _norm_tokens(q)
            if not ex:
                continue
            inter = len(nova & ex)
            menor = min(len(nova), len(ex))
            if menor and inter / menor >= 0.85:   # uma contém ~toda a outra → redundante
                return True
        return False
    finally:
        db.close()


# Dedup SEMÂNTICO contra o conhecimento JÁ APROVADO (indexado no RAG). Pega repetições
# reformuladas ("tópicos do Python" ≈ "conteúdos do Python") que o dedup por palavras não vê.
_RAG_DUP_HARD = 2.2   # distância muito baixa → duplicata certa
_RAG_DUP_SOFT = 2.6   # distância média → duplicata só se as palavras também baterem


async def _rag_dup(pergunta: str) -> bool:
    """True se a pergunta já é coberta por conhecimento aprovado (busca semântica no RAG)."""
    if not rag.is_enabled():
        return False
    try:
        hits = await rag.search(pergunta, k=1)
    except Exception:
        return False
    if not hits:
        return False
    d = hits[0].get("distance", 99)
    if d < _RAG_DUP_HARD:
        return True
    if d < _RAG_DUP_SOFT:
        a = _norm_tokens(pergunta)
        b = _norm_tokens(hits[0].get("text", ""))
        menor = min(len(a), len(b)) or 1
        if len(a & b) / menor >= 0.4:   # distância média + palavras batendo → duplicata
            return True
    return False


def _save_sync(tipo, pergunta, resposta, phone):
    db = sqlite3.connect(DB_PATH)
    try:
        db.execute(
            "INSERT INTO rag_proposals(tipo, pergunta, resposta, source_phone) VALUES (?,?,?,?)",
            (tipo, pergunta, resposta, phone),
        )
        db.commit()
    finally:
        db.close()


# ── Transcrição da conversa ───────────────────────────────────────────────
async def _get_transcript(phone: str, limit: int = 60) -> str:
    from app.core.database import get_db
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT role, message FROM conversations WHERE phone_number=? ORDER BY created_at, id LIMIT ?",
            (phone, limit),
        )
        rows = await cur.fetchall()
    finally:
        await db.close()
    lines = []
    for r in rows:
        role = r["role"]
        msg = (r["message"] or "").strip()
        if not msg or _NOISE.search(msg):
            continue
        who = {"user": "Cliente", "consultant": "Consultor", "assistant": "Bot"}.get(role, role)
        lines.append(f"{who}: {msg}")
    return "\n".join(lines)[:6000]


# ── Extração via IA ───────────────────────────────────────────────────────
async def _extract(transcript: str):
    prompt = (
        "Você recebe uma conversa REAL de atendimento da área de TREINAMENTOS da Impacta "
        "(cursos livres e treinamentos corporativos — Excel, Power BI, Python, vendas, informática, etc.). "
        "Extraia pares REUTILIZÁVEIS que ajudariam um bot de Treinamentos a atender melhor: perguntas "
        "frequentes com boas respostas (tipo 'qa') e objeções com a resposta que funcionou (tipo 'objecao'). "
        "REGRA CRÍTICA: IGNORE totalmente qualquer coisa de GRADUAÇÃO / FACULDADE / MBA / pós-graduação / "
        "vestibular / bolsa / matrícula acadêmica — isso é de OUTRA área, não entra na base de Treinamentos. "
        "NÃO inclua preços, valores, descontos, parcelamento nem datas de turma. "
        "NÃO inclua dados pessoais (nomes, telefones, e-mails). Generalize (nada específico de um aluno). "
        "Se não houver nada reutilizável (ou for de graduação), retorne lista vazia. Responda SÓ JSON: "
        '{"pares":[{"tipo":"qa|objecao","pergunta":"...","resposta":"..."}]}\n\nCONVERSA:\n' + transcript
    )
    try:
        resp = await client.messages.create(
            model=_CURATION_MODEL, max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        txt = resp.content[0].text
        m = re.search(r"\{.*\}", txt, re.S)
        data = json.loads(m.group(0)) if m else {}
        return data.get("pares", [])
    except Exception as e:
        logger.error(f"[Curadoria] Erro na extração: {e}")
        return []


async def curate_recent(days: int = 2, max_convos: int = 25) -> int:
    """Gera propostas de conhecimento das conversas com consultor nos últimos dias."""
    from app.core.database import get_db
    await asyncio.to_thread(_init_sync)
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT DISTINCT phone_number FROM conversations "
            "WHERE role IN ('consultant','operator','agent') AND created_at >= date('now', ?) LIMIT ?",
            (f"-{days} day", max_convos * 2),  # pega mais, pois vamos filtrar faculdade/graduação
        )
        phones = [r[0] for r in await cur.fetchall()]
    finally:
        await db.close()

    # Só aprende com conversas de TREINAMENTOS — exclui faculdade/graduação (mesma régua do digest)
    from app.services.turma_fechada import _channel_of_sync, _CORP_CHANNELS
    from app.services.sales_digest import _is_faculdade_phone

    def _filtra(ps):
        return [p for p in ps if _channel_of_sync(p) in _CORP_CHANNELS and not _is_faculdade_phone(p)]

    phones = (await asyncio.to_thread(_filtra, phones))[:max_convos]

    total = 0
    for phone in phones:
        transcript = await _get_transcript(phone)
        if len(transcript) < 80:
            continue
        for p in await _extract(transcript):
            q = _strip_pii((p.get("pergunta") or "").strip())
            a = _strip_pii((p.get("resposta") or "").strip())
            tipo = p.get("tipo", "qa")
            if len(q) < 8 or len(a) < 8:
                continue
            if _has_forbidden(q + " " + a):
                continue  # descarta qualquer coisa com preço/data
            if await asyncio.to_thread(_dup_exists_sync, q):
                continue
            if await _rag_dup(q):
                continue  # já coberto por conhecimento aprovado (semântico)
            await asyncio.to_thread(_save_sync, tipo, q, a, phone)
            total += 1
        await asyncio.sleep(0.3)  # respeita rate limit
    logger.info(f"[Curadoria] {total} propostas geradas de {len(phones)} conversas (últimos {days}d).")
    return total


# ── Fila de aprovação ─────────────────────────────────────────────────────
def _list_sync(status, limit):
    db = sqlite3.connect(DB_PATH); db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            "SELECT * FROM rag_proposals WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def _get_sync(pid):
    db = sqlite3.connect(DB_PATH); db.row_factory = sqlite3.Row
    try:
        r = db.execute("SELECT * FROM rag_proposals WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None
    finally:
        db.close()


def _set_status_sync(pid, status):
    db = sqlite3.connect(DB_PATH)
    try:
        db.execute("UPDATE rag_proposals SET status=?, reviewed_at=CURRENT_TIMESTAMP WHERE id=?", (status, pid))
        db.commit()
    finally:
        db.close()


def _update_text_sync(pid, pergunta, resposta):
    db = sqlite3.connect(DB_PATH)
    try:
        db.execute("UPDATE rag_proposals SET pergunta=?, resposta=? WHERE id=?", (pergunta, resposta, pid))
        db.commit()
    finally:
        db.close()


async def list_pending(limit: int = 100):
    await asyncio.to_thread(_init_sync)
    return await asyncio.to_thread(_list_sync, "pending", limit)


async def approve(pid: int, pergunta: str = None, resposta: str = None) -> bool:
    """Aprova a proposta e a indexa no vetor (RAG). Aceita texto editado."""
    if pergunta is not None and resposta is not None:
        await asyncio.to_thread(_update_text_sync, pid, _strip_pii(pergunta.strip()), _strip_pii(resposta.strip()))
    prop = await asyncio.to_thread(_get_sync, pid)
    if not prop or prop["status"] != "pending":
        return False
    text = f"Pergunta: {prop['pergunta']}\nResposta: {prop['resposta']}"
    await rag.index_items([{"text": text, "source": "curated",
                            "doc_type": prop["tipo"], "ref_id": pid}])
    await asyncio.to_thread(_set_status_sync, pid, "approved")
    logger.info(f"[Curadoria] Proposta {pid} aprovada e indexada.")
    return True


async def reject(pid: int) -> bool:
    await asyncio.to_thread(_set_status_sync, pid, "rejected")
    return True
