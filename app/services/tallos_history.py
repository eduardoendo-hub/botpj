"""
Serviço de histórico de conversas via API Tallos v2.

A API Tallos retorna as mensagens criptografadas com RSA-OAEP-256.
A chave JWK (pública + privada) é necessária para descriptografar.

Configuração:
  - TALLOS_API_TOKEN: token JWT da API Tallos
  - TALLOS_JWK_KEY: chave JWK completa em JSON string (no .env)
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

import httpx
from jwcrypto import jwk, jwe

from app.core.config import settings

logger = logging.getLogger(__name__)

_BRT = timezone(timedelta(hours=-3))

# ── Chave JWK (carregada uma vez do settings) ─────────────────────────────────

_jwk_key: Optional[jwk.JWK] = None


def _get_jwk_key() -> Optional[jwk.JWK]:
    global _jwk_key
    if _jwk_key is not None:
        return _jwk_key
    raw = getattr(settings, "tallos_jwk_key", "") or ""
    if not raw:
        logger.warning("[TallosHistory] TALLOS_JWK_KEY não configurado — histórico desativado")
        return None
    try:
        key_dict = json.loads(raw)
        _jwk_key = jwk.JWK(**key_dict)
        logger.info("[TallosHistory] Chave JWK carregada com sucesso")
        return _jwk_key
    except Exception as e:
        logger.error(f"[TallosHistory] Erro ao carregar JWK: {e}")
        return None


# ── Descriptografia ───────────────────────────────────────────────────────────

def _decrypt(encrypted_str: str) -> List[Dict]:
    """Descriptografa o campo messages retornado pela API Tallos."""
    key = _get_jwk_key()
    if not key:
        return []
    token = jwe.JWE()
    token.deserialize(encrypted_str, key=key)
    return json.loads(token.payload.decode("latin-1"), strict=False)


# ── Formatação ────────────────────────────────────────────────────────────────

def _fmt_datetime(iso_str: str) -> str:
    """Formata timestamp ISO para dd/mm/yyyy HH:MM no horário de Brasília."""
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_BRT).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return iso_str[:16]


def _normalize_message(m: Dict) -> Dict:
    """Normaliza uma mensagem da API Tallos para o formato do Radar."""
    sent_by = m.get("sent_by", "")
    # Tallos retorna o texto no campo "message"; fallback para "content"
    content = m.get("message", m.get("content", "")) or ""

    # Operadores vêm com o nome no início: "*Nome Operador:*\nTexto"
    operator_name = ""
    text = content
    if sent_by == "operator" and content.startswith("*"):
        # Formato: "*Celia Maciel:*\nOlá!" → split em "*\n"
        parts = content.split("*\n", 1)
        if len(parts) == 2:
            # parts[0] = "*Celia Maciel:" → remove * e : das extremidades
            operator_name = parts[0].strip("*").rstrip(":").strip()
            text = parts[1].strip()
        else:
            # Tenta formato "*Celia Maciel:* Texto"
            import re
            m2 = re.match(r"^\*([^*]+?)\*[:\s]+(.*)", content, re.DOTALL)
            if m2:
                operator_name = m2.group(1).rstrip(":").strip()
                text = m2.group(2).strip()

    return {
        "role":          sent_by,           # "customer", "operator", "bot"
        "message":       text,
        "operator_name": operator_name,
        "channel":       m.get("channel", "whatsapp"),
        "created_at":    m.get("created_at", ""),
        "hora":          _fmt_datetime(m.get("created_at", "")),
        "status":        m.get("status", ""),
        "is_template":   m.get("is_template_message", False),
        "source":        "tallos",
    }


# ── Busca principal ───────────────────────────────────────────────────────────

async def get_conversation_history(
    customer_id: str,
    page: int = 1,
    limit: int = 50,
    sent_by: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Busca e descriptografa o histórico de conversas de um contato no Tallos.

    Args:
        customer_id: ID do contato no Tallos (field contact_id / _id)
        page: página da listagem (começa em 1)
        limit: registros por página (max 100)
        sent_by: filtro por remetente ["customer", "operator", "bot"]

    Returns:
        {"messages": [...], "total": int, "page": int, "has_more": bool}
    """
    if not customer_id:
        return {"messages": [], "total": 0, "page": page, "has_more": False}

    if not settings.tallos_api_token:
        logger.warning("[TallosHistory] TALLOS_API_TOKEN não configurado")
        return {"messages": [], "total": 0, "page": page, "has_more": False}

    if sent_by is None:
        sent_by = ["customer", "operator"]

    url = f"{settings.tallos_api_url}/messages/history"
    headers = {"Authorization": f"Bearer {settings.tallos_api_token}"}
    params = {
        "customer_id": customer_id,
        "limit":       min(limit, 100),
        "page":        page,
        "sent_by":     sent_by,
        "type":        ["text"],
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers, params=params)

        if resp.status_code != 200:
            logger.error(f"[TallosHistory] API retornou {resp.status_code}: {resp.text[:200]}")
            return {"messages": [], "total": 0, "page": page, "has_more": False, "error": resp.text[:200]}

        data = resp.json()
        encrypted = data.get("messages", "")
        if not encrypted:
            return {"messages": [], "total": 0, "page": page, "has_more": False}

        raw_messages = _decrypt(encrypted)
        messages = [_normalize_message(m) for m in raw_messages]

        # Ordena por data crescente (mais antigas primeiro)
        messages.sort(key=lambda x: x.get("created_at", ""))

        logger.info(
            f"[TallosHistory] ✅ customer_id={customer_id} | "
            f"page={page} | {len(messages)} mensagem(ns)"
        )

        return {
            "messages": messages,
            "total":    len(messages),
            "page":     page,
            "has_more": len(messages) >= limit,
        }

    except Exception as e:
        logger.error(f"[TallosHistory] Erro ao buscar histórico: {e}", exc_info=True)
        return {"messages": [], "total": 0, "page": page, "has_more": False, "error": str(e)}


def extract_customer_id_from_notes(notes: str) -> str:
    """Extrai o tallos_contact_id do campo notes do lead."""
    if not notes:
        return ""
    for part in notes.split(";"):
        part = part.strip()
        if part.startswith("tallos_contact_id:"):
            return part.replace("tallos_contact_id:", "").strip()
    return ""
