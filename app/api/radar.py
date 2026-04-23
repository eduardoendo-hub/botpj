"""
Radar — painel de monitoramento em tempo real dos leads PJ.

Rota separada do /admin, acessível pela diretoria em /radar.
Login próprio em /radar/login — mesmas credenciais do admin, cookie separado (radar_sid).
"""

import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.status import HTTP_303_SEE_OTHER

from app.core.config import settings
from app.core.database import (
    get_all_leads, get_bot_session, get_db, get_full_conversation, get_lead_by_phone,
)
from app.services.tallos_history import get_conversation_history, extract_customer_id_from_notes
from app.services.rd_crm import get_deal_info, get_deal_full_info, sync_pipeline_deals_to_leads
from app.services.product_classifier import classify_product
from app.services.company_intel import get_company_intel
from app.services.farol_engine import classify_farol

# Funis PJ rastreados no RD CRM
_CRM_PIPELINES = {
    "6894b0eb767596001722fd1f": "B2B - Corporativo",
    "6824d026974083001417dc6a": "SDR - B2B Corporativo",
    "64873529c1b1860028cf34f1": "B2B - Farmer",
}

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/radar")
templates = Jinja2Templates(directory="app/templates")

# ── Helpers de autenticação (cookie radar_sid — independente do admin) ────────

_COOKIE = "radar_sid"


async def _radar_session_create(session_id: str, username: str):
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO admin_sessions (session_id, username, expires_at) "
            "VALUES (?, ?, datetime('now', '+7 days'))",
            (session_id, f"radar:{username}")
        )
        await db.commit()
    finally:
        await db.close()


async def _radar_session_username(session_id: str) -> str:
    if not session_id:
        return ""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT username FROM admin_sessions WHERE session_id=? AND expires_at > CURRENT_TIMESTAMP",
            (session_id,)
        )
        row = await cursor.fetchone()
        val = row["username"] if row else ""
        # aceita sessões radar:* ou sessões admin normais
        if val.startswith("radar:"):
            return val[6:]
        return val if val else ""
    finally:
        await db.close()


async def _radar_session_delete(session_id: str):
    db = await get_db()
    try:
        await db.execute("DELETE FROM admin_sessions WHERE session_id=?", (session_id,))
        await db.commit()
    finally:
        await db.close()


async def _require_auth(request: Request):
    """Redireciona para /radar/login se não autenticado."""
    from fastapi import HTTPException
    session_id = request.cookies.get(_COOKIE, "")
    username = await _radar_session_username(session_id)
    if not username:
        raise HTTPException(status_code=303, headers={"Location": "/radar/login"})
    return username


# ── Normalização de lead para o Radar ────────────────────────────────────────

_BRT = timezone(timedelta(hours=-3))


def _minutes_since(iso_str: str) -> int:
    """Retorna quantos minutos se passaram desde iso_str (UTC)."""
    if not iso_str:
        return 0
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return max(0, int(delta.total_seconds() / 60))
    except Exception:
        return 0


def _hora_brt(iso_str: str) -> str:
    """Formata timestamp UTC como HH:MM no horário de Brasília."""
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_BRT).strftime("%H:%M")
    except Exception:
        return "—"


def _hora_brt_date(iso_str: str) -> str:
    """Formata timestamp UTC como dd/mm/yyyy HH:MM no horário de Brasília."""
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_BRT).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return "—"


def _normalize_lead(lead: dict, session: dict | None) -> dict:
    """Converte row do banco para o formato esperado pelo Radar React."""
    agent_active = bool(session and session.get("agent_active"))
    last_user    = (session or {}).get("last_user_msg_at") or lead.get("updated_at") or ""
    last_bot     = (session or {}).get("last_bot_msg_at") or ""
    last_agent   = (session or {}).get("last_agent_msg_at") or ""

    # Última interação relevante
    last_activity = max(
        filter(None, [last_user, last_bot, last_agent]),
        default=lead.get("updated_at") or "",
    )
    sla_min = _minutes_since(last_activity)

    # Quem está conduzindo
    if agent_active:
        quem = "consultor"
    elif last_agent and not agent_active:
        quem = "transferido"
    else:
        quem = "bot"

    # Trail → Tipo (aproximação legível)
    trail = (lead.get("trail") or "").upper()
    tipo_map = {
        "A": "Turma Aberta",
        "B": "In Company",
        "C": "Customizado",
        "D": "Locação",
        "E": "In Company",
    }
    # Prioridade: servico (do form) > tipo_interesse (da IA) > trail map
    tipo = (
        lead.get("servico")
        or lead.get("tipo_interesse")
        or tipo_map.get(trail, "Não definido")
    )

    # Origem: identificador do formulário (ex: "LP - Incompany")
    origem = lead.get("identificador") or ""

    # raw_form_data: todos os campos do formulário original (JSON string)
    raw_form_data = lead.get("raw_form_data") or ""

    return {
        "id":               lead.get("phone_number", ""),
        "hora":             _hora_brt(lead.get("created_at") or ""),
        "nome":             lead.get("contact_name") or "Lead sem nome",
        "empresa":          lead.get("company") or "—",
        "empresa_tier":     "comum",          # futuro: classificar via IA
        "telefone":         lead.get("phone_number") or "—",
        "tema":             lead.get("tema_interesse") or lead.get("training_interest") or "—",
        "tipo":             tipo,
        "formato":          lead.get("formato") or "Não informado",
        "temp":             lead.get("lead_temperature") or "frio",
        "produto":          lead.get("_produto") or "A definir",
        "funil":            lead.get("_crm_etapa") or "—",
        "funil_venda":      lead.get("_crm_pipeline") or "",
        "crm_consultor":    lead.get("_crm_consultor") or "",
        "crm_valor":        lead.get("_crm_valor") or 0.0,
        "score":            int(lead.get("score") or 0),
        "status":           _map_status(lead.get("stage") or "novo", lead.get("status_conversa"), lead.get("_crm_etapa_status") or lead.get("_crm_etapa")),
        "proximo_passo":    lead.get("proximo_passo") or "—",
        "quem":             quem,
        "sla_min":          sla_min,
        "trail":            trail or "?",
        "qtd":              lead.get("qtd_participantes"),
        "cidade":           lead.get("cidade") or "—",
        "urgencia":         lead.get("urgencia") or "",
        "objetivo":         lead.get("objetivo_negocio") or "—",
        "email":            lead.get("email") or "—",
        "cargo":            lead.get("job_title") or "—",
        "prazo":            lead.get("prazo") or "—",
        "canal":            lead.get("source_channel") or "tallos_chat",
        "origem":           origem,
        "qtd_colaboradores": lead.get("qtd_colaboradores") or "—",
        "raw_form_data":    raw_form_data,
        "criado_em":        _hora_brt_date(lead.get("created_at") or ""),
        # Farol — preenchido depois pela classificação paralela
        "farol":            lead.get("_farol") or "?",
        "farol_score":      int(lead.get("_farol_score") or 0),
        "farol_urgencia":   lead.get("_farol_urgencia") or "",
        "farol_motivo":     lead.get("_farol_motivo") or "",
        "farol_pendencia":  lead.get("_farol_pendencia") or "",
        "farol_acao":       lead.get("_farol_acao") or "",
        "farol_intervencao": lead.get("_farol_intervencao") or "",
        "farol_resumo":     lead.get("_farol_resumo") or "",
        "rd_deal_id":       lead.get("_crm_deal_id") or "",
    }


def _map_status(stage: str, status_conversa: str | None, crm_etapa: str | None = None) -> str:
    # Etapas/status do CRM que têm prioridade sobre o status local da conversa
    _CRM_OVERRIDE = {"Ganho", "Perdido", "Fechamento", "Em negociação", "Proposta enviada"}

    if crm_etapa:
        # Match exato OU strings que começam com "Perdido —" (com motivo)
        if crm_etapa in _CRM_OVERRIDE or crm_etapa.startswith("Perdido"):
            return crm_etapa

    if status_conversa:
        return status_conversa

    return {
        "novo":        "Novo",
        "qualificado": "Qualificado",
        "negociando":  "Em atendimento humano",
        "convertido":  "Concluído",
        "perdido":     "Perdido",
    }.get(stage, "Em qualificação")


# ── Rotas ─────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def radar_login_page(request: Request):
    """Tela de login dedicada ao Radar."""
    return templates.TemplateResponse("radar_login.html", {"request": request})


@router.post("/login")
async def radar_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """Autentica e redireciona para /radar."""
    valid = (
        (username == settings.admin_username and password == settings.admin_password)
        or (username == "consultor" and password == settings.consultant_password)
    )
    if valid:
        sid = secrets.token_hex(32)
        await _radar_session_create(sid, username)
        response = RedirectResponse(url="/radar", status_code=HTTP_303_SEE_OTHER)
        response.set_cookie(_COOKIE, sid, httponly=True, max_age=7 * 86400)
        return response
    return templates.TemplateResponse(
        "radar_login.html",
        {"request": request, "error": "Usuário ou senha inválidos"},
        status_code=401,
    )


@router.get("/logout")
async def radar_logout(request: Request):
    sid = request.cookies.get(_COOKIE, "")
    if sid:
        await _radar_session_delete(sid)
    resp = RedirectResponse(url="/radar/login", status_code=HTTP_303_SEE_OTHER)
    resp.delete_cookie(_COOKIE)
    return resp


@router.get("", response_class=HTMLResponse)
async def radar_page(request: Request):
    """Página principal do Radar — SPA React carregada via CDN."""
    await _require_auth(request)
    return templates.TemplateResponse("radar.html", {"request": request})


def _lead_date_brt(lead: dict) -> str:
    """Retorna a data do lead em BRT (YYYY-MM-DD), ou string vazia."""
    created_at = lead.get("created_at")
    if not created_at:
        return ""
    try:
        dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_BRT).date().isoformat()
    except Exception:
        return ""


def _lead_updated_date_brt(lead: dict) -> str:
    """Retorna a data de última atualização do lead em BRT (YYYY-MM-DD), ou string vazia."""
    updated_at = lead.get("updated_at")
    if not updated_at:
        return ""
    try:
        dt = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_BRT).date().isoformat()
    except Exception:
        return ""


def _lead_reference_date(lead: dict) -> str:
    """
    Data de referência do lead para exibição no Radar.
    Regra: se o lead tem raw_form_data (veio de formulário), usa updated_at
           pois re-submissões devem mover o lead para o novo dia.
           Caso contrário, usa created_at.
    """
    if lead.get("raw_form_data"):
        d = _lead_updated_date_brt(lead)
        if d:
            return d
    return _lead_date_brt(lead)


def _lead_matches_date(lead: dict, target_date_iso: str) -> bool:
    """Retorna True se o lead deve aparecer no dia solicitado.
    Um lead aparece em um único dia — o de referência (_lead_reference_date).
    """
    return _lead_reference_date(lead) == target_date_iso


@router.get("/data")
async def radar_data(
    request: Request,
    date: str = Query(default=None, description="Data no formato YYYY-MM-DD (BRT). Padrão: hoje."),
):
    """API JSON que alimenta o Radar com dados reais do banco."""
    await _require_auth(request)

    import asyncio

    # Data-alvo (BRT)
    today_brt = datetime.now(_BRT).date()
    if date:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            target_date = today_brt
    else:
        target_date = today_brt

    # Sincroniza oportunidades de todos os funis PJ em background
    target_iso = target_date.isoformat()
    sync_tasks = [
        asyncio.create_task(sync_pipeline_deals_to_leads(target_iso, pid))
        for pid in _CRM_PIPELINES
    ]

    leads_raw = await get_all_leads()

    # Aguarda todos os syncs (até 15s total)
    try:
        results = await asyncio.wait_for(asyncio.gather(*sync_tasks), timeout=15)
        if any(results):
            leads_raw = await get_all_leads()
    except asyncio.TimeoutError:
        logger.warning("[Radar] Sync CRM demorou mais de 15s — continuando sem esperar")

    # Datas disponíveis (últimos 30 dias com dados)
    # Usa a mesma regra de _lead_reference_date para consistência
    available_dates: set = set()
    for lead in leads_raw:
        ld = dict(lead)
        d = _lead_reference_date(ld)
        if d:
            available_dates.add(d)

    # Filtra leads do dia solicitado (criados no dia OU atualizados com novo form)
    leads_do_dia = [
        dict(lead) for lead in leads_raw
        if _lead_matches_date(dict(lead), target_iso)
    ]

    # Busca sessões e etapas do funil em paralelo
    phones = [l.get("phone_number", "") for l in leads_do_dia]

    sessions_list, crm_list = await asyncio.gather(
        asyncio.gather(*[get_bot_session(p) for p in phones]),
        asyncio.gather(*[get_deal_info(p) for p in phones]),
    )

    # Enriquece leads com dados do CRM antes de classificar o produto
    for lead, session, crm in zip(leads_do_dia, sessions_list, crm_list):
        lead["_crm_etapa"]         = crm.get("etapa", "—")          # fase real do pipeline
        lead["_crm_etapa_status"]  = crm.get("etapa_status", crm.get("etapa", "—"))  # status derivado (inclui Perdido)
        lead["_crm_consultor"]     = crm.get("consultor", "")
        lead["_crm_valor"]         = crm.get("valor", 0.0)
        lead["_crm_pipeline"]      = crm.get("pipeline", "")
        lead["_crm_deal_name"]     = crm.get("deal_name", "")
        lead["_crm_deal_products"] = crm.get("deal_products", [])
        lead["_crm_deal_id"]       = crm.get("deal_id", "")
        lead["_session"]           = dict(session) if session else None

    # Busca últimas mensagens de cada lead para alimentar o classificador.
    # Combina mensagens do bot interno + histórico Tallos (para leads que foram
    # direto para o atendente humano e cujos campos estruturados ficaram vazios).
    async def _get_last_msgs(lead: dict) -> list:
        try:
            # 1. Mensagens do bot interno (DB)
            msgs_raw = await get_full_conversation(lead.get("phone_number", ""))
            bot_msgs = [
                {"role": m.get("role", ""), "message": m.get("message", ""), "created_at": m.get("created_at", "")}
                for m in (msgs_raw or [])
            ]

            # 2. Histórico Tallos (mensagens do lead + operador via WhatsApp)
            tallos_msgs: list = []
            notes = lead.get("notes", "") or ""
            customer_id = extract_customer_id_from_notes(notes)
            if customer_id:
                result = await get_conversation_history(customer_id, page=1, limit=30)
                for m in result.get("messages", []):
                    tallos_role = m.get("role", "")
                    role = "user" if tallos_role == "customer" else "agent" if tallos_role == "operator" else "assistant"
                    tallos_msgs.append({
                        "role":       role,
                        "message":    m.get("message", ""),
                        "created_at": m.get("created_at", ""),
                    })

            # 3. Mescla, ordena por data e retorna as últimas 12
            all_msgs = bot_msgs + tallos_msgs
            all_msgs.sort(key=lambda x: x.get("created_at", ""))
            return all_msgs[-12:]
        except Exception:
            return []

    msgs_list = await asyncio.gather(*[_get_last_msgs(l) for l in leads_do_dia])

    # Classifica produto e farol em paralelo (Claude Haiku, com cache)
    produtos, farois = await asyncio.gather(
        asyncio.gather(*[
            classify_product(lead, msgs)
            for lead, msgs in zip(leads_do_dia, msgs_list)
        ]),
        asyncio.gather(*[
            classify_farol(lead, msgs, {
                "etapa":      lead.get("_crm_etapa") or "",
                "consultor":  lead.get("_crm_consultor") or "",
                "valor":      lead.get("_crm_valor") or 0,
                "pipeline":   lead.get("_crm_pipeline") or "",
            })
            for lead, msgs in zip(leads_do_dia, msgs_list)
        ]),
    )

    result: List[Dict[str, Any]] = []
    for lead, produto, farol in zip(leads_do_dia, produtos, farois):
        lead["_produto"]          = produto
        lead["_farol"]            = farol.get("semaforo", "?")
        lead["_farol_score"]      = farol.get("score_risco", 0)
        lead["_farol_urgencia"]   = farol.get("urgencia", "")
        lead["_farol_motivo"]     = farol.get("motivo_principal", "")
        lead["_farol_pendencia"]  = farol.get("pendencia_principal", "")
        lead["_farol_acao"]       = farol.get("acao_recomendada_supervisor", "")
        lead["_farol_intervencao"] = farol.get("nivel_intervencao_supervisor", "")
        lead["_farol_resumo"]     = farol.get("resumo_executivo", "")
        result.append(_normalize_lead(lead, lead.pop("_session", None)))

    # Ordenar por hora desc (mais recentes primeiro)
    result.sort(key=lambda x: x.get("hora", ""), reverse=True)

    sorted_dates = sorted(available_dates, reverse=True)[:30]

    return JSONResponse({
        "leads":           result,
        "total":           len(result),
        "date":            target_date.isoformat(),
        "available_dates": sorted_dates,
    })


@router.get("/conversation/{phone}")
async def radar_conversation(request: Request, phone: str):
    """
    Retorna o histórico completo de mensagens de um lead.
    Mescla mensagens do bot interno + histórico do Tallos (operador + lead).
    Roles mapeados para: 'user' (lead), 'assistant' (bot), 'agent' (operador).
    """
    await _require_auth(request)

    # 1. Mensagens do bot interno
    messages_raw = await get_full_conversation(phone)
    bot_messages = [
        {
            "role":       m.get("role", ""),        # "user" / "assistant"
            "message":    m.get("message", ""),
            "hora":       _hora_brt(m.get("created_at", "")),
            "created_at": m.get("created_at", ""),
            "source":     "bot",
            "operator_name": "",
        }
        for m in messages_raw
    ]

    # 2. Histórico Tallos (operador + lead via WhatsApp)
    tallos_messages = []
    lead = await get_lead_by_phone(phone)
    if lead:
        notes = lead.get("notes", "") or ""
        customer_id = extract_customer_id_from_notes(notes)
        if customer_id:
            result = await get_conversation_history(customer_id, page=1, limit=100)
            for m in result.get("messages", []):
                tallos_role = m.get("role", "")
                # Mapeia roles do Tallos → roles do radar.html
                if tallos_role == "customer":
                    role = "user"
                elif tallos_role == "operator":
                    role = "agent"
                else:
                    role = "assistant"
                tallos_messages.append({
                    "role":          role,
                    "message":       m.get("message", ""),
                    "hora":          m.get("hora", ""),
                    "created_at":    m.get("created_at", ""),
                    "source":        "tallos",
                    "operator_name": m.get("operator_name", ""),
                })

    # 3. Mescla: se tem histórico Tallos, usa ele como base (mais completo)
    #    e complementa com mensagens do bot que não sejam duplicatas
    if tallos_messages:
        # Tallos tem a conversa completa com operador — usa direto
        messages = sorted(tallos_messages, key=lambda x: x.get("created_at", ""))
    else:
        # Sem Tallos: usa apenas mensagens do bot
        messages = sorted(bot_messages, key=lambda x: x.get("created_at", ""))

    return JSONResponse({"messages": messages, "total": len(messages)})


@router.get("/tallos-history/{phone}")
async def radar_tallos_history(
    request: Request,
    phone: str,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, le=100),
):
    """
    Retorna o histórico completo de conversas do Tallos para um lead.
    Inclui mensagens do lead (customer) e do operador (operator).
    O customer_id é extraído do campo notes do lead no banco.
    """
    await _require_auth(request)

    # Busca o lead no banco para pegar o customer_id
    lead = await get_lead_by_phone(phone)
    if not lead:
        return JSONResponse({"messages": [], "total": 0, "error": "Lead não encontrado"}, status_code=404)

    notes = lead.get("notes", "") or ""
    customer_id = extract_customer_id_from_notes(notes)

    if not customer_id:
        return JSONResponse({
            "messages":   [],
            "total":      0,
            "error":      "contact_id Tallos não encontrado para este lead",
            "phone":      phone,
            "notes":      notes,
        })

    result = await get_conversation_history(customer_id, page=page, limit=limit)
    result["customer_id"] = customer_id
    result["phone"] = phone
    return JSONResponse(result)


@router.get("/crm/{phone}")
async def radar_crm_history(request: Request, phone: str):
    """
    Retorna dados completos do deal no RD CRM para exibição na aba Histórico.
    Inclui: pipeline, etapa, consultor, valor, próxima tarefa, última tarefa e histórico de etapas.
    """
    await _require_auth(request)
    data = await get_deal_full_info(phone)
    return JSONResponse(data)


# ── RD Station CRM — OAuth2 callback ─────────────────────────────────────────

@router.get("/company-intel/{phone}")
async def radar_company_intel(request: Request, phone: str):
    """
    Retorna informações sobre a empresa do lead pesquisadas via IA + web.
    Resultado em cache de 24h por nome de empresa.
    """
    await _require_auth(request)

    lead = await get_lead_by_phone(phone)
    if not lead:
        return JSONResponse({"intel": "", "company": ""}, status_code=404)

    company = (lead.get("company") or "").strip()
    if not company or company == "—":
        return JSONResponse({"intel": "", "company": company})

    intel = await get_company_intel(company)
    return JSONResponse({"company": company, **intel})


@router.get("/rd-crm/callback", response_class=HTMLResponse)
async def rd_crm_callback(request: Request, code: str = "", error: str = ""):
    """
    Recebe o código de autorização do RD Station CRM após o usuário autorizar o app.
    Exibe o código para que seja trocado manualmente por um access_token.
    """
    if error:
        html = f"""
        <html><body style="font-family:monospace;padding:40px;background:#0f172a;color:#f87171">
        <h2>❌ Erro na autorização</h2><pre>{error}</pre>
        </body></html>"""
        return HTMLResponse(html)

    if not code:
        html = """
        <html><body style="font-family:monospace;padding:40px;background:#0f172a;color:#94a3b8">
        <h2>⚠️ Nenhum código recebido</h2>
        <p>Acesse esta URL via o fluxo de autorização do RD Station.</p>
        </body></html>"""
        return HTMLResponse(html)

    # Tenta trocar o code por access_token automaticamente
    client_id     = settings.rd_crm_client_id
    client_secret = settings.rd_crm_client_secret
    token_info    = ""

    if client_id and client_secret:
        try:
            import httpx as _httpx
            resp = await _httpx.AsyncClient(timeout=10).post(
                "https://api.rd.services/auth/token",
                json={
                    "client_id":     client_id,
                    "client_secret": client_secret,
                    "code":          code,
                },
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                data         = resp.json()
                access_token = data.get("access_token", "")
                refresh_token = data.get("refresh_token", "")
                token_info = f"""
                <div style="margin-top:24px;padding:16px;background:#0d2d1a;border-radius:8px;border:1px solid #34d399">
                  <p style="color:#34d399;margin:0 0 8px">✅ Token obtido com sucesso! Adicione ao .env do servidor:</p>
                  <pre style="color:#a7f3d0;word-break:break-all">RD_CRM_TOKEN={access_token}</pre>
                  <pre style="color:#6b7280;word-break:break-all">RD_CRM_REFRESH_TOKEN={refresh_token}</pre>
                </div>"""
            else:
                token_info = f'<p style="color:#f87171">Troca falhou ({resp.status_code}): {resp.text[:200]}</p>'
        except Exception as e:
            token_info = f'<p style="color:#f87171">Erro na troca: {e}</p>'
    else:
        token_info = f"""
        <div style="margin-top:16px;padding:12px;background:#1e293b;border-radius:8px">
          <p style="color:#fbbf24;margin:0 0 8px">⚠️ client_id/client_secret não configurados. Troque manualmente:</p>
          <pre style="color:#818cf8;word-break:break-all">CODE={code}</pre>
          <p style="color:#94a3b8;font-size:12px">POST https://api.rd.services/auth/token<br>
          {{"client_id":"...","client_secret":"...","code":"{code}"}}</p>
        </div>"""

    html = f"""
    <html><head><title>RD CRM OAuth</title></head>
    <body style="font-family:monospace;padding:40px;background:#0f172a;color:#e2e8f0">
    <h2 style="color:#818cf8">🔐 RD Station CRM — Autorização</h2>
    <p>Código de autorização recebido:</p>
    <pre style="background:#1e293b;padding:12px;border-radius:6px;word-break:break-all;color:#34d399">{code}</pre>
    {token_info}
    </body></html>"""
    return HTMLResponse(html)
