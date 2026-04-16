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


_EMPTY = {"etapa": "—", "consultor": "", "valor": 0.0}


async def get_deal_info(phone: str) -> Dict:
    """
    Retorna info do deal ativo no RD CRM para o telefone:
    {"etapa": str, "consultor": str, "valor": float}
    """
    if not settings.rd_crm_token:
        return _EMPTY.copy()

    phone_clean = _clean_phone(phone)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            contact_id = await _find_contact_id(client, phone_clean)
            if not contact_id:
                return _EMPTY.copy()

            deal = await _find_latest_deal(client, contact_id)
            if not deal:
                return _EMPTY.copy()

            stage = deal.get("deal_stage", {})
            etapa = stage.get("name", "—") if isinstance(stage, dict) else str(stage or "—")

            user = deal.get("user", {}) or {}
            consultor = user.get("name", "") if isinstance(user, dict) else ""

            valor = float(deal.get("amount_total") or deal.get("amount_unique") or 0)

            logger.info(f"[RD CRM] {phone_clean} → {etapa} | {consultor} | R${valor:.2f}")
            return {"etapa": etapa, "consultor": consultor, "valor": valor}

    except httpx.TimeoutException:
        logger.warning(f"[RD CRM] Timeout para phone={phone}")
        return _EMPTY.copy()
    except Exception as e:
        logger.error(f"[RD CRM] Erro para phone={phone}: {e}")
        return _EMPTY.copy()


# Mantém compatibilidade com código legado
async def get_funil_etapa(phone: str) -> str:
    info = await get_deal_info(phone)
    return info["etapa"]


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

        def _is_lost(d: Dict) -> bool:
            if d.get("win") is True:
                return False  # ganho, não perdido
            if d.get("deal_lost_reason"):
                return True   # tem razão de perda → perdido
            stage_name = ""
            s = d.get("deal_stage", {})
            if isinstance(s, dict):
                stage_name = s.get("name", "").lower()
            return "perdido" in stage_name or "lost" in stage_name

        # Prioriza deals em aberto (não perdidos e não ganhos fechados)
        abertos = [d for d in deals if not _is_lost(d) and not d.get("win")]
        if abertos:
            return abertos[0]

        # Fallback: ganhos
        ganhos = [d for d in deals if d.get("win")]
        if ganhos:
            return ganhos[0]

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
