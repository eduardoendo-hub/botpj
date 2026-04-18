"""
Endpoints de webhook para o RD Conversas (Tallos) — Bot SDR PJ.

ARQUITETURA:

  POST /webhook/tallos  (monitoramento geral — compartilhado com BOT MBA)
    → Recebe eventos do RD Conversas repassados pelo BOT MBA ou diretamente.
    → Loga e salva contact_id de todos os contatos PJ.
    → Bot só atua se o remetente JÁ ESTÁ cadastrado como lead PJ
      (source_channel = 'tallos_pj', registrado via /webhook/tallospj).

  POST /webhook/tallospj  (cadastro de leads PJ)
    → Chamado pelas automações do RD Conversas configuradas para leads PJ.
    → Registra o lead como PJ no banco (source_channel = 'tallos_pj').
    → A partir deste momento, mensagens deste contato via /webhook/tallos
      serão processadas pelo bot.

FLUXO COMPLETO:
  1. Lead PJ entra no funil no RD Conversas
  2. Automação chama /webhook/tallospj  → lead marcado como PJ
  3. Lead envia mensagem → /webhook/tallos recebe (repassado pelo BOT MBA ou nginx)
  4. Bot verifica: é lead PJ? Sim → processa. Não → só loga.
  5. Bot verifica regras (horário, atendente ativo) → responde se permitido

NOTA SOBRE O WEBHOOK COMPARTILHADO:
  O RD Conversas suporta apenas um webhook. O BOT MBA recebe em /webhook/tallos
  e pode repassar eventos ao BOT SDR PJ. Configure no nginx ou no BOT MBA
  para encaminhar eventos ao BOT SDR PJ na porta 8001.
  URL do BOT MBA para repassar: http://127.0.0.1:8001/webhook/tallos
"""

import re
import json
import time
import logging
from fastapi import APIRouter, Request, BackgroundTasks, Header
from typing import Optional

from app.services.bot_controller import handle_incoming_message, handle_agent_message, is_bot_active_now
from app.services.tallos import (
    tallos_service,
    extract_phone_from_payload,
    extract_message_from_payload,
    extract_name_from_payload,
    extract_contact_id_from_payload,
    is_agent_message,
)
from app.core.config import settings
from app.core.database import log_webhook_event, upsert_lead, upsert_bot_session, is_pj_lead, get_lead_by_phone

logger = logging.getLogger(__name__)
router = APIRouter()


async def _save_contact_id_if_missing(phone: str, contact_id: str) -> None:
    """Salva tallos_contact_id nas notes APENAS se o lead ainda não tiver um.
    Evita sobrescrever IDs antigos (que têm histórico) com IDs novos (vazios).
    """
    if not phone or not contact_id:
        return
    lead = await get_lead_by_phone(phone)
    if lead:
        notes = lead.get("notes", "") or ""
        if "tallos_contact_id:" in notes:
            existing_id = ""
            for part in notes.split("|"):
                if "tallos_contact_id:" in part:
                    existing_id = part.split("tallos_contact_id:")[1].strip()
            if existing_id:
                logger.debug(f"[contact_id] Mantendo ID existente {existing_id} (novo ignorado: {contact_id})")
                return
    await upsert_lead(phone, notes=f"tallos_contact_id:{contact_id}")
    logger.info(f"[contact_id] ✅ Salvo contact_id={contact_id} para phone={phone}")


# ── Deduplicação ─────────────────────────────────────────────────────────
_PROCESSED_IDS: dict = {}
_DEDUP_TTL = 60  # segundos


def _is_duplicate(message_id: str) -> bool:
    """Retorna True se este message_id já foi processado nos últimos 60s."""
    if not message_id:
        return False
    now = time.time()
    expired = [k for k, v in _PROCESSED_IDS.items() if now - v > _DEDUP_TTL]
    for k in expired:
        del _PROCESSED_IDS[k]
    if message_id in _PROCESSED_IDS:
        return True
    _PROCESSED_IDS[message_id] = now
    return False


# Eventos de atendente humano
_AGENT_MSG_EVENTS = {"message.agent", "agent.message", "agent_message"}

# Eventos apenas auditados, sem ação
_LOG_ONLY_EVENTS = {
    "conversation.opened", "conversation.closed",
    "conversation.assigned", "conversation.resolved",
}


# ─────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────

def _is_form_lead(body: dict) -> bool:
    """Detecta payload de formulário: tem 'Telefone'/'Celular' e chave 'Identificador' mas não 'content'.
    Nome pode vir vazio — não bloqueia o registro.
    """
    phone = body.get("Telefone") or body.get("Celular")
    if not phone:
        return False
    # Presença de 'Identificador' é marcador forte de formulário PJ
    # (mesmo que Nome venha vazio)
    has_form_keys = "Identificador" in body or "E-mail" in body or "Nome" in body
    if not has_form_keys:
        return False
    if "event" in body or "message_data" in body or "content" in body:
        return False
    return True


def _normalize_form_phone(phone_raw: str) -> str:
    """Normaliza "(11) 9 8180-5098" → "5511981805098"."""
    digits = re.sub(r"\D", "", phone_raw)
    if not digits:
        return ""
    if len(digits) in (10, 11) and not digits.startswith("55"):
        digits = "55" + digits
    elif len(digits) == 12 and not digits.startswith("55"):
        digits = "55" + digits[-11:]
    return digits


def _extract_fields(body: dict):
    """Extrai todos os campos relevantes do payload."""
    data = body["data"] if ("data" in body and isinstance(body["data"], dict)) else body

    phone_number = extract_phone_from_payload(data)
    contact_name = extract_name_from_payload(data)
    contact_id   = extract_contact_id_from_payload(data)
    message_text = extract_message_from_payload(data)

    content_block = data.get("content", {})
    message_id = (
        (content_block.get("id", "") if isinstance(content_block, dict) else "")
        or (data.get("message", {}).get("id", "") if isinstance(data.get("message"), dict) else "")
        or data.get("message_id", "")
    )
    event = body.get("event", "") or body.get("type", "") or body.get("action", "")
    action = content_block.get("action", "") if isinstance(content_block, dict) else ""

    return phone_number, contact_name, contact_id, message_id, message_text, event, action, data


# ─────────────────────────────────────────────────────────────────────────
# WEBHOOK 1 — /webhook/tallos (monitoramento geral + bot para leads PJ)
# ─────────────────────────────────────────────────────────────────────────

@router.post("/webhook/tallos")
async def tallos_webhook_monitor(
    request: Request,
    background_tasks: BackgroundTasks,
    x_tallos_secret: Optional[str] = Header(None, alias="X-Tallos-Secret"),
):
    """
    Recebe eventos do RD Conversas (repassados pelo BOT MBA ou via nginx).
    Bot SDR PJ só atua para leads cadastrados via /webhook/tallospj.
    """
    try:
        body = await request.json()
    except Exception:
        return {"status": "ok"}

    if settings.tallos_webhook_secret:
        if x_tallos_secret != settings.tallos_webhook_secret:
            return {"status": "ok"}

    background_tasks.add_task(_process_monitor, body)
    return {"status": "ok"}


async def _process_monitor(body: dict):
    """
    Processa evento do webhook geral.
    - Loga tudo
    - Salva contact_id
    - Se for lead PJ → passa para o bot (sujeito às regras normais)
    """
    try:
        if _is_form_lead(body):
            await _handle_form_lead(body, source_channel="tallos_form_pj")
            return

        phone_number, contact_name, contact_id, message_id, message_text, event, action, data = \
            _extract_fields(body)

        agent_msg = is_agent_message(data)

        logger.info(
            f"👁️  TALLOS PJ MONITOR | phone={phone_number} | contact_id={contact_id} | "
            f"action={action} | msg={message_text[:60]!r}"
        )

        try:
            await log_webhook_event(
                event_type=f"tallos_pj_monitor:{action}" if action else "tallos_pj_monitor:message",
                raw_payload=body,
                instance_id="tallos_pj_monitor",
                phone_number=phone_number,
            )
        except Exception as e:
            logger.error(f"[Tallos PJ Monitor] Erro ao gravar log: {e}")

        # Só atualiza contact_id se o lead JÁ existe como PJ — não cria registros novos
        if phone_number and contact_id:
            try:
                pj_check = await is_pj_lead(phone_number)
                if pj_check:
                    await upsert_lead(phone_number, contact_name=contact_name)
                    await _save_contact_id_if_missing(phone_number, contact_id)
            except Exception as e:
                logger.error(f"[Tallos PJ Monitor] Erro ao atualizar contato: {e}")

        if not phone_number or not message_text:
            return
        if event in _LOG_ONLY_EVENTS:
            return

        if event in _AGENT_MSG_EVENTS or agent_msg:
            agent_name = (
                data.get("agent", {}).get("name", "Consultor")
                if isinstance(data.get("agent"), dict) else "Consultor PJ"
            )
            await handle_agent_message(phone_number, message_text, agent_name, channel="tallos")
            return

        # ── CRUZAMENTO: só processa se for lead PJ ──────────────────
        pj = await is_pj_lead(phone_number)
        if not pj:
            logger.debug(
                f"[Tallos PJ Monitor] {phone_number} não é lead PJ — mensagem ignorada pelo bot"
            )
            return

        if message_id and _is_duplicate(message_id):
            logger.debug(f"[Tallos PJ Monitor] Duplicata ignorada | message_id={message_id}")
            return

        logger.info(
            f"✅ TALLOS PJ MONITOR → BOT | Lead PJ confirmado | "
            f"phone={phone_number} | msg={message_text[:60]!r}"
        )

        await _send_to_bot(phone_number, message_text, contact_name, message_id, contact_id)

    except Exception as e:
        logger.error(f"[Tallos PJ Monitor] Erro: {e}", exc_info=True)


# ─────────────────────────────────────────────────────────────────────────
# WEBHOOK 2 — /webhook/tallospj (cadastro de leads PJ)
# ─────────────────────────────────────────────────────────────────────────

@router.post("/webhook/tallospj")
async def tallos_webhook_pj(
    request: Request,
    background_tasks: BackgroundTasks,
    x_tallos_secret: Optional[str] = Header(None, alias="X-Tallos-Secret"),
):
    """
    Cadastra leads como PJ. Chamado pelas automações PJ do RD Conversas.
    A partir do cadastro, mensagens deste lead via /webhook/tallos
    serão processadas pelo bot SDR PJ.
    """
    try:
        body = await request.json()
    except Exception:
        return {"status": "ok"}

    if settings.tallos_webhook_secret:
        if x_tallos_secret != settings.tallos_webhook_secret:
            return {"status": "ok"}

    background_tasks.add_task(_register_pj_lead, body)
    return {"status": "ok"}


async def _register_pj_lead(body: dict):
    """
    Registra o lead como PJ no banco.
    source_channel = 'tallos_pj' é o marcador que habilita o bot SDR PJ.
    """
    try:
        if _is_form_lead(body):
            # Distingue origem pelo campo Identificador:
            # "Chat" → veio do fluxo de chat PJ → source_channel = "tallos_pj" (bot rastreia)
            # Outros → veio de formulário → source_channel = "tallos_form_pj"
            identificador = (body.get("Identificador", "") or "").strip().lower()
            if "chat" in identificador:
                source = "tallos_pj"
                logger.info(f"[Tallos PJ] Identificador='{identificador}' → origem: CHAT (bot vai rastrear)")
            else:
                source = "tallos_form_pj"
                logger.info(f"[Tallos PJ] Identificador='{identificador}' → origem: FORMULÁRIO")
            await _handle_form_lead(body, source_channel=source)
            return

        phone_number, contact_name, contact_id, message_id, message_text, event, action, data = \
            _extract_fields(body)

        logger.info(
            f"🏢 TALLOS PJ REGISTER | phone={phone_number} | "
            f"contact_id={contact_id} | msg={message_text[:60]!r}"
        )

        try:
            await log_webhook_event(
                event_type="tallos_pj:register",
                raw_payload=body,
                instance_id="tallos_pj",
                phone_number=phone_number,
            )
        except Exception as e:
            logger.error(f"[Tallos PJ] Erro ao gravar log: {e}")

        if not phone_number:
            logger.warning("[Tallos PJ] Payload sem telefone — ignorado")
            return

        # ── Marca como lead PJ no banco ──────────────────────────────
        await upsert_lead(
            phone_number,
            contact_name=contact_name,
            source_channel="tallos_pj",
        )
        await _save_contact_id_if_missing(phone_number, contact_id)
        await upsert_bot_session(phone_number, agent_active=0)

        logger.info(
            f"[Tallos PJ] ✅ Lead PJ registrado | phone={phone_number} | "
            f"contact_id={contact_id or '(pendente)'}"
        )

        if message_text and not (event in _LOG_ONLY_EVENTS or is_agent_message(data)):
            if not (message_id and _is_duplicate(message_id)):
                await _send_to_bot(phone_number, message_text, contact_name, message_id, contact_id)

    except Exception as e:
        logger.error(f"[Tallos PJ] Erro em _register_pj_lead: {e}", exc_info=True)


# ─────────────────────────────────────────────────────────────────────────
# BOT — envio para processamento
# ─────────────────────────────────────────────────────────────────────────

async def _send_to_bot(
    phone_number: str,
    message: str,
    contact_name: str,
    message_id: str,
    contact_id: str,
) -> None:
    """Encaminha mensagem para o bot (handle_incoming_message)."""
    async def _tallos_send(phone: str, text: str, session_id: str = "") -> None:
        cid = contact_id or session_id
        await tallos_service.send_text(phone, text, session_id=cid)

    await handle_incoming_message(
        phone_number=phone_number,
        message=message,
        contact_name=contact_name,
        message_id=message_id,
        session_id=contact_id,
        send_fn=_tallos_send,
        channel="tallos",
    )


# ─────────────────────────────────────────────────────────────────────────
# HANDLER — Form leads PJ
# ─────────────────────────────────────────────────────────────────────────

async def _handle_form_lead(body: dict, source_channel: str = "tallos_form_pj") -> None:
    """Registra lead de formulário PJ no banco e cria sessão de espera."""
    name           = (body.get("Nome", "") or "").strip()
    email          = (body.get("E-mail", "") or body.get("Email", "") or "").strip()
    company        = (body.get("Empresa", "") or body.get("Razão Social", "") or "").strip()
    job_title      = (body.get("Cargo", "") or "").strip()
    training       = (body.get("Treinamento", "") or body.get("Curso", "") or "").strip()
    identificador  = (body.get("Identificador", "") or "").strip()
    qtd_colab      = (body.get("Quantidade de Colaboradores", "") or "").strip()
    servico        = (body.get("Serviço", "") or body.get("Servico", "") or "").strip()
    canal          = (body.get("Canal", "") or "").strip()
    phone          = _normalize_form_phone(body.get("Telefone", "") or body.get("Celular", "") or "")

    # Salva todo o payload original como JSON para exibição no detalhe do lead
    try:
        raw_form_data = json.dumps(body, ensure_ascii=False)
    except Exception:
        raw_form_data = ""

    logger.info(
        f"📋 FORM LEAD PJ | nome={name!r} | phone={phone} | "
        f"empresa={company!r} | servico={servico!r} | identificador={identificador!r} | source={source_channel}"
    )

    if not phone:
        logger.warning("[Tallos PJ] Form lead sem telefone — ignorado.")
        return

    try:
        await log_webhook_event(
            event_type="tallos_pj:form_lead",
            raw_payload=body,
            instance_id="tallos_pj",
            phone_number=phone,
        )
    except Exception as e:
        logger.error(f"[Tallos PJ Form] Erro ao gravar log: {e}")

    try:
        await upsert_lead(
            phone,
            contact_name=name,
            email=email,
            company=company,
            job_title=job_title,
            training_interest=training or servico,
            identificador=identificador,
            qtd_colaboradores=qtd_colab,
            servico=servico,
            raw_form_data=raw_form_data,
            source_channel=source_channel,
        )
        logger.info(f"[Tallos PJ Form] ✅ Lead registrado | phone={phone} | channel={source_channel}")
    except Exception as e:
        logger.error(f"[Tallos PJ Form] Erro ao salvar lead: {e}")

    contact_id = ""
    try:
        contact = await tallos_service.get_contact_by_phone(phone)
        contact_id = str(contact.get("_id", "")) if contact else ""
        if contact_id:
            logger.info(f"[Tallos PJ Form] ✅ contact_id: {contact_id}")
    except Exception as e:
        logger.error(f"[Tallos PJ Form] Erro ao buscar contact_id: {e}")

    try:
        await upsert_bot_session(phone, agent_active=0)
        if contact_id:
            await _save_contact_id_if_missing(phone, contact_id)
        logger.info(
            f"[Tallos PJ Form] ✅ Sessão criada | phone={phone} | "
            f"contact_id={contact_id or '(pendente)'}"
        )
    except Exception as e:
        logger.error(f"[Tallos PJ Form] Erro ao criar sessão: {e}")


# ─────────────────────────────────────────────────────────────────────────
# STATUS
# ─────────────────────────────────────────────────────────────────────────

@router.get("/webhook/tallospj/status")
async def tallos_webhook_pj_status():
    """Verifica status da integração Tallos PJ."""
    api_status = await tallos_service.get_status()
    return {
        "channel":            "tallos_pj",
        "api_connected":      api_status.get("connected", False),
        "token_configured":   bool(settings.tallos_api_token),
        "secret_configured":  bool(settings.tallos_webhook_secret),
        "webhook_monitor":    "/webhook/tallos  (eventos gerais — bot só para leads PJ)",
        "webhook_pj":         "/webhook/tallospj  (cadastra lead como PJ)",
        "nota":               "Configure o BOT MBA para repassar eventos ao BOT SDR PJ na porta 8001",
    }
