"""Copiloto do Gestor — resumo estratégico diário das conversas de Treinamentos (PJ).

Todo dia de manhã, olha as conversas do dia anterior (área corporativa: Treinamentos +
Site) e monta um email para o gestor com:
  • números com significado (volume, novos leads, temperatura, turma fechada, atendimento);
  • leitura estratégica via IA (temas em alta, objeções, oportunidades, risco de perda);
  • recomendações acionáveis (onde colocar força, quem ligar hoje).

Reusa scheduler + email (Gmail) + Claude já existentes. Escopo: canais corporativos
(`_CORP_CHANNELS` do turma_fechada). NÃO inclui Faculdade/Cobrança.
"""

import json
import logging
import asyncio
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Tuple, Optional

from app.core.database import DB_PATH
from app.services.turma_fechada import _channel_of_sync, _CORP_CHANNELS, is_turma_fechada

logger = logging.getLogger(__name__)

BRT = timezone(timedelta(hours=-3))

# Limites de custo/tamanho ao alimentar o Claude
_MAX_LEADS_ANALYSIS = 40      # nº máx de conversas enviadas para análise
_MAX_MSGS_PER_LEAD = 18       # mensagens por conversa
_MAX_MSG_CHARS = 220          # chars por mensagem


def _yesterday_brt() -> str:
    """Data (YYYY-MM-DD) de ONTEM no fuso BRT."""
    return (datetime.now(BRT) - timedelta(days=1)).strftime("%Y-%m-%d")


# ── Coleta de dados (síncrona, sqlite direto) ──────────────────────────────

def _corporate_phones_sync(day: str) -> List[str]:
    """Telefones com atividade no dia (BRT) cujo canal é corporativo (Treinamentos/Site)."""
    db = sqlite3.connect(DB_PATH)
    try:
        phones = [
            r[0] for r in db.execute(
                "SELECT DISTINCT phone_number FROM conversations "
                "WHERE date(datetime(created_at,'-3 hours'))=? AND phone_number IS NOT NULL",
                (day,),
            ).fetchall()
        ]
    finally:
        db.close()
    corp = []
    for p in phones:
        ch = _channel_of_sync(p)
        # estrito: só canal corporativo conhecido (evita contaminar com Faculdade)
        if ch in _CORP_CHANNELS:
            corp.append(p)
    return corp


def _messages_sync(phone: str) -> List[Tuple[str, str]]:
    """Últimas mensagens (role, message) da conversa, mais antigas → recentes."""
    db = sqlite3.connect(DB_PATH)
    try:
        rows = db.execute(
            "SELECT role, message FROM conversations WHERE phone_number=? "
            "ORDER BY id DESC LIMIT ?",
            (phone, _MAX_MSGS_PER_LEAD),
        ).fetchall()
    finally:
        db.close()
    return list(reversed([(r[0] or "", r[1] or "") for r in rows]))


def _lead_sync(phone: str) -> Dict[str, Any]:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute("SELECT * FROM leads WHERE phone_number=?", (phone,)).fetchone()
        return dict(row) if row else {}
    finally:
        db.close()


def _new_leads_count_sync(day: str, phones: List[str]) -> int:
    if not phones:
        return 0
    db = sqlite3.connect(DB_PATH)
    try:
        marks = ",".join("?" * len(phones))
        n = db.execute(
            f"SELECT COUNT(*) FROM leads WHERE date(datetime(created_at,'-3 hours'))=? "
            f"AND phone_number IN ({marks})",
            (day, *phones),
        ).fetchone()[0]
        return int(n or 0)
    finally:
        db.close()


def _label(lead: Dict, phone: str) -> str:
    return (lead.get("company") or lead.get("contact_name") or phone or "Lead").strip()


# ── Métricas determinísticas ───────────────────────────────────────────────

def _compute_metrics(day: str, records: List[Dict]) -> Dict[str, Any]:
    temp = {"quente": 0, "morno": 0, "frio": 0, "n/i": 0}
    trail = {}
    atend = {"Bot": 0, "Vendedor": 0, "Bot + Vendedor": 0, "—": 0}
    tf = 0
    temas = {}
    for r in records:
        lead = r["lead"]
        t = (lead.get("lead_temperature") or "n/i").lower()
        temp[t if t in temp else "n/i"] = temp.get(t if t in temp else "n/i", 0) + 1
        tr = (lead.get("trail") or "—").upper()
        trail[tr] = trail.get(tr, 0) + 1
        atend[r["atendido"]] = atend.get(r["atendido"], 0) + 1
        if is_turma_fechada(lead):
            tf += 1
        ti = (lead.get("tipo_interesse") or "").strip()
        if ti:
            temas[ti] = temas.get(ti, 0) + 1
    return {
        "dia": day,
        "total_conversas": len(records),
        "novos_leads": _new_leads_count_sync(day, [r["phone"] for r in records]),
        "temperatura": temp,
        "trail": trail,
        "atendimento": atend,
        "turma_fechada": tf,
        "temas": dict(sorted(temas.items(), key=lambda kv: kv[1], reverse=True)),
    }


async def _gather(day: str) -> Tuple[Dict[str, Any], List[Dict]]:
    phones = await asyncio.to_thread(_corporate_phones_sync, day)
    records = []
    for ph in phones[:_MAX_LEADS_ANALYSIS]:
        msgs = await asyncio.to_thread(_messages_sync, ph)
        if not msgs:
            continue
        lead = await asyncio.to_thread(_lead_sync, ph)
        roles = {r for r, _ in msgs}
        tem_bot = "assistant" in roles
        tem_vend = bool({"consultant", "operator", "agent"} & roles)
        atendido = ("Bot + Vendedor" if (tem_bot and tem_vend)
                    else "Vendedor" if tem_vend else "Bot" if tem_bot else "—")
        records.append({"phone": ph, "lead": lead, "msgs": msgs, "atendido": atendido})
    metrics = _compute_metrics(day, records)
    return metrics, records


# ── Transcrições compactas p/ o Claude ─────────────────────────────────────

def _role_tag(role: str) -> str:
    if role == "user":
        return "CLIENTE"
    if role in ("consultant", "operator", "agent"):
        return "VENDEDOR"
    return "BOT"


def _transcripts_block(records: List[Dict]) -> str:
    blocks = []
    for i, r in enumerate(records, 1):
        lead = r["lead"]
        cab = (f"### CONVERSA {i} — {_label(lead, r['phone'])}"
               f" | trilha={lead.get('trail') or '?'}"
               f" | temp={lead.get('lead_temperature') or '?'}"
               f" | interesse={lead.get('tipo_interesse') or '?'}"
               f" | atendido={r['atendido']}")
        linhas = [f"[{_role_tag(role)}] {(msg or '')[:_MAX_MSG_CHARS]}" for role, msg in r["msgs"]]
        blocks.append(cab + "\n" + "\n".join(linhas))
    return "\n\n".join(blocks)


_ANALYST_SYSTEM = (
    "Você é o Diretor Comercial da Impacta (escola de tecnologia — treinamentos corporativos, "
    "turmas abertas, turmas fechadas/in company e locação de laboratório). Recebe as conversas de "
    "UM dia da área de Treinamentos (canal corporativo) e produz uma leitura ESTRATÉGICA para o "
    "gestor da área, com foco em ALAVANCAR VENDAS: enxergar oportunidades, objeções, dinheiro na "
    "mesa e onde colocar força. Seja direto, específico e acionável — cite empresas/leads reais das "
    "conversas. NÃO invente dados que não estão nas conversas. Escreva em português do Brasil. "
    "NUNCA cite preços, valores ou descontos específicos (política da empresa)."
)

_ANALYST_INSTRUCTION = (
    "Analise as conversas do dia e responda SOMENTE com um objeto JSON válido (sem markdown, sem "
    "cercas de código), com EXATAMENTE estas chaves:\n"
    "{\n"
    '  "destaque": "1-2 frases: o que mais importou no dia",\n'
    '  "termometro": "leitura geral do dia (aquecido/normal/fraco) e por quê, em 1 frase",\n'
    '  "temas_em_alta": ["treinamentos/assuntos mais procurados hoje"],\n'
    '  "oportunidades": [{"titulo": "...", "detalhe": "empresa/lead + porquê é oportunidade"}],\n'
    '  "objecoes": [{"objecao": "...", "sugestao": "como contornar"}],\n'
    '  "ligar_hoje": [{"quem": "empresa ou nome", "telefone": "se houver", "motivo": "por que priorizar"}],\n'
    '  "risco_perda": ["leads quentes/corporativos prestes a esfriar ou sem retorno — dinheiro na mesa"],\n'
    '  "recomendacoes": ["ações estratégicas para o gestor colocar força amanhã"]\n'
    "}\n"
    "Listas vazias são permitidas. Máx 5 itens por lista. Priorize o que gera receita."
)


async def _analyze(metrics: Dict, records: List[Dict]) -> Dict[str, Any]:
    """Chama o Claude (Sonnet) para a leitura estratégica. Best-effort — nunca quebra o job."""
    if not records:
        return {"destaque": "Sem conversas corporativas no dia.", "termometro": "fraco — sem volume",
                "temas_em_alta": [], "oportunidades": [], "objecoes": [], "ligar_hoje": [],
                "risco_perda": [], "recomendacoes": []}
    from app.services.ai_engine import client
    transcripts = _transcripts_block(records)
    resumo_metricas = (
        f"Dia: {metrics['dia']}\n"
        f"Conversas corporativas: {metrics['total_conversas']} | Novos leads: {metrics['novos_leads']} | "
        f"Turma fechada detectada: {metrics['turma_fechada']}\n"
        f"Temperatura: {metrics['temperatura']}\n"
        f"Atendimento: {metrics['atendimento']}\n"
        f"Interesses: {metrics['temas']}\n"
    )
    prompt = (
        f"MÉTRICAS DO DIA:\n{resumo_metricas}\n\n"
        f"CONVERSAS DO DIA:\n{transcripts}\n\n{_ANALYST_INSTRUCTION}"
    )
    model = "claude-sonnet-4-5-20250929"
    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=3000,
            system=_ANALYST_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            from app.services.ai_engine import _track_tokens
            asyncio.ensure_future(_track_tokens("sales_digest", "analyze_day", resp.usage, model, ""))
        except Exception:
            pass
        raw = resp.content[0].text.strip()
        # remove eventuais cercas de código
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw[raw.find("{"):]
        raw = raw[raw.find("{"): raw.rfind("}") + 1]
        return json.loads(raw)
    except Exception as e:
        logger.error(f"[SalesDigest] Falha na análise via IA: {e}")
        return {"destaque": "Não foi possível gerar a análise estratégica automática.",
                "termometro": "", "temas_em_alta": [], "oportunidades": [], "objecoes": [],
                "ligar_hoje": [], "risco_perda": [], "recomendacoes": []}


async def build_digest(day: Optional[str] = None) -> Dict[str, Any]:
    """Monta o pacote completo do dia: {dia, metrics, analysis, records_count}."""
    day = day or _yesterday_brt()
    metrics, records = await _gather(day)
    analysis = await _analyze(metrics, records)
    return {"dia": day, "metrics": metrics, "analysis": analysis, "records": len(records)}


# ── Job agendado ────────────────────────────────────────────────────────────

async def run_daily_digest(day: Optional[str] = None) -> bool:
    """Entry-point do scheduler: monta e envia o resumo estratégico de ontem."""
    from app.core.database import get_bot_config
    from app.services.email_service import send_sales_digest
    try:
        cfg = await get_bot_config()
        digest = await build_digest(day)
        ok = await send_sales_digest(digest, cfg)
        logger.info(f"[SalesDigest] Resumo {digest['dia']} — "
                    f"{digest['metrics']['total_conversas']} conversas — enviado={ok}")
        return ok
    except Exception as e:
        logger.error(f"[SalesDigest] Erro no job diário: {e}", exc_info=True)
        return False
