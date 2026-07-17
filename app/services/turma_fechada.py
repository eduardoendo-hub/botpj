"""Alerta de Turma Fechada (corporativo/in company) — Bot SDR PJ.

Quando um lead é identificado como turma fechada / corporativo (trilha B),
dispara UM alerta por email para um grupo configurável (turma_fechada_recipients).
Monitora tanto conversas do bot quanto do vendedor: em tempo real no _run_lead_analysis
e num scan periódico sobre as conversas ingeridas (que incluem consultor).
"""

import asyncio
import logging
import sqlite3

from app.core.database import DB_PATH

logger = logging.getLogger(__name__)

# palavras que sugerem contexto B2B (pré-filtro barato antes de classificar via IA)
_B2B_HINTS = (
    "empresa", "equipe", "colaborador", "funcion", "in company", "in-company",
    "turma fechada", "turma exclusiva", "exclusiva", "corporativ", "para minha", "somos",
    "pessoas", "participantes", "rh", "treinar", "capacita",
)

# Áreas (contact.channel_label) que contam como corporativo/turma fechada.
# O broker do RD recebe TUDO (Faculdade, Cobrança, Sites...), mas cada mensagem traz
# de onde veio — então a varredura de logs filtra só o canal corporativo aqui.
_CORP_CHANNELS = {"treinamentos", "site 1", "site 2"}


def is_turma_fechada(lead: dict) -> bool:
    """True somente se for turma fechada / corporativa DE VERDADE.

    Exige grupo real (2+) OU empresa nomeada OU menção explícita de 'in company /
    fechada / exclusiva'. EXCLUI leads individuais (trilha A ou 1 participante) —
    a IA às vezes marca tipo_interesse='curso_corporativo' por engano, então esse
    campo sozinho NÃO basta.
    """
    ti = (lead.get("tipo_interesse") or "").lower()
    fmt = (lead.get("formato") or "").lower()
    trail = (lead.get("trail") or "").strip().upper()
    company = (lead.get("company") or lead.get("empresa") or "").strip()
    m = re.search(r"\d+", str(lead.get("qtd_participantes") or lead.get("qtd_colaboradores") or ""))
    qtd = int(m.group(0)) if m else 0

    # ── Exclusões fortes de individual ──
    if trail == "A":       # A = individual / turma aberta
        return False
    if qtd == 1:           # 1 participante não é turma fechada
        return False

    explicito = (any(k in ti for k in ("fechada", "in company", "in_company", "incompany"))
                 or any(k in fmt for k in ("in company", "in_company", "incompany", "fechada", "exclusiva")))
    if explicito:
        return True

    # sem menção explícita: precisa de grupo (2+) OU empresa, JUNTO com sinal corporativo
    corporativo = (trail == "B") or ("corporativ" in ti)
    if corporativo and (qtd >= 2 or bool(company)):
        return True
    return False


# ── Dedup (uma vez por lead) ───────────────────────────────────────────────
def _init_sync():
    db = sqlite3.connect(DB_PATH)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS tf_alerts(phone_number TEXT PRIMARY KEY, alerted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        db.commit()
    finally:
        db.close()


def _already_sync(phone: str) -> bool:
    db = sqlite3.connect(DB_PATH)
    try:
        return bool(db.execute("SELECT 1 FROM tf_alerts WHERE phone_number=?", (phone,)).fetchone())
    finally:
        db.close()


def _mark_sync(phone: str):
    db = sqlite3.connect(DB_PATH)
    try:
        db.execute("INSERT OR IGNORE INTO tf_alerts(phone_number) VALUES (?)", (phone,))
        db.commit()
    finally:
        db.close()


async def _contexto_conversa(phone_number: str):
    """Retorna (atendido_por, resumo) — best-effort, não quebra o alerta se falhar."""
    atendido, resumo = "", ""
    try:
        from app.core.database import get_conversation_history
        history = await get_conversation_history(phone_number, limit=30)
        roles = {(m.get("role") or "") for m in history}
        tem_bot = "assistant" in roles
        tem_vend = bool({"consultant", "operator", "agent"} & roles)
        atendido = "Bot + Vendedor" if (tem_bot and tem_vend) else ("Vendedor" if tem_vend else ("Bot" if tem_bot else ""))
        try:
            from app.services.ai_engine import generate_conversation_summary
            resumo = await generate_conversation_summary(phone_number, history) or ""
        except Exception:
            resumo = ""
    except Exception as e:
        logger.error(f"[TurmaFechada] contexto da conversa falhou: {e}")
    return atendido, resumo


async def maybe_alert(phone_number: str, lead: dict = None) -> bool:
    """Dispara o alerta se for turma fechada e ainda não alertado. Idempotente."""
    from app.core.database import get_lead_by_phone, get_bot_config
    from app.services.email_service import send_turma_fechada_alert

    await asyncio.to_thread(_init_sync)
    if not lead:
        lead = await get_lead_by_phone(phone_number) or {}
    if not is_turma_fechada(lead):
        return False
    if await asyncio.to_thread(_already_sync, phone_number):
        return False

    cfg = await get_bot_config()

    # "Atendido por" + resumo da conversa (best-effort)
    atendido, resumo = await _contexto_conversa(phone_number)

    # a chave pode ser um telefone (WhatsApp) ou um contact_id (webchat/site)
    is_real_phone = bool(re.match(r"^\+?\d{10,15}$", (phone_number or "").strip()))
    origem = "Turma Fechada / Corporativo" if is_real_phone else "Turma Fechada / Webchat (Site)"

    payload = {
        "contact_name":      lead.get("contact_name") or lead.get("nome") or "",
        "phone_number":      phone_number if is_real_phone else "",
        "email":             lead.get("email") or "",
        "company":           lead.get("company") or lead.get("empresa") or "",
        "job_title":         lead.get("job_title") or "",
        "training_interest": lead.get("training_interest") or lead.get("tema_interesse") or lead.get("servico") or "",
        "qtd_participantes": lead.get("qtd_participantes") or lead.get("qtd_colaboradores") or "",
        "formato":           lead.get("formato") or "",
        "cidade":            lead.get("cidade") or "",
        "prazo":             lead.get("prazo") or "",
        "urgencia":          lead.get("urgencia") or "",
        "objetivo_negocio":  lead.get("objetivo_negocio") or "",
        "lead_temperature":  lead.get("lead_temperature") or "",
        "score":             lead.get("score") or "",
        "atendido_por":      atendido,
        "resumo":            resumo,
        "origem":            origem,
    }
    ok = await send_turma_fechada_alert(payload, cfg)
    if ok:
        await asyncio.to_thread(_mark_sync, phone_number)
        logger.info(f"[TurmaFechada] Alerta disparado para {phone_number} (empresa={payload['company'] or '—'}).")
    return ok


# ── Scan periódico (cobre conversas do vendedor) ───────────────────────────
def _recent_b2b_phones_sync(days: int, limit: int):
    """Telefones com atividade recente na tabela de conversas, com pista B2B."""
    db = sqlite3.connect(DB_PATH)
    try:
        rows = db.execute(
            "SELECT DISTINCT c.phone_number FROM conversations c "
            "WHERE c.role IN ('consultant','operator','agent','user') "
            "AND c.created_at >= date('now', ?) "
            "AND c.phone_number NOT IN (SELECT phone_number FROM tf_alerts) LIMIT ?",
            (f"-{days} day", limit * 4),
        ).fetchall()
        out = []
        for (ph,) in rows:
            txt = " ".join(
                (m[0] or "").lower()
                for m in db.execute("SELECT message FROM conversations WHERE phone_number=? LIMIT 40", (ph,)).fetchall()
            )
            if any(h in txt for h in _B2B_HINTS):
                out.append((ph, ""))  # contact_id vem do lead depois
            if len(out) >= limit:
                break
        return out
    finally:
        db.close()


def _b2b_from_logs_sync(days: int, limit: int):
    """Candidatos (phone, contact_id) do webhook_logs CRU — cobre TODAS as conversas
    que passaram no monitor, mesmo as que o bot nunca atuou / não viraram lead."""
    import json
    db = sqlite3.connect(DB_PATH)
    try:
        alerted = {r[0] for r in db.execute("SELECT phone_number FROM tf_alerts").fetchall()}
        rows = db.execute(
            "SELECT raw_payload FROM webhook_logs WHERE created_at >= datetime('now', ?) "
            "ORDER BY id DESC LIMIT 4000", (f"-{days} day",),
        ).fetchall()
        seen = {}
        for (raw,) in rows:
            if not raw:
                continue
            try:
                body = json.loads(raw)
            except Exception:
                continue
            data = body.get("data", body) if isinstance(body, dict) else {}
            contact = data.get("contact", {}) if isinstance(data, dict) else {}
            content = data.get("content", {}) if isinstance(data, dict) else {}
            phone = (contact.get("phone") or "") if isinstance(contact, dict) else ""
            cid = str(contact.get("id") or contact.get("_id") or "") if isinstance(contact, dict) else ""
            label = (contact.get("channel_label") or "").strip().lower() if isinstance(contact, dict) else ""
            msg = (content.get("message") or "") if isinstance(content, dict) else ""
            # chave = telefone (WhatsApp) ou o contact_id (webchat/site, que não tem phone)
            key = phone or cid
            if not key or key in alerted or key in seen:
                continue
            # só as áreas corporativas (ignora Faculdade/Cobrança) — evita ruído e custo de IA
            if _CORP_CHANNELS and label not in _CORP_CHANNELS:
                continue
            if any(h in msg.lower() for h in _B2B_HINTS):
                seen[key] = cid
            if len(seen) >= limit:
                break
        return list(seen.items())
    finally:
        db.close()


def _channel_of_sync(phone: str) -> str:
    """channel_label mais recente desse telefone no webhook_logs (área de onde veio)."""
    import json
    db = sqlite3.connect(DB_PATH)
    try:
        for (raw,) in db.execute(
            "SELECT raw_payload FROM webhook_logs WHERE phone_number=? ORDER BY id DESC LIMIT 3", (phone,)
        ):
            if not raw:
                continue
            try:
                b = json.loads(raw)
            except Exception:
                continue
            ct = (b.get("data", b).get("contact", {}) if isinstance(b, dict) else {})
            lbl = (ct.get("channel_label") or "").strip().lower() if isinstance(ct, dict) else ""
            if lbl:
                return lbl
        return ""
    finally:
        db.close()


async def scan_recent(days: int = 2, max_leads: int = 30) -> int:
    """Classifica conversas B2B recentes e alerta as turmas fechadas.

    Fontes: (a) tabela de conversas (leads engajados/ingeridos) e (b) webhook_logs CRU
    — que cobre TODAS as conversas que passaram no monitor, mesmo as que o bot nunca
    atuou ou que nem viraram lead. Para candidatos que só existem no log, ingere a
    conversa (JWK) antes de classificar.
    """
    from app.services.ai_engine import analyze_and_update_lead
    from app.core.database import get_conversation_history, upsert_lead, get_lead_by_phone
    from app.services.ingestion import _sync_one

    await asyncio.to_thread(_init_sync)
    # une candidatos das duas fontes (phone -> contact_id, preferindo o do log)
    candidates = {}
    for ph, cid in await asyncio.to_thread(_recent_b2b_phones_sync, days, max_leads):
        candidates.setdefault(ph, cid)
    for ph, cid in await asyncio.to_thread(_b2b_from_logs_sync, days, max_leads):
        if cid or ph not in candidates:
            candidates[ph] = candidates.get(ph) or cid

    alerted = 0
    for phone, cid in list(candidates.items())[:max_leads]:
        try:
            # filtro de área: se o telefone tem canal conhecido e NÃO é corporativo
            # (ex: Faculdade/Cobrança), pula — evita falso positivo e custo de IA.
            if phone and phone.replace("+", "").isdigit():
                ch = await asyncio.to_thread(_channel_of_sync, phone)
                if ch and _CORP_CHANNELS and ch not in _CORP_CHANNELS:
                    continue
            # candidato só do log (sem histórico local) → ingere a conversa primeiro
            if cid:
                try:
                    await _sync_one(phone, cid, "", 50)
                    await upsert_lead(phone, notes=f"tallos_contact_id:{cid}")
                except Exception:
                    pass
            history = await get_conversation_history(phone, limit=30)
            if not history:
                continue
            extracted = await analyze_and_update_lead(phone, history) or {}
            upd = {k: extracted[k] for k in ("tipo_interesse", "trail", "formato", "qtd_participantes", "empresa")
                   if extracted.get(k)}
            if upd.get("empresa"):
                upd["company"] = upd.pop("empresa")
            if upd:
                await upsert_lead(phone, **upd)
            lead = await get_lead_by_phone(phone) or {}
            if await maybe_alert(phone, lead):
                alerted += 1
        except Exception as e:
            logger.error(f"[TurmaFechada] Erro no scan de {phone}: {e}")
        await asyncio.sleep(0.4)
    logger.info(f"[TurmaFechada] Scan: {alerted} alerta(s) de {len(candidates)} conversa(s) B2B (conversas + logs).")
    return alerted
