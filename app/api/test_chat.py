"""Endpoint de teste para simular conversas sem RD Conversas — Bot SDR PJ."""

import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.core.database import (
    save_message, get_conversation_history, upsert_lead,
    get_bot_session, upsert_bot_session, get_bot_config,
    get_db,
)
from app.services.ai_engine import generate_response
from app.services.bot_controller import _notify_lead_escalation

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/test")

TEST_PHONE = "5511999990000"


class TestMessage(BaseModel):
    message: str
    phone: str = TEST_PHONE
    name: str = "Visitante Teste PJ"


@router.get("", response_class=HTMLResponse)
async def test_page(request: Request):
    """Interface de chat para testes do Bot SDR PJ."""
    html = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Teste — Bot SDR PJ</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #f0f2f5; display: flex; flex-direction: column; height: 100vh; }
  .top-bar { background: #1a1a2e; color: #fff; padding: 12px 20px;
             display: flex; align-items: center; gap: 12px; }
  .top-bar .avatar { width: 42px; height: 42px; background: #4f46e5; border-radius: 50%;
                     display: flex; align-items: center; justify-content: center;
                     font-size: 20px; flex-shrink: 0; }
  .top-bar .info h3 { font-size: 15px; font-weight: 600; }
  .top-bar .info p { font-size: 12px; color: #a0a0c0; }
  .top-bar .actions { margin-left: auto; display: flex; gap: 8px; }
  .top-bar button { background: rgba(255,255,255,0.1); color: #fff; border: none;
                    padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; }
  .top-bar button:hover { background: rgba(255,255,255,0.2); }
  .top-bar a { color: #a0a0c0; font-size: 12px; text-decoration: none; padding: 6px 12px;
               background: rgba(255,255,255,0.1); border-radius: 6px; }
  .top-bar a:hover { background: rgba(255,255,255,0.2); color: #fff; }
  #reactivate-bar { display: none; background: #fef3c7; color: #92400e;
                    padding: 10px 20px; font-size: 13px; text-align: center; font-weight: 500; }
  #reactivate-bar button { margin-left: 12px; background: #f59e0b; color: #fff;
                            border: none; border-radius: 6px; padding: 4px 12px; cursor: pointer; }
  .messages { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 12px; }
  .msg { max-width: 70%; padding: 10px 14px; border-radius: 12px; font-size: 14px; line-height: 1.5; }
  .msg.user { background: #dcf8c6; align-self: flex-end; border-bottom-right-radius: 4px; }
  .msg.bot { background: #fff; align-self: flex-start; border-bottom-left-radius: 4px;
             box-shadow: 0 1px 2px rgba(0,0,0,0.08); }
  .msg.consultant { background: #e0e7ff; align-self: flex-start; border-bottom-left-radius: 4px; }
  .msg .meta { font-size: 11px; color: #9ca3af; margin-top: 4px; text-align: right; }
  .msg.escalated { border: 2px solid #f59e0b; }
  .typing { display: none; align-self: flex-start; background: #fff; padding: 10px 14px;
            border-radius: 12px; font-size: 13px; color: #6b7280; }
  .input-bar { background: #fff; padding: 16px 20px; display: flex; gap: 10px;
               border-top: 1px solid #e5e7eb; }
  .input-bar input { flex: 1; padding: 10px 14px; border: 1px solid #d1d5db;
                     border-radius: 24px; font-size: 14px; outline: none; }
  .input-bar input:focus { border-color: #4f46e5; }
  .input-bar button { background: #4f46e5; color: #fff; border: none;
                      border-radius: 50%; width: 42px; height: 42px; cursor: pointer;
                      font-size: 18px; flex-shrink: 0; }
  .input-bar button:hover { background: #4338ca; }
  .phone-row { padding: 8px 20px; background: #f9fafb; border-top: 1px solid #f3f4f6;
               display: flex; align-items: center; gap: 8px; font-size: 12px; color: #6b7280; }
  .phone-row input { padding: 4px 8px; border: 1px solid #d1d5db; border-radius: 6px;
                     font-size: 12px; font-family: monospace; width: 160px; }
  .badge-pj { background: #dbeafe; color: #1e40af; padding: 2px 8px; border-radius: 12px;
              font-size: 11px; font-weight: 600; margin-left: 4px; }
  .status-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%;
                background: #6b7280; margin-left: 6px; vertical-align: middle;
                flex-shrink: 0; transition: background 0.3s; }
  .status-dot.online { background: #22c55e; box-shadow: 0 0 0 0 rgba(34,197,94,0.6);
                       animation: pulse-green 2s infinite; }
  .status-dot.offline { background: #ef4444; }
  @keyframes pulse-green {
    0%   { box-shadow: 0 0 0 0 rgba(34,197,94,0.6); }
    70%  { box-shadow: 0 0 0 7px rgba(34,197,94,0); }
    100% { box-shadow: 0 0 0 0 rgba(34,197,94,0); }
  }
</style>
</head>
<body>
<div class="top-bar">
  <div class="avatar">🏢</div>
  <div class="info">
    <h3>Bot SDR PJ <span class="badge-pj">Teste</span><span id="status-dot" class="status-dot" title="Verificando..."></span></h3>
    <p>Departamento de Treinamentos Corporativos</p>
  </div>
  <div class="actions">
    <button onclick="reloadHistory()">🔄 Recarregar</button>
    <button onclick="clearHistory()" style="background:rgba(239,68,68,0.3)">🗑 Limpar</button>
    <a href="/pj/admin">⚙️ Admin</a>
  </div>
</div>
<div id="reactivate-bar">
  🤖 Bot pausado — atendente humano ativo nesta conversa.
  <button onclick="reactivateBot()">🔄 Reativar Bot</button>
</div>
<div class="messages" id="messages">
  <div class="typing" id="typing">🏢 Bot SDR PJ está digitando...</div>
</div>
<div class="phone-row">
  <span>Número:</span>
  <input type="text" id="phone-input" value="5511999990000" placeholder="5511999990000">
  <span>Nome:</span>
  <input type="text" id="name-input" value="Lead Teste PJ" style="width:140px">
</div>
<div class="input-bar">
  <input type="text" id="msg-input" placeholder="Digite uma mensagem..." autocomplete="off">
  <button onclick="sendMessage()">➤</button>
</div>
<script>
  let botPaused = false;

  function getPhone() { return document.getElementById('phone-input').value.trim() || '5511999990000'; }
  function getName()  { return document.getElementById('name-input').value.trim()  || 'Lead Teste PJ'; }

  function appendMsg(role, text, extra) {
    const msgs = document.getElementById('messages');
    const typing = document.getElementById('typing');
    const div = document.createElement('div');
    div.className = 'msg ' + role + (extra && extra.escalated ? ' escalated' : '');
    const pre = document.createElement('div');
    pre.style.whiteSpace = 'pre-wrap';
    pre.textContent = text;
    div.appendChild(pre);
    const meta = document.createElement('div');
    meta.className = 'meta';
    meta.textContent = new Date().toLocaleTimeString('pt-BR', {hour:'2-digit',minute:'2-digit'});
    if (extra && extra.escalated) meta.textContent += ' 🚨 escalonado';
    div.appendChild(meta);
    msgs.insertBefore(div, typing);
    msgs.scrollTop = msgs.scrollHeight;
  }

  async function sendMessage() {
    const input = document.getElementById('msg-input');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    appendMsg('user', text);
    const typing = document.getElementById('typing');
    typing.style.display = 'block';
    document.getElementById('messages').scrollTop = 99999;
    try {
      const res = await fetch('/pj/test/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, phone: getPhone(), name: getName() })
      });
      const data = await res.json();
      typing.style.display = 'none';
      if (data.status === 'agent_active') {
        botPaused = true;
        document.getElementById('reactivate-bar').style.display = 'block';
        appendMsg('bot', data.response);
      } else {
        appendMsg('bot', data.response, { escalated: data.escalated });
        if (data.escalated) {
          botPaused = true;
          document.getElementById('reactivate-bar').style.display = 'block';
        }
      }
    } catch(e) {
      typing.style.display = 'none';
      appendMsg('bot', '❌ Erro de conexão: ' + e.message);
    }
  }

  async function reactivateBot() {
    await fetch('/pj/test/reactivate?phone=' + getPhone(), { method: 'POST' });
    botPaused = false;
    document.getElementById('reactivate-bar').style.display = 'none';
    appendMsg('bot', '✅ Bot reativado. Pode continuar testando!');
  }

  async function reloadHistory() {
    const res = await fetch('/pj/test/history?phone=' + getPhone());
    const data = await res.json();
    const msgs = document.getElementById('messages');
    const typing = document.getElementById('typing');
    // Remove tudo exceto o typing
    Array.from(msgs.children).forEach(c => { if (c !== typing) c.remove(); });
    (data.messages || []).forEach(m => appendMsg(m.role === 'user' ? 'user' : (m.role === 'consultant' ? 'consultant' : 'bot'), m.message));
  }

  async function clearHistory() {
    if (!confirm('Limpar histórico desta conversa?')) return;
    await fetch('/pj/test/clear?phone=' + getPhone(), { method: 'POST' });
    const msgs = document.getElementById('messages');
    const typing = document.getElementById('typing');
    Array.from(msgs.children).forEach(c => { if (c !== typing) c.remove(); });
    botPaused = false;
    document.getElementById('reactivate-bar').style.display = 'none';
  }

  document.getElementById('msg-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });

  async function checkStatus() {
    const dot = document.getElementById('status-dot');
    try {
      const res = await fetch('/pj/test/status');
      const data = await res.json();
      if (data.ok) {
        dot.className = 'status-dot online';
        dot.title = 'Bot online ✅';
      } else {
        dot.className = 'status-dot offline';
        dot.title = 'Bot com problemas ⚠️';
      }
    } catch(e) {
      dot.className = 'status-dot offline';
      dot.title = 'Bot offline ❌';
    }
  }

  checkStatus();
  setInterval(checkStatus, 30000);

  reloadHistory();
</script>
</body>
</html>"""
    return HTMLResponse(html)


@router.post("/send")
async def test_send_message(data: TestMessage):
    """Simula envio de mensagem como se fosse do RD Conversas."""
    try:
        phone = data.phone
        name  = data.name

        cfg = await get_bot_config()
        raw_test_phones = cfg.get("test_phone_numbers", "")
        test_phones = [p.strip() for p in raw_test_phones.replace("\n", ",").split(",") if p.strip()]
        is_test_phone = phone in test_phones

        session = await get_bot_session(phone)
        if not is_test_phone and session and session.get("agent_active"):
            return {
                "response": (
                    "🔇 [Modo teste] Bot pausado — atendente humano ativo.\n"
                    "Clique em '🔄 Reativar Bot' para reiniciar."
                ),
                "status": "agent_active",
                "escalated": False,
            }

        await save_message(phone, "user", data.message, name, channel="test")
        await upsert_lead(phone, name)
        await upsert_bot_session(phone, last_user_msg_at=datetime.now(timezone.utc).isoformat())

        history = await get_conversation_history(phone, limit=20)
        is_returning = len(history) > 1

        ai_response, needs_escalation = await generate_response(
            phone_number=phone,
            user_message=data.message,
            contact_name=name,
            is_returning_lead=is_returning,
            prefetched_history=history,
        )

        await save_message(phone, "assistant", ai_response, "Bot SDR PJ", channel="test")
        now_iso = datetime.now(timezone.utc).isoformat()

        if needs_escalation:
            logger.info(f"[TEST][{phone}] Escalação detectada")
            await upsert_bot_session(phone, last_bot_msg_at=now_iso, agent_active=1)
            await _notify_lead_escalation(phone, name, cfg, history)
        else:
            await upsert_bot_session(phone, last_bot_msg_at=now_iso)

        return {
            "response":  ai_response,
            "status":    "escalated" if needs_escalation else "ok",
            "escalated": needs_escalation,
        }

    except Exception as e:
        logger.error(f"[TEST] Erro: {e}", exc_info=True)
        return {"response": f"Erro: {str(e)}", "status": "error"}


@router.post("/reactivate")
async def test_reactivate(phone: str = TEST_PHONE):
    await upsert_bot_session(phone, agent_active=0)
    return {"status": "ok", "message": "Bot reativado."}


@router.get("/history")
async def test_history(phone: str = TEST_PHONE):
    messages = await get_conversation_history(phone, limit=50)
    return {"messages": messages}


@router.post("/clear")
async def test_clear(phone: str = TEST_PHONE):
    db = await get_db()
    try:
        await db.execute("DELETE FROM conversations WHERE phone_number=?", (phone,))
        await db.execute("DELETE FROM bot_sessions WHERE phone_number=?", (phone,))
        await db.commit()
    finally:
        await db.close()
    return {"status": "ok", "message": "Histórico limpo."}


@router.get("/status")
async def test_status():
    """Retorna se o bot está operacional (usado pelo farol na interface de teste)."""
    try:
        db = await get_db()
        try:
            await db.execute("SELECT 1 FROM bot_config LIMIT 1")
        finally:
            await db.close()
        return {"ok": True, "db": True}
    except Exception as e:
        logger.warning(f"[TEST][status] {e}")
        return {"ok": False, "db": False, "error": str(e)}
