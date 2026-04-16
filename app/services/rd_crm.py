"""
Serviço de integração com RD Station CRM (API v2).

Fluxo para buscar etapa do funil de um lead:
  1. Busca contato pelo telefone  → GET /contacts?phone={phone}
  2. Pega o deal mais recente     → GET /deals?contact_id={id}
  3. Resolve stage_id → nome      → GET /pipeline_stages/{id}
  4. Retorna nome da etapa        → exibido no Radar

Base URL: https://api.rd.services/crm/v2
Auth: Authorization: Bearer {token}
"""

import logging
import re
from typing import Optional, Dict, List

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 8
_BASE    = "https://api.rd.services/crm/v2"

# Cache simples de stage_id → nome (evita requisições repetidas)
_stage_cache: Dict[str, str] = {}


def _auth_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.rd_crm_token}",
        "Accept":        "application/json",
    }


async def get_funil_etapa(phone: str) -> str:
    """
    Retorna a etapa do funil do RD Station CRM para o telefone informado.
    Retorna "—" se não encontrar ou em caso de erro.
    """
    if not settings.rd_crm_token:
        return "—"

    phone_clean = _clean_phone(phone)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            contact_id = await _find_contact_id(client, phone_clean)
            if not contact_id:
                logger.debug(f"[RD CRM] Contato não encontrado: phone={phone_clean}")
                return "—"

            deal = await _find_latest_deal(client, contact_id)
            if not deal:
                logger.debug(f"[RD CRM] Sem deals para contact_id={contact_id}")
                return "—"

            stage_id   = deal.get("stage_id", "")
            stage_name = await _resolve_stage_name(client, stage_id)
            logger.info(f"[RD CRM] phone={phone_clean} | stage={stage_name}")
            return stage_name or "—"

    except httpx.TimeoutException:
        logger.warning(f"[RD CRM] Timeout para phone={phone}")
        return "—"
    except Exception as e:
        logger.error(f"[RD CRM] Erro para phone={phone}: {e}")
        return "—"


async def _find_contact_id(client: httpx.AsyncClient, phone: str) -> Optional[str]:
    """Busca _id do contato pelo telefone (tenta variantes com/sem DDI)."""
    for variant in _phone_variants(phone):
        try:
            resp = await client.get(
                f"{_BASE}/contacts",
                headers=_auth_headers(),
                params={"phone": variant, "page": 1, "page_size": 5},
            )
            if resp.status_code != 200:
                continue
            data     = resp.json()
            contacts = data.get("contacts", data) if isinstance(data, dict) else data
            if contacts and isinstance(contacts, list):
                return contacts[0].get("id") or contacts[0].get("_id")
        except Exception:
            continue
    return None


async def _find_latest_deal(client: httpx.AsyncClient, contact_id: str) -> Optional[Dict]:
    """Busca o deal mais recente (não perdido) de um contato."""
    try:
        resp = await client.get(
            f"{_BASE}/deals",
            headers=_auth_headers(),
            params={"contact_id": contact_id, "page": 1, "page_size": 10},
        )
        if resp.status_code != 200:
            logger.warning(f"[RD CRM] GET /deals → {resp.status_code}")
            return None

        data  = resp.json()
        deals: List[Dict] = data.get("deals", data) if isinstance(data, dict) else data

        if not deals:
            return None

        # Prioriza deals em aberto (status != lost/won)
        for deal in deals:
            if deal.get("status") not in ("lost", "won"):
                return deal

        # Fallback: retorna o mais recente mesmo que fechado
        return deals[0]

    except Exception as e:
        logger.error(f"[RD CRM] Erro em _find_latest_deal: {e}")
        return None


async def _resolve_stage_name(client: httpx.AsyncClient, stage_id: str) -> str:
    """Resolve stage_id → nome da etapa (com cache)."""
    if not stage_id:
        return "—"
    if stage_id in _stage_cache:
        return _stage_cache[stage_id]

    try:
        resp = await client.get(
            f"{_BASE}/pipeline_stages/{stage_id}",
            headers=_auth_headers(),
        )
        if resp.status_code == 200:
            data = resp.json()
            stage = data.get("data", data)
            name  = stage.get("name", "—")
            _stage_cache[stage_id] = name
            return name
    except Exception as e:
        logger.warning(f"[RD CRM] Não resolveu stage_id={stage_id}: {e}")

    return stage_id   # fallback: retorna o ID bruto


def _clean_phone(phone: str) -> str:
    return re.sub(r"[^\d]", "", phone)


def _phone_variants(phone: str) -> List[str]:
    variants = [phone]
    if phone.startswith("55") and len(phone) >= 12:
        variants.append(phone[2:])
    elif len(phone) <= 11:
        variants.append("55" + phone)
    return variants
