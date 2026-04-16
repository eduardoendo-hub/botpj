"""
Serviço de integração com RD Station CRM.

Fluxo para buscar etapa do funil de um lead:
  1. Busca contato pelo telefone  → GET /contacts?phone={phone}
  2. Pega o deal mais recente     → GET /deals?contact_id={id}
  3. Retorna deal_stage.name      → etapa do funil

Autenticação: token via query param (?token=...)
Base URL: https://crm.rdstation.com/api/v1
"""

import logging
from typing import Optional, Dict, Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 8  # segundos


def _headers() -> Dict[str, str]:
    return {"Accept": "application/json"}


def _params(extra: Optional[Dict] = None) -> Dict:
    p = {"token": settings.rd_crm_token}
    if extra:
        p.update(extra)
    return p


async def get_funil_etapa(phone: str) -> str:
    """
    Retorna a etapa do funil do RD CRM para o telefone informado.
    Retorna "—" se não encontrar ou em caso de erro.
    """
    if not settings.rd_crm_token:
        return "—"

    phone_clean = _clean_phone(phone)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            # 1. Busca contato pelo telefone
            contact_id = await _find_contact_id(client, phone_clean)
            if not contact_id:
                logger.debug(f"[RD CRM] Contato não encontrado para phone={phone_clean}")
                return "—"

            # 2. Busca deals do contato
            etapa = await _find_deal_stage(client, contact_id)
            logger.debug(f"[RD CRM] phone={phone_clean} | contact_id={contact_id} | etapa={etapa}")
            return etapa or "—"

    except httpx.TimeoutException:
        logger.warning(f"[RD CRM] Timeout ao buscar funil para phone={phone}")
        return "—"
    except Exception as e:
        logger.error(f"[RD CRM] Erro ao buscar funil para phone={phone}: {e}")
        return "—"


async def _find_contact_id(client: httpx.AsyncClient, phone: str) -> Optional[str]:
    """Busca o _id do contato no RD CRM pelo telefone."""
    # Tenta com e sem código do país
    for phone_variant in _phone_variants(phone):
        resp = await client.get(
            f"{settings.rd_crm_url}/contacts",
            headers=_headers(),
            params=_params({"phone": phone_variant}),
        )
        if resp.status_code != 200:
            continue

        data = resp.json()
        contacts = data if isinstance(data, list) else data.get("contacts", [])
        if contacts:
            return contacts[0].get("_id") or contacts[0].get("id")

    return None


async def _find_deal_stage(client: httpx.AsyncClient, contact_id: str) -> Optional[str]:
    """Busca a etapa do deal mais recente (não perdido) de um contato."""
    resp = await client.get(
        f"{settings.rd_crm_url}/deals",
        headers=_headers(),
        params=_params({"contact_id": contact_id, "order": "updated_at", "page": 1, "limit": 10}),
    )
    if resp.status_code != 200:
        logger.warning(f"[RD CRM] GET /deals retornou {resp.status_code}")
        return None

    data = resp.json()
    deals = data if isinstance(data, list) else data.get("deals", [])

    if not deals:
        return None

    # Prioriza deals em aberto (não fechados/perdidos)
    for deal in deals:
        stage = deal.get("deal_stage", {})
        stage_name = stage.get("name", "") if isinstance(stage, dict) else str(stage)
        if "perdido" not in stage_name.lower() and "fechado" not in stage_name.lower():
            return stage_name

    # Se todos fechados, retorna o mais recente mesmo assim
    stage = deals[0].get("deal_stage", {})
    return stage.get("name", "—") if isinstance(stage, dict) else str(stage)


def _clean_phone(phone: str) -> str:
    """Remove espaços, hífens, parênteses e formatação."""
    import re
    return re.sub(r"[^\d]", "", phone)


def _phone_variants(phone: str) -> list:
    """Gera variantes do telefone para busca (com/sem código do país)."""
    variants = [phone]
    if phone.startswith("55") and len(phone) >= 12:
        variants.append(phone[2:])   # sem DDI 55
    elif len(phone) <= 11:
        variants.append("55" + phone)  # com DDI 55
    return variants
