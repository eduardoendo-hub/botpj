"""
Cliente de integração com RD Conversas (Tallos).

Este módulo é COMPLETAMENTE ISOLADO do ChatPro.
Nenhum import de chatpro.py aqui, e nenhum import deste arquivo em chatpro.py.

Responsabilidade:
  • Receber payloads do webhook Tallos e normalizá-los
  • Enviar mensagens de volta via API Tallos

Documentação Tallos:
  https://developers.rdstation.com/reference/conversas-v2-introduction

Autenticação:
  Header: Authorization: Bearer <TALLOS_API_TOKEN>

Fluxo de envio:
  1. Webhook Tallos → POST /webhook/tallos  (traz phone_number)
  2. webhook_tallos.py extrai phone + texto + contact_id (se disponível)
  3. handle_incoming_message(..., send_fn=tallos_service.send_text)
  4. Bot gera resposta → send_fn(phone, text, contact_id)
  5. Se contact_id presente: usa direto
     Se não:  GET /contacts/{phone}/exists  → obtém _id
  6. POST /messages/{contact_id}/send  (multipart/form-data)
"""

import httpx
import logging
from typing import Optional, Dict, Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class TallosService:
    """
    Cliente HTTP para a API do RD Conversas (Tallos) v2.

    Toda a lógica de comunicação com a Tallos fica aqui,
    isolada do restante da aplicação.
    """

    def __init__(self):
        self.base_url = settings.tallos_api_url.rstrip("/")  # https://api.tallos.com.br/v2
        self.token    = settings.tallos_api_token

    @property
    def _headers(self) -> Dict[str, str]:
        # NÃO incluir Content-Type — httpx define automaticamente para form-data
        return {"Authorization": f"Bearer {self.token}"}

    # ── Contatos ───────────────────────────────────────────────────────

    async def get_contact_by_phone(self, phone_number: str) -> Dict[str, Any]:
        """
        Busca contato pelo telefone.

        GET /v2/contacts/{cel_phone}/exists
        Formato esperado: 5511999998888 (E.164 sem o +)

        Retorna o dict completo do contato, incluindo _id.
        Retorna {} se não encontrado ou em caso de erro.
        """
        phone = _normalize_phone(phone_number)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{self.base_url}/contacts/{phone}/exists",
                    headers=self._headers,
                    params={"channel": "whatsapp"},
                )

                logger.debug(
                    f"[Tallos] GET /contacts/{phone}/exists → {response.status_code}"
                )

                if response.status_code == 404:
                    logger.warning(f"[Tallos] Contato não encontrado para phone={phone}")
                    return {}

                if response.status_code == 401:
                    logger.error("[Tallos] 401 Unauthorized. Verifique o TALLOS_API_TOKEN.")
                    return {}

                if response.status_code != 200:
                    logger.error(
                        f"[Tallos] Erro {response.status_code} ao buscar contato: "
                        f"{response.text[:200]}"
                    )
                    return {}

                body = response.json()
                # A resposta vem em "date" (nome do campo conforme docs Tallos)
                # mas também aceitamos "data" como fallback
                contact = body.get("date") or body.get("data") or body
                logger.info(
                    f"[Tallos] ✅ Contato encontrado: "
                    f"_id={contact.get('_id')} | name={contact.get('full_name')} | "
                    f"phone={phone}"
                )
                return contact

        except Exception as e:
            logger.error(f"[Tallos] Erro ao buscar contato por phone {phone}: {e}")
            return {}

    async def get_contact_id_by_phone(self, phone_number: str) -> str:
        """
        Retorna apenas o _id do contato pelo telefone.
        Retorna '' se não encontrado.
        """
        contact = await self.get_contact_by_phone(phone_number)
        return str(contact.get("_id", "")) if contact else ""

    async def get_recent_messages(
        self, contact_id: str, limit: int = 10
    ) -> list:
        """
        Retorna as mensagens mais recentes de uma conversa pelo contact_id.

        GET /v2/messages/{contact_id}

        Útil para verificar se uma mensagem de boas-vindas já foi enviada
        após a chegada de um lead via formulário do site.

        Retorna lista de mensagens ou [] em caso de erro/ausência.
        """
        if not self.token or not contact_id:
            return []

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{self.base_url}/messages/{contact_id}",
                    headers=self._headers,
                    params={"limit": limit},
                )

                logger.debug(
                    f"[Tallos] GET /messages/{contact_id} → {response.status_code}"
                )

                if response.status_code == 404:
                    logger.info(f"[Tallos] Nenhuma mensagem ainda para contact_id={contact_id}")
                    return []

                if response.status_code != 200:
                    logger.warning(
                        f"[Tallos] Erro {response.status_code} ao buscar mensagens: "
                        f"{response.text[:200]}"
                    )
                    return []

                body = response.json()
                messages = (
                    body.get("date")
                    or body.get("data")
                    or body.get("messages")
                    or (body if isinstance(body, list) else [])
                )
                return messages if isinstance(messages, list) else []

        except Exception as e:
            logger.error(f"[Tallos] Erro ao buscar mensagens do contact_id={contact_id}: {e}")
            return []

    # ── Envio de mensagem (compatível com ChannelSendFn) ───────────────

    async def send_text(
        self,
        phone_number: str,
        message: str,
        session_id: str = "",   # ← carrega contact_id quando disponível
    ) -> None:
        """
        Envia mensagem de texto para o contato via API Tallos.

        Endpoint: POST /v2/messages/{contact_id}/send
        Body:      multipart/form-data

        Assinatura compatível com ChannelSendFn do bot_controller:
          async (phone_number, message, session_id) -> None

        Resolução do contact_id (em ordem de prioridade):
          1. session_id fornecido pelo webhook (mais rápido)
          2. Lookup via GET /contacts/{phone}/exists (fallback)

        Raises:
          RuntimeError se o envio falhar.
        """
        if not self.token:
            logger.warning("[Tallos] TALLOS_API_TOKEN não configurado — mensagem não enviada.")
            raise RuntimeError("TALLOS_API_TOKEN não configurado")

        # ── Resolve contact_id ────────────────────────────────────────
        contact_id = session_id.strip() if session_id else ""

        if not contact_id:
            logger.info(
                f"[Tallos] contact_id ausente para {phone_number}, "
                "buscando via GET /contacts/{phone}/exists..."
            )
            contact_id = await self.get_contact_id_by_phone(phone_number)

        if not contact_id:
            raise RuntimeError(
                f"[Tallos] Não foi possível obter contact_id para {phone_number}. "
                "Verifique se o contato existe no RD Conversas."
            )

        # ── Quebra mensagens longas e envia parte a parte ─────────────
        parts = _split_message(message)
        for part in parts:
            await self._send_part(contact_id, part, phone_number)

    async def _send_part(
        self, contact_id: str, message: str, phone_number: str = ""
    ) -> None:
        """
        Envia uma parte da mensagem.

        POST /v2/messages/{contact_id}/send
        Body: multipart/form-data
          - message  (string, obrigatório)
          - sent_by  (string, obrigatório: "bot")
        """
        url = f"{self.base_url}/messages/{contact_id}/send"

        form_data = {
            "message":  message,
            "sent_by":  "bot",   # obrigatório: identifica como mensagem do bot
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url,
                    headers=self._headers,
                    data=form_data,   # ← form-data, NÃO json
                )

                logger.debug(
                    f"[Tallos] POST /messages/{contact_id}/send "
                    f"→ {response.status_code}"
                )

                if response.status_code == 401:
                    raise RuntimeError("[Tallos] 401 Unauthorized — verifique o TALLOS_API_TOKEN")

                if response.status_code == 404:
                    raise RuntimeError(
                        f"[Tallos] 404 — contact_id '{contact_id}' não encontrado"
                    )

                if response.status_code not in (200, 201):
                    raise RuntimeError(
                        f"[Tallos] HTTP {response.status_code}: {response.text[:200]}"
                    )

                logger.info(
                    f"[Tallos] ✅ Mensagem enviada | "
                    f"contact_id={contact_id} | phone={phone_number} | chars={len(message)}"
                )

        except httpx.TimeoutException:
            raise RuntimeError(f"[Tallos] Timeout ao enviar para contact_id={contact_id}")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"[Tallos] Erro inesperado ao enviar: {e}")

    # ── Status ─────────────────────────────────────────────────────────

    async def sync_conversations(
        self,
        phone_number: str,
        contact_id: str,
        contact_name: str = "",
        limit: int = 50,
    ) -> int:
        """
        Busca mensagens do contato na API Tallos e salva no banco local.

        Retorna o número de mensagens novas importadas.
        Usa external_id para evitar duplicatas.
        """
        from app.core.database import save_message_external

        if not contact_id:
            logger.warning(f"[Tallos Sync] contact_id ausente para {phone_number} — skip")
            return 0

        messages = await self.get_recent_messages(contact_id, limit=limit)
        if not messages:
            logger.info(f"[Tallos Sync] Nenhuma mensagem na API para contact_id={contact_id}")
            return 0

        imported = 0
        for msg in messages:
            # Extrai campos da mensagem
            msg_id      = str(msg.get("_id", "") or msg.get("id", ""))
            msg_text    = (
                msg.get("message", "")
                or msg.get("content", "")
                or msg.get("text", "")
                or ""
            ).strip()
            msg_type    = msg.get("type", "text")
            sent_by     = msg.get("sent_by", "") or msg.get("origin", "")
            created_raw = msg.get("created_at", "") or msg.get("createdAt", "") or ""

            if not msg_text:
                continue
            if msg_type not in ("text", "chat", ""):
                msg_text = f"[{msg_type}]"

            # Determina o papel: bot/agent → assistant | lead → user
            if sent_by in ("bot", "operator", "agent", "attendant", "system"):
                role = "assistant"
            else:
                role = "user"

            # Normaliza timestamp
            created_at = ""
            if created_raw:
                try:
                    from datetime import datetime, timezone
                    if isinstance(created_raw, (int, float)):
                        dt = datetime.fromtimestamp(created_raw / 1000, tz=timezone.utc)
                    else:
                        dt = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
                    created_at = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    created_at = str(created_raw)[:19]

            saved = await save_message_external(
                phone_number=phone_number,
                role=role,
                message=msg_text,
                contact_name=contact_name,
                channel="tallos",
                external_id=msg_id,
                created_at=created_at,
            )
            if saved:
                imported += 1

        logger.info(
            f"[Tallos Sync] ✅ {imported} msgs importadas | "
            f"phone={phone_number} | contact_id={contact_id}"
        )
        return imported

    async def get_status(self) -> Dict[str, Any]:
        """Verifica conectividade com a API Tallos."""
        if not self.token:
            return {"status": "token_missing", "connected": False}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.base_url}/employees",
                    headers=self._headers,
                )
                if response.status_code == 200:
                    return {"status": "connected", "connected": True}
                return {
                    "status": "error",
                    "connected": False,
                    "http_code": response.status_code,
                }
        except Exception as e:
            return {"status": "error", "connected": False, "detail": str(e)}


# ── Helpers ────────────────────────────────────────────────────────────

def _normalize_phone(phone: str) -> str:
    """
    Normaliza número para formato E.164 sem o + (ex: 5511999998888).
    Remove +, espaços, traços e parênteses.
    """
    return (
        str(phone)
        .replace("+", "")
        .replace(" ", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
        .strip()
    )


def _split_message(message: str, max_length: int = 4000) -> list[str]:
    """Quebra mensagens longas em partes no limite de parágrafo mais próximo."""
    parts = []
    while len(message) > max_length:
        cut = message[:max_length]
        pos = cut.rfind("\n\n")
        if pos == -1:
            pos = cut.rfind("\n")
        if pos == -1:
            pos = cut.rfind(". ")
        if pos == -1:
            pos = max_length
        parts.append(message[:pos + 1])
        message = message[pos + 1:].strip()
    parts.append(message)
    return [p for p in parts if p.strip()]


# ── Funções de normalização de payload do webhook ──────────────────────

def extract_phone_from_payload(data: dict) -> str:
    """Extrai e normaliza o telefone do payload do webhook Tallos."""
    contact = data.get("contact", {})
    if isinstance(contact, dict):
        phone = (
            contact.get("cel_phone", "")
            or contact.get("phone", "")
            or contact.get("telephone", "")
        )
    else:
        phone = ""

    if not phone:
        phone = data.get("cel_phone", "") or data.get("phone", "") or data.get("number", "")

    return _normalize_phone(phone) if phone else ""


def extract_contact_id_from_payload(data: dict) -> str:
    """
    Extrai o contact_id (_id) do payload do webhook Tallos.
    Quando presente, evita o lookup extra por telefone.
    """
    contact = data.get("contact", {})
    if isinstance(contact, dict):
        cid = contact.get("_id", "") or contact.get("id", "") or contact.get("contact_id", "")
        if cid:
            return str(cid)

    return str(data.get("contact_id", "") or data.get("contactId", "") or "")


def extract_message_from_payload(data: dict) -> str:
    """Extrai o texto da mensagem do payload do webhook Tallos.

    Suporta dois formatos:
      Novo (RD Conversas): {"content": {"message": "Olá?", "type": "text"}, "contact": {...}}
      Antigo (Tallos v1):  {"message": {"content": "...", "text": "..."}}
    """
    # ── Novo formato RD Conversas ─────────────────────────────────────
    # {"content": {"message": "texto", "type": "text", "action": "..."}}
    content_block = data.get("content", {})
    if isinstance(content_block, dict):
        text = (
            content_block.get("message", "")
            or content_block.get("text", "")
            or content_block.get("body", "")
        )
        msg_type = content_block.get("type", "text")
        if msg_type not in ("text", "chat") and not text:
            text = f"[{msg_type}] Mensagem não textual recebida"
        if text:
            return text.strip()

    # ── Formato antigo Tallos v1 ──────────────────────────────────────
    # {"message": {"content": "...", "text": "..."}}
    message = data.get("message", {})
    if isinstance(message, dict):
        text = (
            message.get("content", "")
            or message.get("text", "")
            or message.get("body", "")
            or message.get("message", "")
        )
        msg_type = message.get("type", "text")
        if msg_type not in ("text", "chat") and not text:
            text = f"[{msg_type}] Mensagem não textual recebida"
        return (text or "").strip()

    if isinstance(message, str) and message:
        return message.strip()

    return (data.get("text", "") or data.get("body", "") or "").strip()


def extract_name_from_payload(data: dict) -> str:
    """Extrai o nome do contato do payload do webhook Tallos."""
    contact = data.get("contact", {})
    if isinstance(contact, dict):
        return (
            contact.get("full_name", "")
            or contact.get("name", "")
            or contact.get("firstName", "")
            or ""
        )
    return data.get("contact_name", "") or ""


def is_agent_message(data: dict) -> bool:
    """
    Retorna True se a mensagem foi enviada por um atendente (não pelo lead).

    Suporta dois formatos:
      Novo (RD Conversas): verifica "agent" no bloco top-level ou content.sent_by
      Antigo (Tallos v1):  verifica sent_by='operator'/'agent'/'system'
    """
    # Novo formato: presença do bloco "agent" indica mensagem do atendente
    if isinstance(data.get("agent"), dict) and data["agent"]:
        return True

    # Novo formato: content.sent_by ou content.origin
    content_block = data.get("content", {})
    if isinstance(content_block, dict):
        sent_by = content_block.get("sent_by", "") or content_block.get("origin", "")
        if sent_by in ("operator", "agent", "attendant"):
            return True

    # Formato antigo
    sent_by    = data.get("sent_by", "")
    created_by = data.get("created_by", "")
    origin     = data.get("origin", "")
    return any(v in ("operator", "agent", "system") for v in (sent_by, created_by, origin))


# Instância singleton
tallos_service = TallosService()
