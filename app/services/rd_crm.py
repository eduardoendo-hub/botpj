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
            contact = await _find_contact(client, phone_clean)
            if not contact:
                return _EMPTY.copy()

            deal_ids = contact.get("deal_ids", []) or []
            if not deal_ids:
                return _EMPTY.copy()

            deal = await _find_best_deal(client, deal_ids)
            if not deal:
                return _EMPTY.copy()

            stage = deal.get("deal_stage", {})
            etapa = stage.get("name", "—") if isinstance(stage, dict) else str(stage or "—")

            user = deal.get("user", {}) or {}
            consultor = user.get("name", "") if isinstance(user, dict) else ""

            valor = float(deal.get("amount_total") or deal.get("amount_unique") or 0)

            deal_name     = deal.get("name") or ""
            deal_products = deal.get("deal_products") or []
            logger.info(f"[RD CRM] {phone_clean} → {etapa} | {consultor} | R${valor:.2f} | {deal_name}")
            return {
                "etapa":         etapa,
                "consultor":     consultor,
                "valor":         valor,
                "deal_name":     deal_name,
                "deal_products": deal_products,
            }

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


async def get_deal_full_info(phone: str) -> Dict:
    """
    Retorna dados completos do deal para exibição no histórico:
    {
      "pipeline": str,
      "etapa": str,
      "consultor": str,
      "valor": float,
      "next_task": dict | None,
      "previous_task": dict | None,
      "stage_histories": list[dict],  # [{stage_name, start_date, end_date}]
    }
    """
    empty = {
        "pipeline": "", "etapa": "—", "consultor": "", "valor": 0.0,
        "next_task": None, "previous_task": None, "stage_histories": [],
    }
    if not settings.rd_crm_token:
        return empty

    phone_clean = _clean_phone(phone)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            contact = await _find_contact(client, phone_clean)
            if not contact:
                return empty

            deal_ids = contact.get("deal_ids", []) or []
            if not deal_ids:
                return empty

            deal = await _find_best_deal(client, deal_ids)
            if not deal:
                return empty

            # Etapa atual
            stage = deal.get("deal_stage", {})
            etapa = stage.get("name", "—") if isinstance(stage, dict) else str(stage or "—")

            # Pipeline
            pipeline_obj = deal.get("deal_pipeline", {}) or {}
            pipeline = pipeline_obj.get("name", "") if isinstance(pipeline_obj, dict) else ""

            # Consultor
            user = deal.get("user", {}) or {}
            consultor = user.get("name", "") if isinstance(user, dict) else ""

            # Valor
            valor = float(deal.get("amount_total") or deal.get("amount_unique") or 0)

            # Próxima tarefa e última tarefa do objeto do deal
            next_task = deal.get("next_task") or None
            prev_task = deal.get("previous_task") or None

            # Histórico de etapas — resolve stage_id → nome
            histories_raw = deal.get("deal_stage_histories") or []
            stage_names = await _fetch_stage_names(client)
            stage_histories = []
            for h in histories_raw:
                sid = h.get("deal_stage_id", "")
                stage_histories.append({
                    "stage_name": stage_names.get(sid, f"Etapa {sid[:8]}…" if sid else "—"),
                    "start_date": h.get("start_date") or "",
                    "end_date":   h.get("end_date") or "",
                })

            # Atividades do deal via /activities (único endpoint que retorna conteúdo)
            deal_id        = deal.get("_id") or deal.get("id") or ""
            all_activities = await _fetch_deal_activities(client, deal_id)

            # Nome e produtos da negociação
            deal_name     = deal.get("name") or ""
            deal_products = deal.get("deal_products") or []

            return {
                "pipeline":        pipeline,
                "etapa":           etapa,
                "consultor":       consultor,
                "valor":           valor,
                "next_task":       next_task,
                "previous_task":   prev_task,
                "stage_histories": stage_histories,
                "all_activities":  all_activities,
                "deal_name":       deal_name,
                "deal_products":   deal_products,
            }

    except httpx.TimeoutException:
        logger.warning(f"[RD CRM] Timeout get_deal_full_info phone={phone}")
        return empty
    except Exception as e:
        logger.error(f"[RD CRM] Erro get_deal_full_info phone={phone}: {e}")
        return empty


async def _fetch_deal_activities(client: httpx.AsyncClient, deal_id: str) -> List[Dict]:
    """
    Busca atividades do deal via GET /activities?deal_id= (único endpoint funcional).
    Cada item tem: _id, date, deal_id, user_id, text (conteúdo completo da atividade).
    Ordena por date desc.
    """
    if not deal_id:
        return []
    try:
        resp = await client.get(f"{_BASE}/activities", params=_p({"deal_id": deal_id}))
        if resp.status_code != 200:
            logger.warning(f"[RD CRM] /activities retornou {resp.status_code} deal {deal_id}")
            return []
        data = resp.json()
        items = data if isinstance(data, list) else data.get("activities", [])
        items.sort(key=lambda x: x.get("date") or "", reverse=True)
        return items
    except Exception as e:
        logger.warning(f"[RD CRM] Falha ao buscar activities do deal {deal_id}: {e}")
        return []


async def _fetch_stage_names(client: httpx.AsyncClient) -> Dict[str, str]:
    """Busca todas as etapas e retorna {id: name}. Usa cache de sessão."""
    global _stage_cache
    if _stage_cache:
        return _stage_cache
    try:
        resp = await client.get(f"{_BASE}/deal_stages", params=_p())
        if resp.status_code == 200:
            data = resp.json()
            stages = data if isinstance(data, list) else data.get("deal_stages", [])
            for s in stages:
                sid = s.get("_id") or s.get("id") or ""
                name = s.get("name") or ""
                if sid and name:
                    _stage_cache[sid] = name
    except Exception as e:
        logger.warning(f"[RD CRM] Falha ao buscar stage_names: {e}")
    return _stage_cache


async def _find_contact(client: httpx.AsyncClient, phone: str) -> Optional[Dict]:
    """Busca o contato pelo telefone e retorna o objeto completo (com deal_ids)."""
    for variant in _phone_variants(phone):
        try:
            resp = await client.get(f"{_BASE}/contacts", params=_p({"phone": variant}))
            if resp.status_code != 200:
                continue
            data     = resp.json()
            contacts = data.get("contacts", data) if isinstance(data, dict) else data
            if contacts and isinstance(contacts, list):
                # Retorna o objeto completo do primeiro contato encontrado
                cid = contacts[0].get("_id") or contacts[0].get("id")
                if not cid:
                    continue
                # Busca o contato completo para ter deal_ids
                r2 = await client.get(f"{_BASE}/contacts/{cid}", params=_p())
                if r2.status_code == 200:
                    return r2.json()
                return contacts[0]
        except Exception:
            continue
    return None


async def _find_best_deal(client: httpx.AsyncClient, deal_ids: List[str]) -> Optional[Dict]:
    """Busca os deals pelos IDs e retorna o melhor (em aberto, mais recente)."""
    deals = []
    for deal_id in deal_ids[:5]:  # limita a 5 para não sobrecarregar
        try:
            resp = await client.get(f"{_BASE}/deals/{deal_id}", params=_p())
            if resp.status_code == 200:
                deals.append(resp.json())
        except Exception:
            continue

    if not deals:
        return None

    def _is_lost(d: Dict) -> bool:
        if d.get("win") is True:
            return False
        if d.get("deal_lost_reason"):
            return True
        s = d.get("deal_stage", {})
        name = s.get("name", "").lower() if isinstance(s, dict) else ""
        return "perdido" in name or "lost" in name

    abertos = [d for d in deals if not _is_lost(d) and not d.get("win")]
    if abertos:
        return abertos[0]

    ganhos = [d for d in deals if d.get("win")]
    if ganhos:
        return ganhos[0]

    return deals[0]


def _clean_phone(phone: str) -> str:
    return re.sub(r"[^\d]", "", phone)


def _phone_variants(phone: str) -> List[str]:
    variants = [phone]
    if phone.startswith("55") and len(phone) >= 12:
        variants.append(phone[2:])   # sem DDI
    elif len(phone) <= 11:
        variants.append("55" + phone)  # com DDI
    return variants


# ─────────────────────────────────────────────────────────────────────────────
# SYNC: RD CRM → tabela de leads
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_phone(raw: str) -> str:
    """Normaliza telefone para formato 55XXXXXXXXXXX."""
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return ""
    if len(digits) in (10, 11) and not digits.startswith("55"):
        return "55" + digits
    if len(digits) == 13 and digits.startswith("55"):
        return digits
    if len(digits) == 12 and digits.startswith("55"):
        return digits
    return digits


async def get_deals_by_date(date_iso: str, pipeline_id: str) -> List[Dict]:
    """
    Retorna todos os deals de um pipeline criados em date_iso (YYYY-MM-DD).
    Usa a API v1 com created_at_period + start_date/end_date.
    """
    if not settings.rd_crm_token:
        return []

    start = f"{date_iso}T00:00:00"
    end   = f"{date_iso}T23:59:59"

    query = {
        "token":              settings.rd_crm_token,
        "created_at_period":  "true",
        "start_date":         start,
        "end_date":           end,
        "deal_pipeline_id":   pipeline_id,
        "limit":              200,
        "page":               1,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{_BASE}/deals", params=query)
            if resp.status_code != 200:
                logger.warning(f"[RD CRM sync] GET /deals retornou {resp.status_code}")
                return []
            data = resp.json()
            deals = data if isinstance(data, list) else data.get("deals", [])
            logger.info(f"[RD CRM sync] {len(deals)} deal(s) encontrados para {date_iso}")
            return deals
    except Exception as e:
        logger.error(f"[RD CRM sync] Erro ao buscar deals: {e}")
        return []


async def _get_contact_phone_from_deal(client: httpx.AsyncClient, deal: Dict) -> tuple[str, str]:
    """
    Extrai (phone, contact_id) do deal.
    Tenta o campo 'contacts' embutido; se não tiver, busca via GET /contacts?deal_id=.
    Retorna ("", "") se não encontrar.
    """
    deal_id = deal.get("_id") or deal.get("id") or ""

    # 1. Tenta campo contacts embutido no deal
    contacts_raw = deal.get("contacts") or []
    if isinstance(contacts_raw, list) and contacts_raw:
        for c in contacts_raw:
            if isinstance(c, dict):
                cid = c.get("_id") or c.get("id") or ""
                phones = c.get("phones") or []
                for ph in phones:
                    raw = ph.get("phone", "") if isinstance(ph, dict) else str(ph)
                    normalized = _normalize_phone(raw)
                    if normalized:
                        return normalized, cid
            elif isinstance(c, str):
                # c é apenas o ID — busca o contato
                try:
                    r = await client.get(f"{_BASE}/contacts/{c}", params={"token": settings.rd_crm_token})
                    if r.status_code == 200:
                        contact = r.json()
                        cid = contact.get("_id") or c
                        for ph in (contact.get("phones") or []):
                            raw = ph.get("phone", "") if isinstance(ph, dict) else str(ph)
                            normalized = _normalize_phone(raw)
                            if normalized:
                                return normalized, cid
                except Exception:
                    pass

    # 2. Fallback: GET /contacts?deal_id=
    if deal_id:
        try:
            r = await client.get(
                f"{_BASE}/contacts",
                params={"token": settings.rd_crm_token, "deal_id": deal_id}
            )
            if r.status_code == 200:
                data = r.json()
                contacts = data.get("contacts", data) if isinstance(data, dict) else data
                if isinstance(contacts, list) and contacts:
                    c = contacts[0]
                    cid = c.get("_id") or c.get("id") or ""
                    for ph in (c.get("phones") or []):
                        raw = ph.get("phone", "") if isinstance(ph, dict) else str(ph)
                        normalized = _normalize_phone(raw)
                        if normalized:
                            return normalized, cid
        except Exception:
            pass

    return "", ""


async def _backfill_webhook_messages(phone: str, days: int = 3) -> None:
    """
    Varre webhook_logs dos últimos `days` dias buscando mensagens deste telefone
    e as grava na tabela conversations (apenas as que ainda não existem).
    """
    import json as _json
    from app.core.database import get_db, save_message_external

    try:
        db = await get_db()
        try:
            cursor = await db.execute(
                """SELECT raw_payload, created_at FROM webhook_logs
                   WHERE phone_number = ?
                     AND created_at >= datetime('now', ? || ' days')
                   ORDER BY created_at""",
                (phone, f"-{days}")
            )
            rows = await cursor.fetchall()
        finally:
            await db.close()

        count = 0
        for row in rows:
            try:
                payload = _json.loads(row["raw_payload"] or "{}")
                content = payload.get("content", {})
                if not isinstance(content, dict):
                    continue
                message = content.get("message", "").strip()
                action  = content.get("action", "")
                msg_id  = content.get("id", "")
                if not message or action == "automation":
                    continue

                # Role: agent_message = atendente, on_attendance = lead (user)
                payload_event = (payload.get("event") or payload.get("action") or action or "")
                role = "agent" if "agent" in payload_event.lower() else "user"

                saved = await save_message_external(
                    phone_number=phone,
                    role=role,
                    message=message,
                    external_id=msg_id,
                    created_at=str(row["created_at"]),
                )
                if saved:
                    count += 1
            except Exception:
                continue

        if count:
            logger.info(f"[RD CRM sync] Backfill: {count} mensagem(ns) importadas para {phone}")
    except Exception as e:
        logger.error(f"[RD CRM sync] Erro no backfill de mensagens para {phone}: {e}")


async def sync_pipeline_deals_to_leads(date_iso: str, pipeline_id: str) -> int:
    """
    Sincroniza oportunidades do funil pipeline_id criadas em date_iso
    com a tabela de leads interna.

    Para cada deal do CRM:
      - Extrai o telefone do contato associado
      - Se o lead já existe na tabela → ignora (ou atualiza deal_id se faltando)
      - Se não existe → cria o lead com source_channel='tallos_crm_sync'
      - Faz backfill das mensagens dos últimos 3 dias do webhook_logs

    Retorna o número de leads novos importados.
    """
    from app.core.database import get_lead_by_phone, upsert_lead, upsert_bot_session

    if not settings.rd_crm_token:
        return 0

    deals = await get_deals_by_date(date_iso, pipeline_id)
    if not deals:
        return 0

    imported = 0

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for deal in deals:
                deal_id   = deal.get("_id") or deal.get("id") or ""
                deal_name = deal.get("name") or ""

                # Extrai nome e etapa do deal
                stage_obj = deal.get("deal_stage", {}) or {}
                etapa     = stage_obj.get("name", "") if isinstance(stage_obj, dict) else ""
                user_obj  = deal.get("user", {}) or {}
                consultor = user_obj.get("name", "") if isinstance(user_obj, dict) else ""

                phone, contact_id = await _get_contact_phone_from_deal(client, deal)
                if not phone:
                    logger.debug(f"[RD CRM sync] Deal {deal_id} ({deal_name}) sem telefone — ignorado")
                    continue

                existing = await get_lead_by_phone(phone)

                if existing:
                    # Lead já existe — apenas garante que o deal_id está salvo
                    if not existing.get("rd_crm_deal_id") and deal_id:
                        await upsert_lead(phone, rd_crm_deal_id=deal_id)
                        logger.debug(f"[RD CRM sync] deal_id atualizado para {phone}")
                    continue

                # Lead novo — importa do CRM
                logger.info(
                    f"[RD CRM sync] ✅ Importando lead CRM | phone={phone} | "
                    f"deal={deal_name!r} | etapa={etapa} | consultor={consultor}"
                )

                # Nome: tenta extrair do deal_name (ex: "Fernanda Fonseca - Power Automate")
                nome = deal_name.split(" - ")[0].strip() if " - " in deal_name else deal_name

                notes = f"tallos_contact_id:{contact_id}" if contact_id else ""

                # Preserva a data original do deal no CRM (para o Radar filtrar corretamente)
                crm_created_at = deal.get("created_at") or ""
                # Converte "2026-04-17T10:51:24.460-03:00" → "2026-04-17 13:51:24" (UTC para SQLite)
                crm_created_utc = ""
                if crm_created_at:
                    try:
                        from datetime import datetime, timezone
                        dt = datetime.fromisoformat(crm_created_at.replace("Z", "+00:00"))
                        crm_created_utc = dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        crm_created_utc = ""

                await upsert_lead(
                    phone,
                    contact_name=nome,
                    training_interest=deal_name,
                    source_channel="tallos_crm_sync",
                    rd_crm_deal_id=deal_id,
                    notes=notes,
                    stage="novo",
                )

                # Ajusta o created_at para a data original do deal (para filtro de data no Radar)
                if crm_created_utc:
                    from app.core.database import get_db as _get_db
                    _db = await _get_db()
                    try:
                        await _db.execute(
                            "UPDATE leads SET created_at=? WHERE phone_number=?",
                            (crm_created_utc, phone)
                        )
                        await _db.commit()
                    finally:
                        await _db.close()

                await upsert_bot_session(phone, agent_active=0)

                # Backfill das conversas dos últimos 3 dias
                await _backfill_webhook_messages(phone, days=3)

                imported += 1

    except Exception as e:
        logger.error(f"[RD CRM sync] Erro geral na sincronização: {e}", exc_info=True)

    if imported:
        logger.info(f"[RD CRM sync] {imported} lead(s) novo(s) importado(s) do CRM para {date_iso}")

    return imported
