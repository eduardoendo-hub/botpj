"""
Controlador principal do bot SDR PJ — orquestra todos os fluxos de atendimento.

Arquitetura multi-canal:
  O bot_controller é agnóstico ao canal de envio.
  Cada integração injeta sua própria função de envio via parâmetro `send_fn`.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Callable, Awaitable, Optional

_BRT = timezone(timedelta(hours=-3))

from app.core.database import (
    save_message, upsert_lead, get_bot_config,
    get_bot_session, upsert_bot_session,
    get_conversation_history, get_lead_by_phone,
)
from app.services.ai_engine import (
    generate_response, classify_conversation_context,
    extract_lead_data, analyze_and_update_lead,
)
from app.services.email_service import send_lead_notification

logger = logging.getLogger(__name__)


async def _cfg() -> dict:
    return await get_bot_config()


def _bool(val: str) -> bool:
    return str(val).lower() in ("true", "1", "yes", "sim")


def _int(val: str, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _is_test_phone(phone_number: str, cfg: dict) -> bool:
    raw = cfg.get("test_phone_numbers", "")
    if not raw:
        return False
    test_phones = [p.strip() for p in raw.split(",") if p.strip()]
    return phone_number in test_phones


async def is_bot_active_now() -> bool:
    """
    Verifica se o bot deve estar ativo neste momento.
    Regras:
      1. bot_enabled=false → inativo sempre
      2. bot_schedule_enabled=false → ativo sempre
      3. Seg-sex: ativo das 19h até 8h (fora do horário comercial)
      4. Fim de semana: ativo o dia todo se bot_schedule_weekend=true
    """
    cfg = await _cfg()

    if not _bool(cfg.get("bot_enabled", "true")):
        return False

    if not _bool(cfg.get("bot_schedule_enabled", "true")):
        return True

    now = datetime.now(_BRT)
    weekday = now.weekday()
    hour    = now.hour
    is_weekend = weekday >= 5

    if is_weekend:
        return _bool(cfg.get("bot_schedule_weekend", "true"))

    start = _int(cfg.get("bot_schedule_weekday_start", "19"))
    end   = _int(cfg.get("bot_schedule_weekday_end",   "8"))

    if start >= end:
        return hour >= start or hour < end
    else:
        return start <= hour < end


ChannelSendFn = Callable[[str, str, str], Awaitable[None]]


async def handle_incoming_message(
    phone_number: str,
    message: str,
    contact_name: str = "",
    message_id: str = "",
    session_id: str = "",
    send_fn: Optional[ChannelSendFn] = None,
    channel: str = "tallos",
) -> None:
    """Ponto de entrada para mensagens recebidas de qualquer canal (lead → bot)."""
    if not message or len(message.strip()) < 2:
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    logger.info(f"[{phone_number}] Mensagem recebida: {message[:80]}")

    await save_message(phone_number, "user", message, contact_name, channel=channel)
    source_channel = "tallos_chat" if channel == "tallos" else channel
    await upsert_lead(phone_number, contact_name, source_channel=source_channel)
    await upsert_bot_session(
        phone_number,
        last_user_msg_at=now_iso,
        context_is_waiting=0,
    )

    cfg = await _cfg()
    session = await get_bot_session(phone_number)

    # Número de teste? Sempre responde
    is_test = _is_test_phone(phone_number, cfg)
    if is_test:
        logger.info(f"[{phone_number}] Número de teste — bot responde diretamente.")
        await _respond(phone_number, message, contact_name, cfg, session, session_id, send_fn, channel)
        return

    bot_active = await is_bot_active_now()

    agent_active = bool(session and session.get("agent_active"))

    if agent_active:
        last_agent_msg_at = session.get("last_agent_msg_at") if session else None
        if last_agent_msg_at:
            try:
                last_dt = datetime.fromisoformat(last_agent_msg_at)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                hours_since = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
                if hours_since >= 12:
                    await upsert_bot_session(phone_number, agent_active=0)
                    agent_active = False
                    logger.info(f"[{phone_number}] Consultor inativo há {hours_since:.1f}h — bot reativado.")
            except (ValueError, TypeError):
                pass

        if agent_active:
            logger.info(f"[{phone_number}] Atendente ativo — bot silenciado.")
            return

    if bot_active:
        await _respond(phone_number, message, contact_name, cfg, session, session_id, send_fn, channel)
    else:
        logger.info(f"[{phone_number}] Horário comercial — aguardando consultor.")


async def _respond(
    phone_number: str,
    message: str,
    contact_name: str,
    cfg: dict,
    session: dict | None,
    session_id: str = "",
    send_fn: Optional[ChannelSendFn] = None,
    channel: str = "tallos",
) -> None:
    """Gera e envia resposta do bot."""
    try:
        history = await get_conversation_history(phone_number, limit=20)
        is_returning = len(history) > 1

        ai_response, needs_escalation = await generate_response(
            phone_number=phone_number,
            user_message=message,
            contact_name=contact_name,
            is_returning_lead=is_returning,
            prefetched_history=history,
        )

        if needs_escalation:
            await _send(phone_number, ai_response, session_id, send_fn)
            await save_message(phone_number, "assistant", ai_response, "Bot SDR PJ", channel=channel)

            now_iso = datetime.now(timezone.utc).isoformat()
            await upsert_bot_session(
                phone_number,
                last_bot_msg_at=now_iso,
                last_agent_msg_at=now_iso,
                agent_active=1,
            )
            logger.info(f"[{phone_number}] Escalonado para consultor — bot pausado.")

            await _notify_lead_escalation(phone_number, contact_name, cfg, history)
            return

        await _send(phone_number, ai_response, session_id, send_fn)
        await save_message(phone_number, "assistant", ai_response, "Bot SDR PJ", channel=channel)

        now_iso = datetime.now(timezone.utc).isoformat()
        await upsert_bot_session(phone_number, last_bot_msg_at=now_iso)

        context_waiting = await classify_conversation_context(phone_number)
        await upsert_bot_session(phone_number, context_is_waiting=int(context_waiting))

        # Análise estruturada em background — não bloqueia a resposta ao lead
        asyncio.create_task(_run_lead_analysis(phone_number, contact_name))

        logger.info(f"[{phone_number}] Resposta enviada. context_is_waiting={context_waiting}")

    except Exception as e:
        logger.error(f"[{phone_number}] Erro ao responder: {e}")


async def _run_lead_analysis(phone_number: str, contact_name: str) -> None:
    """
    Analisa a conversa em background e persiste todos os campos estruturados do lead PJ.
    Roda via asyncio.create_task — não bloqueia o fluxo principal.
    """
    try:
        history = await get_conversation_history(phone_number, limit=30)
        extracted = await analyze_and_update_lead(phone_number, history)

        # Monta payload para upsert_lead com todos os campos extraídos
        update_kwargs: dict = {}

        # Campos de identificação
        if extracted.get("nome"):
            update_kwargs["contact_name"] = extracted["nome"]
        if extracted.get("email"):
            update_kwargs["email"] = extracted["email"]
        if extracted.get("empresa"):
            update_kwargs["company"] = extracted["empresa"]
        if extracted.get("job_title"):
            update_kwargs["job_title"] = extracted["job_title"]

        # Campos PJ específicos
        if extracted.get("training_interest") or extracted.get("tema_interesse"):
            update_kwargs["training_interest"] = (
                extracted.get("training_interest") or extracted.get("tema_interesse")
            )

        # Campos de qualificação estruturada
        for field in (
            "tema_interesse", "tipo_interesse", "qtd_participantes",
            "formato", "cidade", "prazo", "urgencia", "objetivo_negocio",
            "lead_temperature", "trail", "score", "proximo_passo", "status_conversa",
        ):
            val = extracted.get(field)
            if val is not None and val != "" and val != "desconhecido":
                update_kwargs[field] = val

        # Estágio do funil baseado na temperatura e trail
        trail = extracted.get("trail", "")
        temp  = extracted.get("lead_temperature", "")
        score = extracted.get("score")
        try:
            score_int = int(score) if score else 0
        except (ValueError, TypeError):
            score_int = 0

        if trail in ("D", "E") or score_int >= 80:
            update_kwargs["stage"] = "negociando"
        elif temp == "quente" or score_int >= 60:
            update_kwargs["stage"] = "qualificado"

        if update_kwargs:
            await upsert_lead(phone_number, contact_name=contact_name, **update_kwargs)
            logger.info(
                f"[{phone_number}] Lead atualizado — trail={extracted.get('trail')} "
                f"temp={extracted.get('lead_temperature')} score={extracted.get('score')}"
            )

    except Exception as e:
        logger.error(f"[{phone_number}] Erro na análise em background: {e}", exc_info=True)


async def _send(
    phone_number: str,
    text: str,
    session_id: str = "",
    send_fn: Optional[ChannelSendFn] = None,
):
    """Envia mensagem pelo canal adequado."""
    if send_fn is not None:
        await send_fn(phone_number, text, session_id)
    else:
        # Fallback: Tallos como canal padrão
        from app.services.tallos import tallos_service
        await tallos_service.send_text(phone_number, text, session_id=session_id)


async def _notify_lead_escalation(
    phone_number: str,
    contact_name: str,
    cfg: dict,
    history: list,
) -> None:
    """Extrai dados completos do lead PJ e envia notificação por email."""
    try:
        logger.info(f"[{phone_number}] ── Extraindo dados do lead PJ para escalação ──")

        # Usa analyze_and_update_lead para extração rica de todos os campos
        extracted = await analyze_and_update_lead(phone_number, history)
        logger.info(f"[{phone_number}] Dados extraídos: {extracted}")

        saved_lead = await get_lead_by_phone(phone_number) or {}

        nome    = extracted.get("nome")    or saved_lead.get("contact_name") or contact_name or ""
        email   = extracted.get("email")   or saved_lead.get("email")   or ""
        empresa = extracted.get("empresa") or saved_lead.get("company") or ""
        cargo   = extracted.get("job_title") or saved_lead.get("job_title") or ""
        tema    = (
            extracted.get("training_interest")
            or extracted.get("tema_interesse")
            or saved_lead.get("training_interest") or ""
        )

        lead_payload = {
            "contact_name":      nome,
            "phone_number":      phone_number,
            "email":             email,
            "company":           empresa,
            "job_title":         cargo,
            "training_interest": tema,
            "trail":             extracted.get("trail") or saved_lead.get("trail") or "",
            "lead_temperature":  extracted.get("lead_temperature") or saved_lead.get("lead_temperature") or "",
            "score":             extracted.get("score") or saved_lead.get("score") or "",
            "proximo_passo":     extracted.get("proximo_passo") or saved_lead.get("proximo_passo") or "",
            "qtd_participantes": extracted.get("qtd_participantes") or saved_lead.get("qtd_participantes") or "",
            "formato":           extracted.get("formato") or saved_lead.get("formato") or "",
            "urgencia":          extracted.get("urgencia") or saved_lead.get("urgencia") or "",
            "ocorrencia":        datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M"),
        }

        # Persiste no banco antes de enviar o email
        upsert_kwargs = {k: v for k, v in lead_payload.items()
                        if k not in ("ocorrencia", "phone_number") and v}
        upsert_kwargs["stage"] = "negociando"
        await upsert_lead(phone_number, **upsert_kwargs)

        email_ok = await send_lead_notification(lead_payload, cfg)
        if email_ok:
            logger.info(f"[{phone_number}] ✅ Email de notificação enviado.")
        else:
            logger.warning(f"[{phone_number}] ⚠️ Email NÃO enviado.")

    except Exception as e:
        logger.error(f"[{phone_number}] ❌ Erro ao notificar escalação: {e}", exc_info=True)


async def handle_agent_message(
    phone_number: str,
    message: str = "",
    agent_name: str = "Consultor",
    channel: str = "tallos",
) -> None:
    """Chamado quando um atendente humano envia mensagem. Pausa o bot."""
    now_iso = datetime.now(timezone.utc).isoformat()
    await upsert_bot_session(
        phone_number,
        last_agent_msg_at=now_iso,
        agent_active=1,
    )
    if message:
        await save_message(phone_number, "consultant", message, agent_name, channel=channel)
    logger.info(f"[{phone_number}] Atendente '{agent_name}' respondeu — bot pausado.")
