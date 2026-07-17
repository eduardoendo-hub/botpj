"""Copiloto do Gestor — resumo estratégico diário da área de Treinamentos (PF + PJ).

Todo dia de manhã, olha as conversas do dia anterior de TODA a área de Treinamentos
(canais Treinamentos + Site — inclui PF/individual e PJ/corporativo), CRUZA com o funil
do CRM do RD (etapa + insights já cacheados nos leads) e monta um email para o gestor com:
  • números com significado, SEPARADOS por PF e PJ (volume, quentes, novos, turma fechada);
  • leitura do FUNIL (etapas, negociações, perdidos + motivo);
  • leitura estratégica via IA por segmento (oportunidades, quem ligar hoje);
  • temas em alta, objeções, risco de perda e recomendações.

Reusa scheduler + email (Gmail) + Claude já existentes. NÃO inclui Faculdade/Cobrança.
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
_MAX_LEADS_ANALYSIS = 45      # nº máx de conversas enviadas para análise
_MAX_MSGS_PER_LEAD = 16       # mensagens por conversa
_MAX_MSG_CHARS = 200          # chars por mensagem


def _yesterday_brt() -> str:
    """Data (YYYY-MM-DD) de ONTEM no fuso BRT."""
    return (datetime.now(BRT) - timedelta(days=1)).strftime("%Y-%m-%d")


def _is_created_on(lead: Dict, day: str) -> bool:
    """True se o lead foi criado no dia informado (BRT)."""
    ca = lead.get("created_at") or ""
    if not ca:
        return False
    try:
        dt = datetime.fromisoformat(ca.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(BRT).strftime("%Y-%m-%d") == day
    except Exception:
        return False


# ── Segmentação PF × PJ ─────────────────────────────────────────────────────

def _segment(lead: Dict) -> str:
    """Classifica o lead como 'PJ' (corporativo) ou 'PF' (individual)."""
    trail = (lead.get("trail") or "").strip().upper()
    ti = (lead.get("tipo_interesse") or "").lower()
    company = (lead.get("company") or lead.get("empresa") or "").strip()
    if trail == "B" or company or any(k in ti for k in ("fechada", "corporativ")) or is_turma_fechada(lead):
        return "PJ"
    return "PF"


# ── Coleta de dados (síncrona, sqlite direto) ──────────────────────────────

def _corporate_phones_sync(day: str) -> List[str]:
    """Telefones com atividade no dia (BRT) da área de Treinamentos (canais Treinamentos/Site)."""
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
    return [p for p in phones if _channel_of_sync(p) in _CORP_CHANNELS]


def _messages_sync(phone: str) -> List[Tuple[str, str]]:
    db = sqlite3.connect(DB_PATH)
    try:
        rows = db.execute(
            "SELECT role, message FROM conversations WHERE phone_number=? ORDER BY id DESC LIMIT ?",
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


def _label(lead: Dict, phone: str) -> str:
    return (lead.get("company") or lead.get("contact_name") or phone or "Lead").strip()


# ── Métricas determinísticas (split PF/PJ + funil) ─────────────────────────

def _blank_seg() -> Dict[str, int]:
    return {"conversas": 0, "quentes": 0, "novos": 0, "turma_fechada": 0}


def _compute_metrics(day: str, records: List[Dict]) -> Dict[str, Any]:
    seg = {"PF": _blank_seg(), "PJ": _blank_seg()}
    funil: Dict[str, int] = {}
    perdidos: List[str] = []
    temas: Dict[str, int] = {}
    atend: Dict[str, int] = {}
    for r in records:
        lead = r["lead"]
        s = r["seg"]
        seg[s]["conversas"] += 1
        if (lead.get("lead_temperature") or "").lower() == "quente":
            seg[s]["quentes"] += 1
        if _is_created_on(lead, day):
            seg[s]["novos"] += 1
        if s == "PJ" and is_turma_fechada(lead):
            seg["PJ"]["turma_fechada"] += 1
        etapa = (lead.get("crm_etapa_cache") or "").strip()
        if etapa:
            funil[etapa] = funil.get(etapa, 0) + 1
            if etapa.lower().startswith("perdido"):
                perdidos.append(f"{_label(lead, r['phone'])} — {etapa}")
        ti = (lead.get("tipo_interesse") or "").strip()
        if ti:
            temas[ti] = temas.get(ti, 0) + 1
        atend[r["atendido"]] = atend.get(r["atendido"], 0) + 1
    return {
        "dia": day,
        "total_conversas": len(records),
        "pf": seg["PF"],
        "pj": seg["PJ"],
        "funil": dict(sorted(funil.items(), key=lambda kv: kv[1], reverse=True)),
        "perdidos": perdidos[:8],
        "atendimento": atend,
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
        records.append({"phone": ph, "lead": lead, "msgs": msgs,
                        "atendido": atendido, "seg": _segment(lead)})
    metrics = _compute_metrics(day, records)
    return metrics, records


# ── Transcrições compactas p/ o Claude (com funil + insight do CRM) ─────────

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
        etapa = (lead.get("crm_etapa_cache") or "").strip()
        insight = (lead.get("crm_insights") or "").strip()
        cab = (f"### CONVERSA {i} [{r['seg']}] — {_label(lead, r['phone'])}"
               f" | trilha={lead.get('trail') or '?'}"
               f" | temp={lead.get('lead_temperature') or '?'}"
               f" | interesse={lead.get('tipo_interesse') or '?'}"
               f" | funil_CRM={etapa or '—'}"
               f" | atendido={r['atendido']}")
        linhas = [f"[{_role_tag(role)}] {(msg or '')[:_MAX_MSG_CHARS]}" for role, msg in r["msgs"]]
        corpo = cab + "\n" + "\n".join(linhas)
        if insight:
            corpo += f"\n(insight CRM: {insight[:280]})"
        blocks.append(corpo)
    return "\n\n".join(blocks)


_ANALYST_SYSTEM = (
    "Você é o Diretor Comercial da Impacta (escola de tecnologia). A área de Treinamentos vende para "
    "DOIS públicos: PF (pessoa física — indivíduos comprando cursos/turmas abertas) e PJ (empresas — "
    "treinamento corporativo, turma fechada/in company, locação). Você recebe as conversas de UM dia "
    "dessa área, JÁ CRUZADAS com a etapa do funil do CRM, e produz uma leitura ESTRATÉGICA para o gestor, "
    "SEPARANDO PF e PJ, com foco em ALAVANCAR VENDAS: oportunidades, dinheiro na mesa, onde colocar força. "
    "Seja direto, específico, cite empresas/leads reais e a etapa do funil quando útil. NÃO invente dados. "
    "Português do Brasil. NUNCA cite preços/valores/descontos ao CLIENTE, mas você PODE relatar ao gestor "
    "o que foi praticado nas conversas (é interno)."
)

_ANALYST_INSTRUCTION = (
    "Analise o dia e responda SOMENTE com um objeto JSON válido (sem markdown/cercas), com EXATAMENTE:\n"
    "{\n"
    '  "destaque": "1-2 frases: o que mais importou no dia (cite PF e/ou PJ)",\n'
    '  "termometro": "leitura geral do dia (aquecido/normal/fraco) e por quê, 1 frase",\n'
    '  "funil_leitura": "1-2 frases lendo o pipeline: negociações, gargalos, perdidos e motivos",\n'
    '  "pf": {"leitura": "1-2 frases sobre o segmento PF", "oportunidades": ["..."], "ligar_hoje": [{"quem":"...","motivo":"..."}]},\n'
    '  "pj": {"leitura": "1-2 frases sobre o segmento PJ", "oportunidades": ["..."], "ligar_hoje": [{"quem":"...","motivo":"..."}]},\n'
    '  "temas_em_alta": ["treinamentos/assuntos mais procurados"],\n'
    '  "objecoes": [{"objecao": "...", "sugestao": "como contornar"}],\n'
    '  "risco_perda": ["leads quentes/corporativos prestes a esfriar ou sem retorno"],\n'
    '  "recomendacoes": ["ações estratégicas para o gestor amanhã"]\n'
    "}\n"
    "Listas vazias são permitidas. Máx 5 itens por lista. Priorize o que gera receita."
)


def _empty_analysis(msg: str = "") -> Dict[str, Any]:
    return {"destaque": msg or "Sem conversas de Treinamentos no dia.", "termometro": "", "funil_leitura": "",
            "pf": {"leitura": "", "oportunidades": [], "ligar_hoje": []},
            "pj": {"leitura": "", "oportunidades": [], "ligar_hoje": []},
            "temas_em_alta": [], "objecoes": [], "risco_perda": [], "recomendacoes": []}


async def _analyze(metrics: Dict, records: List[Dict]) -> Dict[str, Any]:
    """Chama o Claude (Sonnet) para a leitura estratégica. Best-effort — nunca quebra o job."""
    if not records:
        return _empty_analysis()
    from app.services.ai_engine import client
    transcripts = _transcripts_block(records)
    resumo_metricas = (
        f"Dia: {metrics['dia']} | Conversas: {metrics['total_conversas']}\n"
        f"PF: {metrics['pf']}\nPJ: {metrics['pj']}\n"
        f"Funil (etapa: qtd): {metrics['funil']}\n"
        f"Perdidos: {metrics['perdidos']}\n"
        f"Atendimento: {metrics['atendimento']}\nInteresses: {metrics['temas']}\n"
    )
    prompt = (f"MÉTRICAS DO DIA:\n{resumo_metricas}\n\nCONVERSAS DO DIA (cruzadas com funil):\n"
              f"{transcripts}\n\n{_ANALYST_INSTRUCTION}")
    model = "claude-sonnet-4-5-20250929"
    try:
        resp = await client.messages.create(
            model=model, max_tokens=3500, system=_ANALYST_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            from app.services.ai_engine import _track_tokens
            asyncio.ensure_future(_track_tokens("sales_digest", "analyze_day", resp.usage, model, ""))
        except Exception:
            pass
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw[raw.find("{"):]
        raw = raw[raw.find("{"): raw.rfind("}") + 1]
        data = json.loads(raw)
        # garante as chaves de segmento
        for seg in ("pf", "pj"):
            if not isinstance(data.get(seg), dict):
                data[seg] = {"leitura": "", "oportunidades": [], "ligar_hoje": []}
        return data
    except Exception as e:
        logger.error(f"[SalesDigest] Falha na análise via IA: {e}")
        return _empty_analysis("Não foi possível gerar a análise estratégica automática.")


async def build_digest(day: Optional[str] = None) -> Dict[str, Any]:
    """Monta o pacote completo do dia: {dia, metrics, analysis, records}."""
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
                    f"{digest['metrics']['total_conversas']} conversas "
                    f"(PF {digest['metrics']['pf']['conversas']} / PJ {digest['metrics']['pj']['conversas']}) "
                    f"— enviado={ok}")
        return ok
    except Exception as e:
        logger.error(f"[SalesDigest] Erro no job diário: {e}", exc_info=True)
        return False
