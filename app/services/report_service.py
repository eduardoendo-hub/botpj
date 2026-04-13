"""
Serviço de relatório diário — Bot SDR PJ.

Monta a mensagem WhatsApp para a diretoria com:
  • Leads que entraram hoje
  • Leads da semana em andamento
  • ⚠️ Pontos de atenção (max 3 leads priorizados por score de urgência)

Envio via ChatPro (placeholder — preencher com token + instância quando disponível).
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from app.core.database import get_db

logger = logging.getLogger(__name__)

# ── Timezone BRT (UTC-3) ──────────────────────────────────────────────────────
BRT = timezone(timedelta(hours=-3))


def _now_brt() -> datetime:
    return datetime.now(BRT)


def _to_brt(ts_str: str) -> Optional[datetime]:
    """Converte string ISO UTC do SQLite para datetime BRT."""
    if not ts_str:
        return None
    try:
        # SQLite guarda sem 'Z' mas é UTC
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(BRT)
    except Exception:
        return None


def _hours_since(ts_str: str) -> float:
    """Horas desde updated_at até agora (BRT)."""
    dt = _to_brt(ts_str)
    if dt is None:
        return 999.0
    return (_now_brt() - dt).total_seconds() / 3600


# ── Helpers de formatação ─────────────────────────────────────────────────────

def _responsible_icon(lead: Dict) -> str:
    stage = (lead.get("stage") or "").lower()
    status = (lead.get("status_conversa") or "").lower()
    trail  = (lead.get("trail") or "").lower()

    if any(k in stage for k in ["consultor", "proposta", "humano_ativo"]):
        return "👤 Consultor"
    if any(k in stage  for k in ["transferido", "escalado", "fila"]) or \
       any(k in status for k in ["transferido", "escalado"]) or \
       any(k in trail  for k in ["e"]):
        return "↗️ Transição para humano"
    return "🤖 Bot"


def _next_step(lead: Dict) -> str:
    pp = (lead.get("proximo_passo") or "").strip()
    if pp:
        return pp

    stage  = (lead.get("stage") or "").lower()
    status = (lead.get("status_conversa") or "").lower()
    temp   = (lead.get("lead_temperature") or "").lower()

    if "consultor" in stage:
        return "Envio de proposta"
    if "transferido" in stage or "escalado" in status:
        return "Consultor assumir"
    if "aguardando" in status:
        return "Retorno do interessado"
    if temp == "quente":
        return "Validar e enviar proposta"
    if temp == "morno":
        return "Qualificar interesse"
    return "Acompanhar lead"


def _interest_label(lead: Dict) -> str:
    """Combina tipo_interesse + formato em label curto."""
    tipo    = (lead.get("tipo_interesse") or lead.get("training_interest") or "").strip()
    formato = (lead.get("formato") or "").strip()
    tema    = (lead.get("tema_interesse") or "").strip()

    parts = []
    if tipo:
        parts.append(tipo.capitalize())
    if formato:
        parts.append(formato.capitalize())
    if not parts and tema:
        parts.append(tema[:30])
    return " / ".join(parts) if parts else "Interesse geral"


def _company_label(lead: Dict) -> str:
    c = (lead.get("company") or "").strip()
    return c if c else "Empresa n/i"


def _name_label(lead: Dict) -> str:
    n = (lead.get("contact_name") or "").strip()
    return n if n else "Lead"


# ── Lógica de atenção (score de urgência) ────────────────────────────────────

def _urgency_score(lead: Dict) -> float:
    """
    Pontuação de urgência (maior = mais urgente).

    Critérios:
      +40  Quente sem movimentação há > 4h
      +30  Transferido há > 6h sem assumir (aguardando humano)
      +20  Morno parado há > 24h (pode esfriar)
      +15  Muitos participantes (>= 10) ainda em qualificação
      +10  Tem prazo informado (urgência declarada)
      +5   Tem empresa identificada (lead mais qualificado)
      -10  Já com consultor ativo (não precisa de alerta)
    """
    score  = 0.0
    temp   = (lead.get("lead_temperature") or "").lower()
    stage  = (lead.get("stage") or "").lower()
    status = (lead.get("status_conversa") or "").lower()
    qtd    = lead.get("qtd_participantes") or ""
    prazo  = (lead.get("prazo") or "").strip()
    hours  = _hours_since(lead.get("updated_at") or lead.get("created_at") or "")

    if temp == "quente" and hours > 4:
        score += 40
    if ("transferido" in stage or "escalado" in status) and hours > 6:
        score += 30
    if temp == "morno" and hours > 24:
        score += 20
    try:
        if int("".join(filter(str.isdigit, qtd))) >= 10:
            score += 15
    except Exception:
        pass
    if prazo:
        score += 10
    if (lead.get("company") or "").strip():
        score += 5
    if "consultor" in stage and hours < 8:
        score -= 10

    return score


def _attention_label(lead: Dict) -> str:
    """Gera o texto do ponto de atenção com motivo."""
    temp   = (lead.get("lead_temperature") or "").lower()
    stage  = (lead.get("stage") or "").lower()
    status = (lead.get("status_conversa") or "").lower()
    hours  = _hours_since(lead.get("updated_at") or lead.get("created_at") or "")
    h_str  = f"{int(hours)}h"

    if ("transferido" in stage or "escalado" in status) and hours > 6:
        return f"aguardando consultor há {h_str}"
    if temp == "quente" and hours > 4:
        return f"lead quente parado há {h_str}"
    if temp == "morno" and hours > 24:
        return f"lead esfriando (sem contato há {h_str})"
    return f"parado há {h_str}"


# ── Busca de dados no banco ───────────────────────────────────────────────────

async def _fetch_leads_today() -> List[Dict]:
    """Leads criados hoje (BRT)."""
    today_str = _now_brt().strftime("%Y-%m-%d")
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT * FROM leads
            WHERE date(datetime(created_at, '-3 hours')) = ?
            ORDER BY created_at DESC
            """,
            (today_str,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def _fetch_leads_week_active() -> List[Dict]:
    """
    Leads criados nos últimos 7 dias que ainda estão em andamento
    (exclui os de hoje — já aparecem na 1ª seção).
    """
    now_brt   = _now_brt()
    today_str = now_brt.strftime("%Y-%m-%d")
    week_ago  = (now_brt - timedelta(days=7)).strftime("%Y-%m-%d")

    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT * FROM leads
            WHERE date(datetime(created_at, '-3 hours')) >= ?
              AND date(datetime(created_at, '-3 hours')) <  ?
              AND (stage NOT IN ('fechado', 'perdido', 'cancelado') OR stage IS NULL)
            ORDER BY created_at DESC
            """,
            (week_ago, today_str)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


# ── Montagem da mensagem ──────────────────────────────────────────────────────

def _fmt_lead_today(lead: Dict) -> str:
    dt     = _to_brt(lead.get("created_at") or "")
    hora   = dt.strftime("%H:%M") if dt else "--:--"
    status = lead.get("status_conversa") or lead.get("stage") or "Novo"
    resp   = _responsible_icon(lead)
    pp     = _next_step(lead)
    return (
        f"{hora} {_company_label(lead)} - {_name_label(lead)} - "
        f"{_interest_label(lead)} - {status} - {resp} - {pp}"
    )


def _fmt_lead_week(lead: Dict) -> str:
    dt   = _to_brt(lead.get("created_at") or "")
    data = dt.strftime("%d/%m") if dt else "--/--"
    temp = lead.get("lead_temperature") or "N/i"
    status = lead.get("status_conversa") or lead.get("stage") or "Em andamento"
    resp   = _responsible_icon(lead)
    pp     = _next_step(lead)
    return (
        f"{data} {_company_label(lead)} - {_name_label(lead)} - "
        f"{_interest_label(lead)} - {temp.capitalize()} - {status} - {resp} - {pp}"
    )


def _build_attention_section(today: List[Dict], week: List[Dict]) -> str:
    """Seleciona os top 2-3 leads mais urgentes com base no score."""
    all_leads = today + week
    if not all_leads:
        return "✅ Sem pontos de atenção no momento."

    scored = sorted(all_leads, key=_urgency_score, reverse=True)
    top    = [l for l in scored if _urgency_score(l) > 10][:3]

    if not top:
        return "✅ Todos os leads estão dentro do fluxo esperado."

    lines = []
    for lead in top:
        lines.append(
            f"• {_company_label(lead)} / {_name_label(lead)} — {_attention_label(lead)}"
        )
    return "\n".join(lines)


_MAX_VAR_LEN = 900  # Meta limita parâmetros de template a ~1024 chars; usamos 900 com margem


def _truncate_var(text: str, max_len: int = _MAX_VAR_LEN) -> str:
    """Trunca o texto para caber no limite de parâmetros da Meta API."""
    if len(text) <= max_len:
        return text
    # Corta na última linha completa que cabe
    truncated = text[:max_len]
    last_nl = truncated.rfind("\n")
    if last_nl > max_len // 2:
        truncated = truncated[:last_nl]
    return truncated + "\n_(lista truncada — veja o Radar completo no admin)_"


async def build_daily_report() -> Dict[str, str]:
    """
    Monta as 3 seções do relatório diário separadamente.

    Retorna dict com:
      sec1  — leads de hoje       → variável {{1}} do template
      sec2  — leads da semana     → variável {{2}} do template
      sec3  — pontos de atenção   → variável {{3}} do template
      full  — texto completo (preview no admin)
    """
    today_leads = await _fetch_leads_today()
    week_leads  = await _fetch_leads_week_active()
    now_brt     = _now_brt()

    # ── {{1}}: Leads hoje  (sem \n — Meta rejeita newlines em parâmetros de template)
    if today_leads:
        sec1 = _truncate_var(" | ".join(_fmt_lead_today(l) for l in today_leads))
    else:
        sec1 = "Nenhum lead registrado hoje ainda."

    # ── {{2}}: Leads da semana
    if week_leads:
        sec2 = _truncate_var(" | ".join(_fmt_lead_week(l) for l in week_leads))
    else:
        sec2 = "Nenhum lead ativo da semana."

    # ── {{3}}: Pontos de atenção (seção curta, não precisa truncar)
    sec3 = _build_attention_section(today_leads, week_leads)

    # Preview completo para o admin (simula como aparecerá no WhatsApp)
    full = (
        f"📡 *Radar PJ | Diretoria*\n"
        f"_{now_brt.strftime('%d/%m/%Y às %H:%M')}_\n"
        f"\n"
        f"*Leads que entraram hoje*\n{sec1}\n"
        f"\n"
        f"*Leads da semana em andamento*\n{sec2}\n"
        f"\n"
        f"⚠️ *Pontos de atenção:*\n{sec3}\n"
        f"\n"
        f"_🤖 Bot | ↗️ Transferido para humano | 👤 Consultor_"
    )

    return {"sec1": sec1, "sec2": sec2, "sec3": sec3, "full": full}


# ── Envio via ChatPro WABA (número oficial) ───────────────────────────────────

_SPARKS_BASE = "https://sparks.chatpro.com.br"


async def list_waba_templates(
    chatpro_token: str,
    instance_id: str,
) -> List[Dict]:
    """
    Lista os templates WABA aprovados da instância.
    Útil para descobrir o nome exato após criar no Meta Business Manager.
    POST /waba/getTemplates
    """
    import httpx
    headers = {"instance-token": chatpro_token, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_SPARKS_BASE}/waba/getTemplates",
                json={"instanceId": instance_id},
                headers=headers,
            )
            data = resp.json()
            # Retorna lista de templates ou extrai de dentro do payload
            if isinstance(data, list):
                return data
            return data.get("templates") or data.get("data") or []
    except Exception as e:
        logger.error(f"[REPORT] Erro ao listar templates: {e}")
        return []


async def send_report_whatsapp(
    report: Dict[str, str],
    recipients: List[str],
    chatpro_url: str,        # não utilizado (mantido para compatibilidade)
    chatpro_token: str,
    instance_id: str = "chatpro-71f6d6f880",
    template_name: str = "radarpj",
    language_code: str = "pt_BR",
) -> Dict[str, Any]:
    """
    Envia o relatório via template WABA (número oficial WhatsApp Business).

    Usa POST /waba/sendTemplate com schedulingMessage=true — funciona
    fora da janela de 24h sem precisar de sessão ativa.

    Template 'radarpj' com 3 variáveis no corpo:
      {{1}} → leads de hoje
      {{2}} → leads da semana
      {{3}} → pontos de atenção
    """
    import httpx

    if not template_name:
        return {"__error": {"ok": False, "body": "Nome do template não configurado."}}

    results: Dict[str, Any] = {}
    headers = {"instance-token": chatpro_token, "Content-Type": "application/json"}

    # Monta os 3 parâmetros do body do template
    components = [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": report.get("sec1", "")},
                {"type": "text", "text": report.get("sec2", "")},
                {"type": "text", "text": report.get("sec3", "")},
            ],
        }
    ]

    async with httpx.AsyncClient(timeout=20) as client:
        for number in recipients:
            number = number.strip()
            if not number:
                continue
            try:
                payload: Dict[str, Any] = {
                    "instanceId":        instance_id,
                    "number":            number,
                    "name":              template_name,
                    "languageCode":      language_code,
                    "schedulingMessage": True,
                    # Variáveis do corpo do template ({{1}}, {{2}}, {{3}})
                    # Meta exige objetos {"type": "text", "text": "..."}
                    "variables": [
                        {"type": "text", "text": report.get("sec1", "")},
                        {"type": "text", "text": report.get("sec2", "")},
                        {"type": "text", "text": report.get("sec3", "")},
                    ],
                }
                resp = await client.post(
                    f"{_SPARKS_BASE}/waba/sendTemplate",
                    json=payload,
                    headers=headers,
                    timeout=15,
                )
                results[number] = {
                    "status": resp.status_code,
                    "ok":     resp.status_code < 300,
                    "body":   resp.text[:300],
                }
                logger.info(
                    f"[REPORT] Template '{template_name}' enviado para {number}: HTTP {resp.status_code}"
                )
            except Exception as e:
                results[number] = {"status": 0, "ok": False, "body": str(e)}
                logger.error(f"[REPORT] Erro ao enviar para {number}: {e}")

    return results
