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

            # etapa_status: status derivado para exibição na coluna Status do Radar.
            # Quando deal_lost_reason estiver preenchido e win=False → "Perdido — motivo".
            # etapa permanece com o nome real da fase do pipeline (ex: "Fechamento").
            etapa_status = etapa
            lost_reason = deal.get("deal_lost_reason")
            if lost_reason and not deal.get("win"):
                reason_name = lost_reason.get("name", "") if isinstance(lost_reason, dict) else str(lost_reason)
                etapa_status = f"Perdido — {reason_name}" if reason_name else "Perdido"

            user = deal.get("user", {}) or {}
            consultor = user.get("name", "") if isinstance(user, dict) else ""

            valor = float(deal.get("amount_total") or deal.get("amount_unique") or 0)

            pipeline_obj = deal.get("deal_pipeline", {}) or {}
            pipeline = pipeline_obj.get("name", "") if isinstance(pipeline_obj, dict) else ""

            deal_name     = deal.get("name") or ""
            deal_products = deal.get("deal_products") or []
            deal_id         = deal.get("_id") or deal.get("id") or ""
            deal_updated_at = deal.get("updated_at") or ""
            logger.info(f"[RD CRM] {phone_clean} → funil={etapa} | status={etapa_status} | {consultor} | R${valor:.2f} | id={deal_id}")
            return {
                "etapa":           etapa,           # fase real do pipeline (para coluna Funil)
                "etapa_status":    etapa_status,    # status derivado (para coluna Status)
                "consultor":       consultor,
                "valor":           valor,
                "pipeline":        pipeline,
                "deal_name":       deal_name,
                "deal_products":   deal_products,
                "deal_id":         deal_id,
                "deal_updated_at": deal_updated_at, # para detectar movimentação recente no CRM
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

            # Se deal_lost_reason estiver preenchido e win=False → perdido
            lost_reason = deal.get("deal_lost_reason")
            if lost_reason and not deal.get("win"):
                reason_name = lost_reason.get("name", "") if isinstance(lost_reason, dict) else str(lost_reason)
                etapa = f"Perdido — {reason_name}" if reason_name else "Perdido"

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


async def get_deals_updated_by_date(date_iso: str, pipeline_id: str) -> List[Dict]:
    """
    Retorna deals de um pipeline atualizados em date_iso (YYYY-MM-DD).
    Usa a API v1 sem created_at_period, que filtra por updated_at.
    Usado para detectar leads antigos que tiveram movimentação no CRM hoje.
    """
    if not settings.rd_crm_token:
        return []

    start = f"{date_iso}T00:00:00"
    end   = f"{date_iso}T23:59:59"

    query = {
        "token":            settings.rd_crm_token,
        "start_date":       start,
        "end_date":         end,
        "deal_pipeline_id": pipeline_id,
        "limit":            200,
        "page":             1,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{_BASE}/deals", params=query)
            if resp.status_code != 200:
                logger.warning(f"[RD CRM sync updated] GET /deals retornou {resp.status_code}")
                return []
            data = resp.json()
            deals = data if isinstance(data, list) else data.get("deals", [])
            logger.info(f"[RD CRM sync updated] {len(deals)} deal(s) atualizados em {date_iso}")
            return deals
    except Exception as e:
        logger.error(f"[RD CRM sync updated] Erro ao buscar deals: {e}")
        return []


def _extract_contact_info(c: dict) -> tuple[str, str, str]:
    """Extrai (nome, empresa, contact_id) de um dict de contato do CRM.

    Nota: evitar ternary inline em cadeia de `or` — o Python aplica o if/else
    com precedência mais baixa que `or`, o que pode curto-circuitar campos
    anteriores quando a condição for False.
    """
    cid  = c.get("_id") or c.get("id") or ""
    nome = c.get("name") or ""

    # Tenta cada campo de empresa em ordem de confiabilidade
    org = c.get("organization") or {}
    org_name = org.get("name", "") if isinstance(org, dict) else ""
    empresa = (
        c.get("organization_name")   # campo direto do contato
        or c.get("company")          # campo alternativo
        or org_name                  # objeto organização aninhado
        or ""
    )
    return nome, empresa, cid


def _deal_org_name(deal: dict) -> str:
    """Tenta extrair nome da empresa/organização diretamente do objeto deal."""
    # Nível direto
    org_name = deal.get("organization_name") or deal.get("company") or ""
    if org_name:
        return org_name
    # Objeto aninhado deal.organization ou deal.deal_organization
    for key in ("organization", "deal_organization"):
        obj = deal.get(key)
        if isinstance(obj, dict):
            name = obj.get("name") or obj.get("organization_name") or ""
            if name:
                return name
    return ""


async def _get_contact_phone_from_deal(client: httpx.AsyncClient, deal: Dict) -> tuple[str, str, str, str]:
    """
    Extrai (phone, contact_id, contact_name, company) do deal.
    Tenta o campo 'contacts' embutido; se não tiver, busca via GET /contacts?deal_id=.
    Retorna ("", "", "", "") se não encontrar.
    """
    deal_id = deal.get("_id") or deal.get("id") or ""
    # Empresa pode estar direto no deal (organização associada ao negócio)
    deal_empresa = _deal_org_name(deal)

    # Se a empresa ainda não veio no deal resumido (listagem), busca o deal completo
    # O endpoint GET /deals/{id} retorna o objeto organization aninhado com o nome
    if not deal_empresa and deal_id:
        try:
            r_full = await client.get(f"{_BASE}/deals/{deal_id}", params={"token": settings.rd_crm_token})
            if r_full.status_code == 200:
                full_deal = r_full.json()
                deal_empresa = _deal_org_name(full_deal)
                logger.debug(f"[RD CRM] deal_empresa do deal completo: {deal_empresa!r}")
        except Exception:
            pass

    # 1. Tenta campo contacts embutido no deal
    contacts_raw = deal.get("contacts") or []
    if isinstance(contacts_raw, list) and contacts_raw:
        for c in contacts_raw:
            if isinstance(c, dict):
                nome, empresa, cid = _extract_contact_info(c)
                empresa = empresa or deal_empresa
                for ph in (c.get("phones") or []):
                    raw = ph.get("phone", "") if isinstance(ph, dict) else str(ph)
                    normalized = _normalize_phone(raw)
                    if normalized:
                        return normalized, cid, nome, empresa
            elif isinstance(c, str):
                # c é apenas o ID — busca o contato completo
                try:
                    r = await client.get(f"{_BASE}/contacts/{c}", params={"token": settings.rd_crm_token})
                    if r.status_code == 200:
                        contact = r.json()
                        nome, empresa, cid = _extract_contact_info(contact)
                        empresa = empresa or deal_empresa
                        for ph in (contact.get("phones") or []):
                            raw = ph.get("phone", "") if isinstance(ph, dict) else str(ph)
                            normalized = _normalize_phone(raw)
                            if normalized:
                                return normalized, cid, nome, empresa
                except Exception:
                    pass

    # 2. Fallback: GET /contacts?deal_id= (busca contato pelo telefone para garantir empresa)
    if deal_id:
        try:
            r = await client.get(
                f"{_BASE}/contacts",
                params={"token": settings.rd_crm_token, "deal_id": deal_id}
            )
            if r.status_code == 200:
                data = r.json()
                contacts = data.get("contacts", data) if isinstance(data, dict) else data
                if isinstance(contacts, list):
                    for c in contacts:
                        nome, empresa, cid = _extract_contact_info(c)
                        empresa = empresa or deal_empresa
                        for ph in (c.get("phones") or []):
                            raw = ph.get("phone", "") if isinstance(ph, dict) else str(ph)
                            normalized = _normalize_phone(raw)
                            if normalized:
                                return normalized, cid, nome, empresa
        except Exception:
            pass

    # 3. Último recurso: busca contato diretamente pelo ID do deal se tiver deal_id
    # O contato pode estar no campo user/contact_id do deal
    if deal_id:
        try:
            # Tenta buscar contatos pelo contact_ids do deal completo
            r_full = await client.get(f"{_BASE}/deals/{deal_id}", params={"token": settings.rd_crm_token})
            if r_full.status_code == 200:
                full_deal = r_full.json()
                for cid_raw in (full_deal.get("contact_ids") or []):
                    try:
                        r_c = await client.get(f"{_BASE}/contacts/{cid_raw}", params={"token": settings.rd_crm_token})
                        if r_c.status_code == 200:
                            contact = r_c.json()
                            nome, empresa, cid = _extract_contact_info(contact)
                            empresa = empresa or _deal_org_name(full_deal)
                            for ph in (contact.get("phones") or []):
                                raw = ph.get("phone", "") if isinstance(ph, dict) else str(ph)
                                normalized = _normalize_phone(raw)
                                if normalized:
                                    return normalized, cid, nome, empresa
                    except Exception:
                        pass
        except Exception:
            pass

    return "", "", "", ""


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


def _extract_info_from_activity_text(text: str) -> dict:
    """
    Parseia o texto de uma atividade do CRM buscando pares pergunta→resposta.

    Suporta dois formatos reais da API:

    Formato A (limpo, blocos separados por \\n\\n):
      "Qual é o nome da sua empresa?\\n\\nBot - 22 abril, 2026 13:28\\n\\nSpartan do Brasil\\n\\nreply message"

    Formato B (com timestamps, pode ter encoding UTF-8 quebrado):
      "[22/04/2026 13:28] Bot: Qual Ã© o nome da sua empresa?\\n[22/04/2026 13:28] Cliente: Spartan do Brasil"
    """
    import re as _re

    extracted: dict = {}
    if not text:
        return extracted

    _EMAIL_VAL = _re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
    _EMPRESA_Q = _re.compile(r"nome da sua empresa|nome da empresa", _re.I)
    _EMAIL_Q   = _re.compile(r"melhor e-mail|seu e-mail|seu email", _re.I)
    _NOME_Q    = _re.compile(r"qual.*?seu nome\??|como.*?chama", _re.I)

    # ── Formato A: blocos por \n\n ────────────────────────────────────────────
    # Estrutura: [pergunta]\n\nBot - data\n\n[resposta]\n\nreply message
    # A pergunta vem logo antes de "Bot - data", a resposta logo depois
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    for i, block in enumerate(blocks):
        if not _re.match(r"Bot\s*-\s*\d", block) and not _re.match(r"Treinamentos\s*-", block):
            # Este bloco pode ser uma pergunta — próximo bloco "Bot - data" → bloco seguinte = resposta
            if i + 2 < len(blocks) and _re.match(r"Bot\s*-\s*\d", blocks[i + 1]):
                resposta = blocks[i + 2] if i + 2 < len(blocks) else ""
                # Ignora se resposta é atribuição de remetente ou separador
                if resposta and not _re.match(r"Bot\s*-|Treinamentos\s*-|reply message", resposta, _re.I):
                    if _EMPRESA_Q.search(block) and "company" not in extracted:
                        extracted["company"] = resposta
                    if _EMAIL_Q.search(block) and "email" not in extracted:
                        m = _EMAIL_VAL.search(resposta)
                        if m:
                            extracted["email"] = m.group(0)
                    if _NOME_Q.search(block) and "contact_name" not in extracted:
                        if len(resposta) < 60:
                            extracted["contact_name"] = resposta

    # ── Formato B: linhas com [data] Role: msg ────────────────────────────────
    if "company" not in extracted:
        last_bot_q = ""
        for line in text.split("\n"):
            line = line.strip()
            m_bot = _re.match(r"\[\d{2}/\d{2}/\d{4} \d{2}:\d{2}\]\s*Bot:\s*(.+)", line)
            m_cli = _re.match(r"\[\d{2}/\d{2}/\d{4} \d{2}:\d{2}\]\s*Cliente:\s*(.+)", line)
            if m_bot:
                last_bot_q = m_bot.group(1)
            elif m_cli and last_bot_q:
                answer = m_cli.group(1).strip()
                if _EMPRESA_Q.search(last_bot_q) and "company" not in extracted:
                    extracted["company"] = answer
                if _EMAIL_Q.search(last_bot_q) and "email" not in extracted:
                    m = _EMAIL_VAL.search(answer)
                    if m:
                        extracted["email"] = m.group(0)
                if _NOME_Q.search(last_bot_q) and "contact_name" not in extracted:
                    if len(answer) < 60:
                        extracted["contact_name"] = answer

    # Email avulso no texto (independente de pergunta)
    if "email" not in extracted:
        m = _EMAIL_VAL.search(text)
        if m:
            extracted["email"] = m.group(0)

    return extracted


async def _extract_lead_info_from_conversation(phone: str) -> dict:
    """
    Varre as mensagens da conversa do lead buscando pares pergunta→resposta
    para extrair empresa, email e nome quando esses campos estão vazios.

    Detecta padrões como:
      Bot: "Qual é o nome da sua empresa?"  → próxima msg do user = company
      Bot: "Qual é o seu melhor e-mail?"    → próxima msg do user = email
      Bot: "Qual é o seu nome?"             → próxima msg do user = contact_name
    """
    import re as _re
    from app.core.database import get_full_conversation

    try:
        msgs = await get_full_conversation(phone)
        if not msgs:
            return {}

        extracted: dict = {}
        _EMPRESA_RE = _re.compile(r"nome da sua empresa|nome da empresa", _re.I)
        _EMAIL_RE   = _re.compile(r"melhor e-mail|seu e-mail|seu email", _re.I)
        _NOME_RE    = _re.compile(r"qual.*seu nome\??|como.*chama", _re.I)
        _EMAIL_VAL  = _re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

        msgs_list = list(msgs)
        for i, msg in enumerate(msgs_list):
            role    = (msg.get("role") or "").lower()
            content = (msg.get("message") or "").strip()
            if role not in ("assistant", "agent") or not content:
                continue

            # Próxima mensagem do usuário
            next_user = next(
                (m.get("message", "").strip()
                 for m in msgs_list[i+1:]
                 if (m.get("role") or "").lower() == "user" and m.get("message", "").strip()),
                None
            )
            if not next_user:
                continue

            if _EMPRESA_RE.search(content) and "company" not in extracted:
                extracted["company"] = next_user
            elif _EMAIL_RE.search(content) and "email" not in extracted:
                m = _EMAIL_VAL.search(next_user)
                if m:
                    extracted["email"] = m.group(0)
            elif _NOME_RE.search(content) and "contact_name" not in extracted:
                extracted["contact_name"] = next_user

        return extracted
    except Exception as e:
        logger.warning(f"[RD CRM sync] Erro ao extrair info da conversa de {phone}: {e}")
        return {}


async def _build_combined_transcript(
    client: httpx.AsyncClient,
    deal_id: str,
    phone: str,
) -> str:
    """
    Monta um texto combinado com TODAS as fontes de informação disponíveis:
      1. Atividades do CRM (transcript da conversa no Tallos/RD)
      2. Mensagens do chat local (tabela conversations)

    Separa cada fonte com '--- [seção] ---' para o LLM ter contexto.
    Retorna string vazia se não houver nenhuma fonte.
    """
    parts: list[str] = []

    # 1. Atividades do CRM
    if deal_id:
        activities = await _fetch_deal_activities(client, deal_id)
        crm_texts = [
            (a.get("text") or a.get("description") or "").strip()
            for a in activities
            if a.get("text") or a.get("description")
        ]
        if crm_texts:
            parts.append("=== TRANSCRIPT CRM (Tallos/RD) ===\n\n" + "\n\n---\n\n".join(crm_texts))

    # 2. Conversa local (webhook_logs / conversations)
    try:
        from app.core.database import get_full_conversation
        msgs = await get_full_conversation(phone)
        if msgs:
            lines = []
            for m in msgs:
                role = (m.get("role") or "").lower()
                text = (m.get("message") or "").strip()
                if not text:
                    continue
                label = "Bot" if role in ("assistant", "agent") else "Cliente"
                lines.append(f"{label}: {text}")
            if lines:
                parts.append("=== CONVERSA DO CHAT (Bot/Lead) ===\n\n" + "\n".join(lines))
    except Exception as e:
        logger.warning(f"[RD CRM sync] Erro ao obter conversa local de {phone}: {e}")

    return "\n\n".join(parts)


async def sync_pipeline_deals_to_leads(date_iso: str, pipeline_id: str) -> int:
    """
    Sincroniza oportunidades do funil pipeline_id criadas OU atualizadas em date_iso
    com a tabela de leads interna.

    Para cada deal do CRM:
      - Extrai o telefone do contato associado
      - Se o lead já existe na tabela → garante deal_id salvo e atualiza updated_at
        se o deal foi movimentado hoje (para o lead aparecer no Radar de hoje)
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
    today_utc_prefix = date_iso  # "YYYY-MM-DD" para comparar com updated_at do CRM

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

                phone, contact_id, contact_nome_crm, contact_empresa_crm = await _get_contact_phone_from_deal(client, deal)
                if not phone:
                    logger.debug(f"[RD CRM sync] Deal {deal_id} ({deal_name}) sem telefone — ignorado")
                    continue

                existing = await get_lead_by_phone(phone)

                if existing:
                    # Lead já existe — garante deal_id e enriquece campos vazios via LLM
                    updates: dict = {}
                    if not existing.get("rd_crm_deal_id") and deal_id:
                        updates["rd_crm_deal_id"] = deal_id

                    needs_enrichment = (
                        not existing.get("company")
                        or not existing.get("email")
                        or not existing.get("crm_insights")
                    )
                    if needs_enrichment:
                        combined_text = await _build_combined_transcript(client, deal_id, phone)
                        if combined_text:
                            from app.services.lead_enricher import enrich_lead_from_activity, map_enriched_to_lead_fields
                            enriched = await enrich_lead_from_activity(phone, combined_text)
                            fields = map_enriched_to_lead_fields(enriched)
                            insights = fields.pop("_insights", None)
                            for k, v in fields.items():
                                if not existing.get(k):
                                    updates[k] = v
                            if insights:
                                updates["crm_insights"] = insights
                    if updates:
                        await upsert_lead(phone, **updates)
                        logger.info(f"[RD CRM sync] enriquecido {phone}: {list(updates.keys())}")
                    continue

                # Lead novo — importa do CRM
                logger.info(
                    f"[RD CRM sync] ✅ Importando lead CRM | phone={phone} | "
                    f"deal={deal_name!r} | etapa={etapa} | empresa={contact_empresa_crm!r}"
                )

                # Nome: prioriza nome do contato do CRM; fallback para deal_name
                nome = (
                    contact_nome_crm
                    or (deal_name.split(" - ")[0].strip() if " - " in deal_name else deal_name)
                )

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
                    company=contact_empresa_crm,
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

                # Enriquecimento via LLM — combina atividades do CRM + chat local
                combined_text = await _build_combined_transcript(client, deal_id, phone)
                if combined_text:
                    from app.services.lead_enricher import enrich_lead_from_activity, map_enriched_to_lead_fields
                    enriched = await enrich_lead_from_activity(phone, combined_text)
                    fields = map_enriched_to_lead_fields(enriched)
                    insights = fields.pop("_insights", None)
                    fill: dict = {}
                    for k, v in fields.items():
                        # Não sobrescreve campos já preenchidos vindos do CRM
                        if k == "contact_name" and nome:
                            continue
                        if k == "company" and contact_empresa_crm:
                            continue
                        fill[k] = v
                    if insights:
                        fill["crm_insights"] = insights
                    if fill:
                        await upsert_lead(phone, **fill)
                        logger.info(f"[RD CRM sync] LLM enrichment novo lead {phone}: {list(fill.keys())}")

                # Notificação por email — apenas para leads de HOJE
                # Importações históricas (datas passadas) não disparam email
                from datetime import timedelta as _td2
                _today_brt = datetime.now(timezone(offset=_td2(hours=-3))).date().isoformat()
                if date_iso == _today_brt:
                    try:
                        from app.core.database import get_bot_config
                        from app.services.email_service import send_lead_notification
                        cfg = await get_bot_config()
                        lead_payload = {
                            "contact_name": nome,
                            "phone_number": phone,
                            "job_title":    "",
                            "produto":      deal_name,
                            "origem":       f"RD CRM — {etapa}" if etapa else "RD CRM",
                        }
                        await send_lead_notification(lead_payload, cfg)
                    except Exception as e:
                        logger.error(f"[RD CRM sync] Erro ao enviar email para {phone}: {e}")

                imported += 1

    except Exception as e:
        logger.error(f"[RD CRM sync] Erro geral na sincronização: {e}", exc_info=True)

    if imported:
        logger.info(f"[RD CRM sync] {imported} lead(s) novo(s) importado(s) do CRM para {date_iso}")

    # ── Backfill: enriquece leads antigos com empresa/email/insights vazios ──────
    # Processa até 5 leads por ciclo para não impactar a latência do Radar.
    # Leads que já têm empresa E email E crm_insights não são reprocessados.
    try:
        await _enrich_stale_leads(max_leads=5)
    except Exception as e:
        logger.warning(f"[RD CRM sync] Erro no backfill de leads antigos: {e}")

    return imported


async def _enrich_stale_leads(max_leads: int = 5) -> None:
    """
    Busca leads com rd_crm_deal_id mas com company/email/crm_insights vazios
    e os enriquece via LLM (combinando atividades CRM + chat local).
    Limita a max_leads por chamada para não impactar latência.
    """
    from app.core.database import get_db, upsert_lead

    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT phone_number, rd_crm_deal_id, company, email, crm_insights
               FROM leads
               WHERE rd_crm_deal_id IS NOT NULL AND rd_crm_deal_id != ''
                 AND (company IS NULL OR company = ''
                      OR email IS NULL OR email = ''
                      OR crm_insights IS NULL OR crm_insights = '')
               ORDER BY created_at DESC
               LIMIT ?""",
            (max_leads,)
        )
        rows = await cursor.fetchall()
    finally:
        await db.close()

    if not rows:
        return

    from app.services.lead_enricher import enrich_lead_from_activity, map_enriched_to_lead_fields

    async with httpx.AsyncClient(timeout=15) as client:
        for row in rows:
            phone   = row["phone_number"]
            deal_id = row["rd_crm_deal_id"]
            row_dict = dict(row)
            try:
                updates: dict = {}

                # Passo 1: tenta pegar empresa direto do deal no CRM (mais confiável que o LLM)
                if not row_dict.get("company") and deal_id:
                    try:
                        r = await client.get(f"{_BASE}/deals/{deal_id}", params={"token": settings.rd_crm_token})
                        if r.status_code == 200:
                            full_deal = r.json()
                            empresa_crm = _deal_org_name(full_deal)
                            if empresa_crm:
                                updates["company"] = empresa_crm
                                logger.info(f"[RD CRM backfill] {phone} empresa do deal: {empresa_crm!r}")
                    except Exception:
                        pass

                # Passo 2: LLM para campos restantes (email, insights, trail, etc.)
                if not row_dict.get("crm_insights") or not row_dict.get("email"):
                    combined = await _build_combined_transcript(client, deal_id, phone)
                    if combined:
                        enriched = await enrich_lead_from_activity(phone, combined)
                        fields   = map_enriched_to_lead_fields(enriched)
                        insights = fields.pop("_insights", None)
                        for k, v in fields.items():
                            if v and not row_dict.get(k) and k not in updates:
                                updates[k] = v
                        if insights and not row_dict.get("crm_insights"):
                            updates["crm_insights"] = insights

                if updates:
                    await upsert_lead(phone, **updates)
                    logger.info(f"[RD CRM backfill] {phone} enriquecido: {list(updates.keys())}")
            except Exception as e:
                logger.warning(f"[RD CRM backfill] Erro para {phone}: {e}")
