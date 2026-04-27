"""Rotas do painel administrativo — Bot SDR PJ."""

import csv
import io
import secrets
import os
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.status import HTTP_303_SEE_OTHER
from jinja2 import Environment, FileSystemLoader

from app.core.config import settings
from app.core.database import (
    get_all_knowledge, get_knowledge_by_id, add_knowledge, update_knowledge,
    delete_knowledge, get_all_conversations_summary, get_conversation_history,
    get_all_leads, get_system_prompt, set_system_prompt,
    get_bot_config, get_bot_config_full, set_bot_config_bulk,
    get_webhook_logs, get_db, get_lead_by_phone,
    get_token_usage_daily, get_token_usage_by_service,
    get_token_usage_by_model, get_token_usage_totals,
    get_all_radar_users, create_radar_user, delete_radar_user,
)
from app.services.tallos import tallos_service
from app.services.url_fetcher import fetch_url_content
from app.services.report_service import build_daily_report, send_report_whatsapp, list_waba_templates
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/admin")

# ── Fuso horário de Brasília (UTC-3) ─────────────────────────────────
_BRT = timezone(timedelta(hours=-3))


def _to_brt(value: str) -> str:
    """Converte string de timestamp UTC para horário de Brasília (UTC-3)."""
    if not value:
        return ""
    dt = None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    brt = dt.astimezone(_BRT)
    return brt.strftime("%d/%m/%Y %H:%M:%S")


_templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "admin")
_jinja_env = Environment(
    loader=FileSystemLoader(_templates_dir),
    cache_size=0,
    auto_reload=True,
)
_jinja_env.filters["brt"] = _to_brt
_jinja_env.globals["prefix"] = "/pj"   # prefixo nginx — garante links corretos em todos os templates
templates = Jinja2Templates(env=_jinja_env)


# ── Sessões persistidas no banco ─────────────────────────────────────
async def _session_exists(session_id: str) -> bool:
    db = await get_db()
    try:
        row = await db.execute(
            "SELECT 1 FROM admin_sessions WHERE session_id=? AND expires_at > CURRENT_TIMESTAMP",
            (session_id,)
        )
        return await row.fetchone() is not None
    finally:
        await db.close()


async def _session_create(session_id: str, username: str):
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO admin_sessions (session_id, username, expires_at) "
            "VALUES (?, ?, datetime('now', '+1 day'))",
            (session_id, username)
        )
        await db.commit()
    finally:
        await db.close()


async def _session_username(session_id: str) -> str:
    db = await get_db()
    try:
        row = await db.execute(
            "SELECT username FROM admin_sessions WHERE session_id=? AND expires_at > CURRENT_TIMESTAMP",
            (session_id,)
        )
        result = await row.fetchone()
        return result[0] if result else ""
    finally:
        await db.close()


async def _session_delete(session_id: str):
    db = await get_db()
    try:
        await db.execute("DELETE FROM admin_sessions WHERE session_id=?", (session_id,))
        await db.commit()
    finally:
        await db.close()


_ADMIN_COOKIE = "pj_admin_sid"


async def _check_auth(request: Request):
    """Verifica autenticação — apenas admin tem acesso ao painel completo."""
    session_id = request.cookies.get(_ADMIN_COOKIE)
    username = await _session_username(session_id) if session_id else ""
    if username == settings.admin_username:
        return
    if username == "consultor":
        raise HTTPException(status_code=303, headers={"Location": "/admin/conversations"})
    raise HTTPException(status_code=303, headers={"Location": "/admin/login"})


async def _check_auth_shared(request: Request):
    """Auth compartilhado — aceita admin e consultor."""
    session_id = request.cookies.get(_ADMIN_COOKIE)
    username = await _session_username(session_id) if session_id else ""
    if username in (settings.admin_username, "consultor"):
        return
    raise HTTPException(status_code=303, headers={"Location": "/admin/login"})


def _r(request: Request, template: str, ctx: dict = {}):
    return templates.TemplateResponse(request, template, ctx)


# ==================== Auth ====================

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return _r(request, "login.html")


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == settings.admin_username and password == settings.admin_password:
        session_id = secrets.token_hex(32)
        await _session_create(session_id, settings.admin_username)
        response = RedirectResponse(url="/admin", status_code=HTTP_303_SEE_OTHER)
        response.set_cookie(_ADMIN_COOKIE, session_id, httponly=True, max_age=86400, path="/pj")
        return response
    if username == "consultor" and password == settings.consultant_password:
        session_id = secrets.token_hex(32)
        await _session_create(session_id, "consultor")
        response = RedirectResponse(url="/admin/conversations", status_code=HTTP_303_SEE_OTHER)
        response.set_cookie(_ADMIN_COOKIE, session_id, httponly=True, max_age=86400, path="/pj")
        return response
    return _r(request, "login.html", {"error": "Credenciais inválidas"})


@router.get("/logout")
async def logout(request: Request):
    session_id = request.cookies.get(_ADMIN_COOKIE)
    if session_id:
        await _session_delete(session_id)
    response = RedirectResponse(url="/admin/login", status_code=HTTP_303_SEE_OTHER)
    response.delete_cookie(_ADMIN_COOKIE, path="/pj")
    return response


# ==================== Dashboard ====================

@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request):
    await _check_auth(request)
    leads = await get_all_leads()
    conversations = await get_all_conversations_summary()
    knowledge_items = await get_all_knowledge()
    tallos_status = await tallos_service.get_status()
    return _r(request, "dashboard.html", {
        "leads_count": len(leads),
        "conversations_count": len(conversations),
        "knowledge_count": len(knowledge_items),
        "tallos_status": tallos_status,
        "active_page": "dashboard",
    })


# ==================== Knowledge Base ====================

@router.get("/knowledge", response_class=HTMLResponse)
async def knowledge_list(request: Request):
    await _check_auth(request)
    items = await get_all_knowledge()
    return _r(request, "knowledge_list.html", {"items": items, "active_page": "knowledge"})


@router.get("/knowledge/new", response_class=HTMLResponse)
async def knowledge_new(request: Request):
    await _check_auth(request)
    return _r(request, "knowledge_form.html", {"item": None, "active_page": "knowledge"})


@router.get("/knowledge/{item_id}/edit", response_class=HTMLResponse)
async def knowledge_edit(request: Request, item_id: int):
    await _check_auth(request)
    item = await get_knowledge_by_id(item_id)
    if not item:
        raise HTTPException(status_code=404)
    return _r(request, "knowledge_form.html", {"item": item, "active_page": "knowledge"})


class _UrlRequest(BaseModel):
    url: str


@router.post("/knowledge/fetch-url")
async def knowledge_fetch_url(request: Request, body: _UrlRequest):
    """Busca o conteúdo de uma URL de curso e retorna o texto extraído."""
    await _check_auth(request)
    result = await fetch_url_content(body.url)
    return result


@router.post("/knowledge/save")
async def knowledge_save(
    request: Request,
    category: str = Form(...),
    title: str = Form(...),
    content: str = Form(...),
    item_id: int = Form(None),
):
    await _check_auth(request)
    if item_id:
        await update_knowledge(item_id, category, title, content)
    else:
        await add_knowledge(category, title, content)
    from app.services.ai_engine import clear_cache
    clear_cache()
    return RedirectResponse(url="/admin/knowledge", status_code=HTTP_303_SEE_OTHER)


@router.post("/knowledge/{item_id}/delete")
async def knowledge_delete(request: Request, item_id: int):
    await _check_auth(request)
    await delete_knowledge(item_id)
    from app.services.ai_engine import clear_cache
    clear_cache()
    return RedirectResponse(url="/admin/knowledge", status_code=HTTP_303_SEE_OTHER)


# ==================== Conversations ====================

@router.get("/conversations", response_class=HTMLResponse)
async def conversations_list(request: Request):
    await _check_auth_shared(request)
    conversations = await get_all_conversations_summary()
    return _r(request, "conversations.html", {
        "conversations": conversations,
        "active_page": "conversations",
    })


@router.get("/conversations/{phone_number}", response_class=HTMLResponse)
async def conversation_detail(request: Request, phone_number: str, back: str = ""):
    await _check_auth_shared(request)

    back_url = back or request.headers.get("referer", "") or "/admin/conversations"
    if back_url and not back_url.startswith("/"):
        from urllib.parse import urlparse
        parsed = urlparse(back_url)
        back_url = parsed.path + ("?" + parsed.query if parsed.query else "")

    _TALLOS_CHANNELS = ("tallos_pj", "tallos_form_pj", "tallos_chat", "tallos_monitor")
    messages = []
    source = "local"

    lead = await get_lead_by_phone(phone_number)
    contact_id = ""
    if lead:
        channel = lead.get("source_channel", "")
        notes   = lead.get("notes", "") or ""
        for part in notes.split("|"):
            part = part.strip()
            if part.startswith("tallos_contact_id:"):
                contact_id = part.replace("tallos_contact_id:", "").strip()
                break
            if part.startswith("contact_id:"):
                contact_id = part.replace("contact_id:", "").strip()
                break

        if channel in _TALLOS_CHANNELS and contact_id:
            raw = await tallos_service.get_recent_messages(contact_id, limit=100)
            if raw:
                source = "tallos_api"
                for m in raw:
                    raw_content = m.get("content") or m.get("message") or m.get("text") or ""
                    if isinstance(raw_content, dict):
                        text = (
                            raw_content.get("message")
                            or raw_content.get("text")
                            or raw_content.get("body")
                            or str(raw_content)
                        )
                    else:
                        text = str(raw_content) if raw_content else ""

                    sent_by = str(m.get("sentBy") or m.get("sent_by") or "")
                    agent   = m.get("agent") or {}
                    created = str(
                        m.get("createdAt") or m.get("created_at")
                        or m.get("timestamp") or ""
                    )

                    if agent or sent_by == "agent":
                        role = "consultant"
                    elif sent_by in ("contact", "user", "lead"):
                        role = "user"
                    else:
                        role = "consultant"

                    if not text:
                        continue

                    messages.append({
                        "role":         role,
                        "message":      text,
                        "created_at":   created,
                        "channel":      "tallos",
                        "contact_name": lead.get("contact_name", "") or "",
                    })

    if not messages:
        messages = await get_conversation_history(phone_number, limit=100)
        source = "local"

    return _r(request, "conversation_detail.html", {
        "phone_number": phone_number,
        "messages":     messages,
        "back_url":     back_url,
        "source":       source,
        "contact_id":   contact_id,
        "active_page":  "conversations",
    })


# ==================== Leads ====================

@router.get("/leads", response_class=HTMLResponse)
async def leads_list(request: Request):
    await _check_auth(request)
    leads = await get_all_leads()
    return _r(request, "leads.html", {"leads": leads, "active_page": "leads"})


@router.get("/leads/export")
async def leads_export(request: Request):
    """Exporta todos os leads PJ em CSV para download."""
    await _check_auth(request)
    leads = await get_all_leads()

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["phone_number", "contact_name", "email", "company", "job_title",
                    "servico", "identificador", "qtd_colaboradores",
                    "training_interest", "interest", "stage", "source_channel",
                    "created_at", "updated_at"],
        extrasaction="ignore",
    )
    writer.writeheader()
    for lead in leads:
        writer.writerow(lead)

    output.seek(0)
    filename = f"leads_pj_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ==================== System Prompt ====================

@router.get("/prompt", response_class=HTMLResponse)
async def prompt_page(request: Request):
    await _check_auth(request)
    current_prompt = await get_system_prompt()
    return _r(request, "prompt.html", {"prompt": current_prompt, "active_page": "prompt"})


@router.post("/prompt/save")
async def prompt_save(request: Request, prompt: str = Form(...)):
    await _check_auth(request)
    await set_system_prompt(prompt)
    return RedirectResponse(url="/admin/prompt", status_code=HTTP_303_SEE_OTHER)


# ==================== Bot Config ====================

@router.get("/bot-config", response_class=HTMLResponse)
async def bot_config_page(request: Request, saved: bool = False):
    await _check_auth(request)
    config = await get_bot_config()
    bot_enabled = config.get("bot_enabled", "true") == "true"
    return _r(request, "bot_config.html", {
        "active_page": "bot_config",
        "config": config,
        "bot_enabled": bot_enabled,
        "saved": saved,
    })


@router.post("/bot-config/save")
async def bot_config_save(request: Request):
    await _check_auth(request)
    form = await request.form()

    bool_keys = {
        "bot_enabled", "bot_schedule_enabled",
        "bot_schedule_weekend",
    }
    updates = {}
    for key in bool_keys:
        updates[key] = "true" if form.get(key) == "true" else "false"

    for key in (
        "bot_schedule_weekday_start", "bot_schedule_weekday_end",
    ):
        if key in form:
            updates[key] = str(form[key])

    if "escalation_message" in form:
        updates["escalation_message"] = str(form["escalation_message"])

    if "test_phone_numbers" in form:
        raw = str(form["test_phone_numbers"]).strip()
        phones = [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()]
        updates["test_phone_numbers"] = ", ".join(phones)

    if "escalation_flow_id" in form:
        updates["escalation_flow_id"] = str(form["escalation_flow_id"]).strip()

    if "bypass_hours_identificadores" in form:
        raw = str(form["bypass_hours_identificadores"]).strip()
        items = [i.strip() for i in raw.split("\n") if i.strip()]
        updates["bypass_hours_identificadores"] = "\n".join(items)

    await set_bot_config_bulk(updates)
    return RedirectResponse(url="/admin/bot-config?saved=true", status_code=HTTP_303_SEE_OTHER)


@router.post("/cache/clear")
async def clear_response_cache():
    """Limpa o cache de respostas da IA em memória."""
    from app.services.ai_engine import clear_cache
    clear_cache()
    return {"status": "ok", "message": "Cache limpo com sucesso."}


# ==================== Email Config ====================

_EMAIL_KEYS = {
    "email_notifications_enabled",
    "gmail_sender",
    "gmail_app_password",
    "email_recipients",
}


@router.get("/email-config", response_class=HTMLResponse)
async def email_config_page(request: Request, saved: bool = False):
    await _check_auth(request)
    config = await get_bot_config()
    return _r(request, "email_config.html", {
        "active_page": "email_config",
        "config": config,
        "saved": saved,
        "error": None,
    })


@router.post("/email-config/save")
async def email_config_save(request: Request):
    await _check_auth(request)
    form = await request.form()

    updates: dict = {}
    updates["email_notifications_enabled"] = (
        "true" if form.get("email_notifications_enabled") == "true" else "false"
    )

    for key in ("gmail_sender", "gmail_app_password", "email_recipients"):
        if key in form:
            updates[key] = str(form[key]).strip()

    await set_bot_config_bulk(updates)
    return RedirectResponse(url="/admin/email-config?saved=true", status_code=HTTP_303_SEE_OTHER)


@router.post("/email-config/test")
async def email_config_test(request: Request):
    """Envia um email de teste para validar as configurações."""
    await _check_auth(request)
    from app.services.email_service import send_lead_notification

    config = await get_bot_config()

    test_lead = {
        "contact_name":       "Lead PJ de Teste",
        "phone_number":       "5511900000000",
        "email":              "teste@empresa.com",
        "company":            "Empresa Teste LTDA",
        "job_title":          "Gerente de RH",
        "training_interest":  "Treinamento em Liderança",
        "ocorrencia":         __import__("datetime").datetime.now().strftime("%d/%m/%Y %H:%M"),
    }

    ok = await send_lead_notification(test_lead, config)
    if ok:
        return {"ok": True,  "message": "Email de teste enviado com sucesso! Verifique a caixa de entrada."}
    else:
        return {"ok": False, "message": "Falha ao enviar. Verifique as configurações de Gmail e destinatários."}


# ==================== Webhook Logs ====================

@router.get("/webhook-logs", response_class=HTMLResponse)
async def webhook_logs_page(request: Request, phone: str = "", limit: int = 50):
    await _check_auth(request)
    import json as _json
    logs = await get_webhook_logs(phone_number=phone, limit=min(limit, 200))
    for log in logs:
        try:
            log["payload_obj"] = _json.loads(log["raw_payload"])
            log["payload_fmt"] = _json.dumps(log["payload_obj"], indent=2, ensure_ascii=False)
        except Exception:
            log["payload_obj"] = {}
            log["payload_fmt"] = log.get("raw_payload", "")
    return _r(request, "webhook_logs.html", {
        "active_page": "webhook_logs",
        "logs": logs,
        "filter_phone": phone,
        "limit": limit,
    })


# ==================== Relatório Diário ====================

@router.get("/relatorio-diario", response_class=HTMLResponse)
async def relatorio_diario_page(request: Request):
    await _check_auth(request)
    report = await build_daily_report()
    config = await get_bot_config_full()
    cfg = {c["key"]: c["value"] for c in config}
    return _r(request, "relatorio_diario.html", {
        "active_page":           "relatorio_diario",
        "report_text":           report["full"],
        "chatpro_url":           cfg.get("chatpro_url", ""),
        "chatpro_token":         cfg.get("chatpro_token", ""),
        "report_recipients":     cfg.get("report_recipients", ""),
        "report_hour":           cfg.get("report_hour", "18"),
        "chatpro_template_name": cfg.get("report_template_name", "radarpj"),
        "chatpro_language_code": cfg.get("report_language_code", "pt_BR"),
    })


@router.post("/relatorio-diario/salvar")
async def relatorio_diario_salvar(request: Request):
    """Salva as configurações do relatório diário (ChatPro + destinatários + horário)."""
    await _check_auth(request)
    form = await request.form()
    updates = {}
    for key in (
        "chatpro_url", "chatpro_token", "chatpro_instance_id",
        "report_template_name", "report_language_code",
        "report_recipients", "report_hour",
    ):
        if key in form:
            updates[key] = str(form[key]).strip()

    await set_bot_config_bulk(updates)
    return JSONResponse({"ok": True, "message": "Configurações salvas com sucesso!"})


@router.get("/relatorio-diario/templates")
async def relatorio_listar_templates(request: Request):
    """Lista templates WABA aprovados na instância ChatPro."""
    await _check_auth(request)
    config = await get_bot_config_full()
    cfg = {c["key"]: c["value"] for c in config}
    chatpro_token = cfg.get("chatpro_token", "").strip()
    instance_id   = cfg.get("chatpro_instance_id", "chatpro-71f6d6f880").strip()
    if not chatpro_token:
        return JSONResponse({"ok": False, "templates": [], "message": "Token não configurado."})
    templates = await list_waba_templates(chatpro_token, instance_id)
    return JSONResponse({"ok": True, "templates": templates, "total": len(templates)})


@router.get("/relatorio-diario/preview")
async def relatorio_diario_preview(request: Request):
    """Retorna o texto atual do relatório (JSON) para atualização ao vivo."""
    await _check_auth(request)
    report = await build_daily_report()
    return JSONResponse({"text": report["full"]})


@router.post("/relatorio-diario/enviar")
async def relatorio_diario_enviar(request: Request):
    """Dispara o envio manual do relatório via ChatPro."""
    await _check_auth(request)
    config = await get_bot_config_full()
    cfg = {c["key"]: c["value"] for c in config}

    chatpro_token   = cfg.get("chatpro_token", "").strip()
    chatpro_url     = cfg.get("chatpro_url", "").strip()
    instance_id     = cfg.get("chatpro_instance_id", "chatpro-71f6d6f880").strip()
    template_name   = cfg.get("report_template_name", "").strip()
    language_code   = cfg.get("report_language_code", "pt_BR").strip()
    recipients_raw  = cfg.get("report_recipients", "")
    recipients = [n.strip() for n in recipients_raw.replace("\n", ",").split(",") if n.strip()]

    if not chatpro_token:
        return JSONResponse({"ok": False, "message": "Configure o token do ChatPro primeiro."})
    if not recipients:
        return JSONResponse({"ok": False, "message": "Nenhum número destinatário configurado."})

    report = await build_daily_report()
    results = await send_report_whatsapp(
        report, recipients, chatpro_url, chatpro_token, instance_id, template_name, language_code
    )

    total   = len(results)
    success = sum(1 for r in results.values() if r.get("ok"))
    return JSONResponse({
        "ok": success > 0,
        "message": f"Enviado para {success}/{total} número(s).",
        "details": results,
    })


# ── Consumo de Tokens ──────────────────────────────────────────────────────

@router.get("/tokens", response_class=HTMLResponse)
async def admin_tokens(request: Request):
    await _check_auth(request)

    from app.services.token_tracker import USD_TO_BRL

    totals  = await get_token_usage_totals()
    daily   = await get_token_usage_daily(days=30)
    by_svc  = await get_token_usage_by_service(period="month")
    by_mdl  = await get_token_usage_by_model(period="month")

    return _r(request, "token_usage.html", {
        "active_page": "tokens",
        "totals":      totals,
        "daily":       daily,
        "by_service":  by_svc,
        "by_model":    by_mdl,
        "usd_to_brl":  USD_TO_BRL,
    })


# ── Gerenciamento de Usuários do Radar ────────────────────────────────────────

@router.get("/radar-users", response_class=HTMLResponse)
async def admin_radar_users(request: Request):
    """Lista os usuários com acesso ao Radar PJ."""
    await _check_auth(request)
    users = await get_all_radar_users()
    return _r(request, "radar_users.html", {
        "active_page": "radar_users",
        "users": users,
    })


@router.post("/radar-users/create")
async def admin_radar_users_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(default="viewer"),
):
    """Cria um novo usuário do Radar."""
    await _check_auth(request)
    if not username.strip() or not password:
        users = await get_all_radar_users()
        return _r(request, "radar_users.html", {
            "active_page": "radar_users",
            "users": users,
            "error": "Usuário e senha são obrigatórios.",
        })
    ok = await create_radar_user(username.strip(), password, role)
    users = await get_all_radar_users()
    if ok:
        return _r(request, "radar_users.html", {
            "active_page": "radar_users",
            "users": users,
            "success": f"Usuário '{username.strip()}' criado com sucesso.",
        })
    return _r(request, "radar_users.html", {
        "active_page": "radar_users",
        "users": users,
        "error": f"Não foi possível criar o usuário. O nome '{username.strip()}' pode já estar em uso.",
    })


@router.post("/radar-users/delete/{user_id}")
async def admin_radar_users_delete(request: Request, user_id: int):
    """Apaga um usuário do Radar."""
    await _check_auth(request)
    await delete_radar_user(user_id)
    users = await get_all_radar_users()
    return _r(request, "radar_users.html", {
        "active_page": "radar_users",
        "users": users,
        "success": "Usuário removido com sucesso.",
    })
