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
    get_all_leads, get_bot_session, get_db, get_full_conversation,
)

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
        "score":            int(lead.get("score") or 0),
        "status":           _map_status(lead.get("stage") or "novo", lead.get("status_conversa")),
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
    }


def _map_status(stage: str, status_conversa: str | None) -> str:
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


@router.get("/data")
async def radar_data(
    request: Request,
    date: str = Query(default=None, description="Data no formato YYYY-MM-DD (BRT). Padrão: hoje."),
):
    """API JSON que alimenta o Radar com dados reais do banco."""
    await _require_auth(request)

    # Data-alvo (BRT)
    today_brt = datetime.now(_BRT).date()
    if date:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            target_date = today_brt
    else:
        target_date = today_brt

    leads_raw = await get_all_leads()

    # Datas disponíveis (últimos 30 dias com dados)
    available_dates: set = set()
    for lead in leads_raw:
        d = _lead_date_brt(dict(lead))
        if d:
            available_dates.add(d)

    result: List[Dict[str, Any]] = []
    for lead in leads_raw:
        # Filtrar pelo dia solicitado
        if _lead_date_brt(dict(lead)) != target_date.isoformat():
            continue
        phone   = lead.get("phone_number", "")
        session = await get_bot_session(phone)
        result.append(_normalize_lead(dict(lead), dict(session) if session else None))

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
    """Retorna o histórico completo de mensagens de um lead."""
    await _require_auth(request)
    messages_raw = await get_full_conversation(phone)
    messages = [
        {
            "role":    m.get("role", ""),
            "message": m.get("message", ""),
            "hora":    _hora_brt(m.get("created_at", "")),
        }
        for m in messages_raw
    ]
    return JSONResponse({"messages": messages, "total": len(messages)})
