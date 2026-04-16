"""
Serviço de integração com RD Station CRM (API v1).

Autenticação simples via token de API (sem OAuth2).
Token disponível em: CRM → avatar → Integrações → Token API

Fluxo para buscar etapa do funil:
  1. Busca contato pelo telefone  → GET /contacts?phone={phone}&token={token}
  2. Pega o deal mais recente     → GET /deals?contact_id={id}&token={token}
  3. Retorna deal_stage.name      → etapa do funil
"""

import logging
import re
from typing import Optional, Dict, List

import httpx

from app.core.config import settings

logger  = logging.getLogger(__name__)
_BASE   = "https://crm.rdstation.com/api/v1"
_TIMEOUT = 8

# Cache stage_id → nome para evitar requisições repetidas por sessão
_stage_cache: Dict[str, str] = {}


def _p(extra: Optional[Dict] = None) -> Dict:
    """Monta params com token."""
    p = {"token": settings.rd_crm_token}
    if extra:
        p.update(extra)
    return p


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
                logger.debug(f"[RD CRM] Contato não encontrado: {phone_clean}")
                return "—"

            deal = await _find_latest_deal(client, contact_id)
            if not deal:
                return "—"

            # deal_stage pode ser dict {id, name} ou string
            stage = deal.get("deal_stage", {})
            if isinstance(stage, dict):
                etapa = stage.get("name", "—")
            else:
                etapa = str(stage) if stage else "—"

            logger.info(f"[RD CRM] {phone_clean} → {etapa}")
            return etapa

    except httpx.TimeoutException:
        logger.warning(f"[RD CRM] Timeout para phone={phone}")
        return "—"
    except Exception as e:
        logger.error(f"[RD CRM] Erro para phone={phone}: {e}")
        return "—"


async def _find_contact_id(client: httpx.AsyncClient, phone: str) -> Optional[str]:
    """Busca _id do contato pelo telefone."""
    for variant in _phone_variants(phone):
        try:
            resp = await client.get(
                f"{_BASE}/contacts",
                params=_p({"phone": variant}),
            )
            if resp.status_code != 200:
                continue
            data     = resp.json()
            contacts = data.get("contacts", data) if isinstance(data, dict) else data
            if contacts and isinstance(contacts, list):
                cid = contacts[0].get("_id") or contacts[0].get("id")
                if cid:
                    return cid
        except Exception:
            continue
    return None


async def _find_latest_deal(client: httpx.AsyncClient, contact_id: str) -> Optional[Dict]:
    """Busca o deal mais recente (não perdido) de um contato."""
    try:
        resp = await client.get(
            f"{_BASE}/deals",
            params=_p({"contact_id": contact_id, "order": "updated_at", "page": 1}),
        )
        if resp.status_code != 200:
            logger.warning(f"[RD CRM] GET /deals → {resp.status_code}: {resp.text[:100]}")
            return None

        data  = resp.json()
        deals: List[Dict] = data.get("deals", data) if isinstance(data, dict) else data

        if not deals:
            return None

        # Prioriza deals em aberto
        for deal in deals:
            stage = deal.get("deal_stage", {})
            name  = stage.get("name", "") if isinstance(stage, dict) else str(stage)
            if "perdido" not in name.lower() and "lost" not in name.lower():
                return deal

        return deals[0]

    except Exception as e:
        logger.error(f"[RD CRM] Erro em _find_latest_deal: {e}")
        return None


def _clean_phone(phone: str) -> str:
    return re.sub(r"[^\d]", "", phone)


def _phone_variants(phone: str) -> List[str]:
    variants = [phone]
    if phone.startswith("55") and len(phone) >= 12:
        variants.append(phone[2:])   # sem DDI
    elif len(phone) <= 11:
        variants.append("55" + phone)  # com DDI
    return variants
