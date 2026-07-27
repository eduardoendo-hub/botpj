"""
Serviço de notificação por email — Gmail SMTP — Bot SDR PJ.

Enviado automaticamente quando um novo lead PJ é registrado
(Nome, WhatsApp/Telefone, Cargo, Data/Hora, Produto, Origem).
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Fuso de São Paulo (UTC-3) — o container roda em UTC, então datas nos emails DEVEM usar isto.
_BRT = timezone(timedelta(hours=-3))


def _build_html(lead: Dict) -> str:
    nome       = lead.get("contact_name") or lead.get("nome") or "—"
    whatsapp   = lead.get("phone_number") or lead.get("whatsapp") or "—"
    email      = lead.get("email") or "—"
    empresa    = lead.get("company") or lead.get("empresa") or "—"
    cargo      = lead.get("job_title") or lead.get("cargo") or "—"
    produto    = lead.get("produto") or lead.get("training_interest") or lead.get("treinamento") or "—"
    origem     = lead.get("origem") or lead.get("source_channel") or "—"
    ocorrencia = lead.get("ocorrencia") or datetime.now(_BRT).strftime("%d/%m/%Y %H:%M")
    resumo     = lead.get("resumo") or ""

    resumo_block = ""
    if resumo:
        resumo_block = f"""
      <div class="resumo-box">
        <div class="resumo-title">📝 Resumo da Conversa</div>
        <div class="resumo-text">{resumo}</div>
      </div>"""

    return f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f3f4f6; margin: 0; padding: 20px; }}
    .container {{ max-width: 560px; margin: 0 auto; background: #fff;
                  border-radius: 12px; overflow: hidden;
                  box-shadow: 0 2px 8px rgba(0,0,0,.08); }}
    .header {{ background: #1a1a2e; color: #fff; padding: 24px 28px; }}
    .header h1 {{ margin: 0; font-size: 20px; }}
    .header p  {{ margin: 6px 0 0; font-size: 13px; color: #a0a0c0; }}
    .body {{ padding: 28px; }}
    .badge {{ display: inline-block; background: #dbeafe; color: #1e40af;
              padding: 4px 12px; border-radius: 20px; font-size: 12px;
              font-weight: 600; margin-bottom: 20px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    td {{ padding: 10px 0; border-bottom: 1px solid #f3f4f6; font-size: 14px; }}
    td:first-child {{ color: #6b7280; width: 160px; font-weight: 500; }}
    td:last-child {{ color: #111827; font-weight: 600; }}
    .resumo-box {{ margin-top: 24px; background: #f8faff; border-left: 4px solid #3b82f6;
                   border-radius: 0 8px 8px 0; padding: 16px 18px; }}
    .resumo-title {{ font-size: 13px; font-weight: 700; color: #1e40af; margin-bottom: 8px; }}
    .resumo-text {{ font-size: 14px; color: #374151; line-height: 1.6; white-space: pre-wrap; }}
    .footer {{ background: #f9fafb; padding: 16px 28px; font-size: 12px;
               color: #9ca3af; text-align: center; border-top: 1px solid #f3f4f6;
               margin-top: 24px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>🏢 Novo Lead PJ</h1>
      <p>Um novo lead corporativo foi registrado no Bot SDR PJ.</p>
    </div>
    <div class="body">
      <span class="badge">✅ Novo lead recebido</span>
      <table>
        <tr><td>👤 Nome</td><td>{nome}</td></tr>
        <tr><td>📱 WhatsApp / Telefone</td><td>{whatsapp}</td></tr>
        <tr><td>✉️ Email</td><td>{email}</td></tr>
        <tr><td>🏢 Empresa</td><td>{empresa}</td></tr>
        <tr><td>💼 Cargo</td><td>{cargo}</td></tr>
        <tr><td>🕐 Data / Hora</td><td>{ocorrencia}</td></tr>
        <tr><td>🎯 Produto</td><td>{produto}</td></tr>
        <tr><td>📌 Origem</td><td>{origem}</td></tr>
      </table>{resumo_block}
    </div>
    <div class="footer">
      Enviado automaticamente pelo Bot SDR PJ — Departamento de Treinamentos
    </div>
  </div>
</body>
</html>
"""


def _build_plain(lead: Dict) -> str:
    nome       = lead.get("contact_name") or lead.get("nome") or "—"
    whatsapp   = lead.get("phone_number") or lead.get("whatsapp") or "—"
    email      = lead.get("email") or "—"
    empresa    = lead.get("company") or lead.get("empresa") or "—"
    cargo      = lead.get("job_title") or lead.get("cargo") or "—"
    produto    = lead.get("produto") or lead.get("training_interest") or lead.get("treinamento") or "—"
    origem     = lead.get("origem") or lead.get("source_channel") or "—"
    ocorrencia = lead.get("ocorrencia") or datetime.now(_BRT).strftime("%d/%m/%Y %H:%M")
    resumo     = lead.get("resumo") or ""

    body = (
        f"Novo Lead PJ — Bot SDR PJ\n\n"
        f"Nome:              {nome}\n"
        f"WhatsApp/Telefone: {whatsapp}\n"
        f"Email:             {email}\n"
        f"Empresa:           {empresa}\n"
        f"Cargo:             {cargo}\n"
        f"Data / Hora:       {ocorrencia}\n"
        f"Produto:           {produto}\n"
        f"Origem:            {origem}\n"
    )
    if resumo:
        body += f"\n── Resumo da Conversa ──\n{resumo}\n"
    body += "\nEnviado automaticamente pelo Bot SDR PJ"
    return body


def _parse_recipients(raw: str) -> List[str]:
    import re
    parts = re.split(r"[,;\n]+", raw or "")
    return [p.strip() for p in parts if p.strip() and "@" in p]


async def send_lead_notification(lead: Dict, config: Dict) -> bool:
    """
    Envia email de notificação para os consultores.
    Retorna True se enviado com sucesso.
    """
    import asyncio

    enabled = str(config.get("email_notifications_enabled", "false")).lower()
    if enabled not in ("true", "1", "yes", "sim"):
        logger.debug("Notificações por email desabilitadas.")
        return False

    sender   = (config.get("gmail_sender") or "").strip()
    password = (config.get("gmail_app_password") or "").strip()
    raw_recipients = config.get("email_recipients") or ""
    recipients = _parse_recipients(raw_recipients)

    if not sender or not password or not recipients:
        logger.warning(
            "Email não enviado: configuração incompleta "
            f"(sender={bool(sender)}, password={bool(password)}, recipients={recipients})"
        )
        return False

    # Fallbacks para quando o form chega com campos nulos
    nome    = (lead.get("contact_name") or lead.get("nome") or "").strip()
    email_c = (lead.get("email") or "").strip()
    produto = (lead.get("produto") or lead.get("training_interest") or lead.get("treinamento") or "").strip()
    origem  = (lead.get("origem") or lead.get("source_channel") or "").strip()

    # Subject: usa email do lead quando não tem nome, e identificador quando não tem produto
    nome_subject   = nome or email_c or lead.get("phone_number") or "Lead PJ"
    produto_subject = produto or origem or "Novo lead PJ"

    subject_parts = [nome_subject]
    if origem and origem != nome_subject:
        subject_parts.append(origem)
    subject = f"🏢 Novo lead PJ: {' — '.join(subject_parts)} | {produto_subject}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Bot SDR PJ <{sender}>"
    msg["To"]      = ", ".join(recipients)

    msg.attach(MIMEText(_build_plain(lead), "plain", "utf-8"))
    msg.attach(MIMEText(_build_html(lead),  "html",  "utf-8"))

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _smtp_send, sender, password, recipients, msg)
        logger.info(f"Email de notificação enviado para {recipients} (lead PJ: {nome})")
        return True
    except Exception as e:
        logger.error(f"Falha ao enviar email de notificação: {e}")
        return False


def _smtp_send(sender: str, password: str, recipients: List[str], msg: MIMEMultipart):
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
        server.ehlo()
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, recipients, msg.as_string())


def _build_turma_fechada_html(lead: Dict) -> str:
    """Email dedicado do alerta de turma fechada — foco no que o comercial precisa."""
    def g(*keys):
        for k in keys:
            v = lead.get(k)
            if v not in (None, "", "desconhecido"):
                return str(v)
        return ""

    empresa   = g("company", "empresa") or "—"
    contato   = g("contact_name", "nome") or "—"
    cargo     = g("job_title", "cargo")
    whatsapp  = g("phone_number", "whatsapp") or "—"
    email_c   = g("email")
    treino    = g("training_interest", "tema_interesse", "produto", "servico")
    qtd       = g("qtd_participantes", "qtd_colaboradores")
    formato   = g("formato")
    cidade    = g("cidade")
    prazo     = g("prazo")
    urgencia  = g("urgencia")
    objetivo  = g("objetivo_negocio")
    temp      = g("lead_temperature")
    score     = g("score")
    atendido  = g("atendido_por")
    resumo    = g("resumo")
    ocorr     = g("ocorrencia") or datetime.now(_BRT).strftime("%d/%m/%Y %H:%M")

    def row(label, value):
        return f'<tr><td>{label}</td><td>{value}</td></tr>' if value else ""

    contato_full = contato + (f" — {cargo}" if cargo else "")
    contato_line = whatsapp + (f"  ·  {email_c}" if email_c else "")

    resumo_block = ""
    if resumo:
        resumo_block = f'<div class="resumo-box"><div class="resumo-title">📝 Resumo da Conversa</div><div class="resumo-text">{resumo}</div></div>'

    # Bloco de inteligência da empresa (pesquisa web via Claude, mesmo do Radar) — ~5 linhas
    intel = lead.get("empresa_intel") or {}
    desc = (intel.get("descricao") or "").strip()
    intel_block = ""
    if desc and "não foram encontradas" not in desc.lower():
        _func = (intel.get("funcionarios") or "").strip()
        meta = " · ".join(x for x in [
            intel.get("setor"),
            (f"👥 {_func} func." if _func and _func.lower() != "não identificado" else ""),
            intel.get("porte"), intel.get("cidade"),
        ] if x)
        desc_short = desc[:600] + ("…" if len(desc) > 600 else "")
        intel_block = (
            '<div class="intel-box"><div class="intel-title">🔎 Sobre a empresa</div>'
            f'<div class="intel-text">{_sd_esc(desc_short)}</div>'
            + (f'<div class="intel-meta">{_sd_esc(meta)}</div>' if meta else "")
            + '</div>'
        )
    # Link de busca no LinkedIn (consultor clica e confere a pessoa) — sem raspagem/privacidade
    linkedin_link = ""
    if contato and contato != "—":
        import urllib.parse as _up
        _q = _up.quote(f"{contato} {empresa if empresa != '—' else ''}".strip())
        linkedin_link = (
            f'<a href="https://www.linkedin.com/search/results/people/?keywords={_q}" '
            'style="display:inline-block;margin:8px 0 4px;font-size:13px;color:#0a66c2;font-weight:700;text-decoration:none;">'
            '🔗 Buscar esta pessoa no LinkedIn →</a>'
        )

    return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><style>
    body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:#f3f4f6; margin:0; padding:20px; }}
    .container {{ max-width:580px; margin:0 auto; background:#fff; border-radius:12px; overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,.08); }}
    .header {{ background:#7c2d12; color:#fff; padding:24px 28px; }}
    .header h1 {{ margin:0; font-size:20px; }}
    .header p {{ margin:6px 0 0; font-size:13px; color:#fed7aa; }}
    .body {{ padding:24px 28px; }}
    .badge {{ display:inline-block; background:#ffedd5; color:#9a3412; padding:4px 12px; border-radius:20px; font-size:12px; font-weight:700; margin-bottom:18px; }}
    .section {{ font-size:12px; font-weight:700; color:#9a3412; text-transform:uppercase; letter-spacing:.5px; margin:18px 0 6px; }}
    table {{ width:100%; border-collapse:collapse; }}
    td {{ padding:9px 0; border-bottom:1px solid #f3f4f6; font-size:14px; vertical-align:top; }}
    td:first-child {{ color:#6b7280; width:150px; font-weight:500; }}
    td:last-child {{ color:#111827; font-weight:600; }}
    .highlight td:last-child {{ color:#9a3412; font-size:16px; }}
    .resumo-box {{ margin-top:20px; background:#fff7ed; border-left:4px solid #ea580c; border-radius:0 8px 8px 0; padding:16px 18px; }}
    .resumo-title {{ font-size:13px; font-weight:700; color:#9a3412; margin-bottom:8px; }}
    .resumo-text {{ font-size:14px; color:#374151; line-height:1.6; white-space:pre-wrap; }}
    .intel-box {{ margin-top:10px; background:#f8faff; border-left:4px solid #2563eb; border-radius:0 8px 8px 0; padding:12px 16px; }}
    .intel-title {{ font-size:12px; font-weight:700; color:#1e40af; margin-bottom:6px; }}
    .intel-text {{ font-size:13.5px; color:#374151; line-height:1.55; }}
    .intel-meta {{ font-size:12px; color:#2563eb; font-weight:600; margin-top:6px; }}
    .footer {{ background:#f9fafb; padding:14px 28px; font-size:12px; color:#9ca3af; text-align:center; border-top:1px solid #f3f4f6; }}
    </style></head><body><div class="container">
    <div class="header"><h1>🎯 Alerta — Turma Fechada</h1><p>Lead corporativo / in company identificado no Bot SDR PJ.</p></div>
    <div class="body">
      <span class="badge">🏢 Oportunidade corporativa</span>
      <div class="section">Empresa & contato</div>
      <table>{row("🏢 Empresa", empresa)}{row("👤 Contato", contato_full)}{row("📱 WhatsApp / E-mail", contato_line)}</table>
      {linkedin_link}
      {intel_block}
      <div class="section">Oportunidade</div>
      <table class="highlight">{row("🎓 Treinamento", treino)}{row("👥 Participantes", qtd)}{row("🖥️ Formato", formato)}</table>
      <table>{row("📍 Cidade", cidade)}{row("⏱️ Prazo", prazo)}{row("🔥 Urgência", urgencia)}{row("🎯 Objetivo", objetivo)}</table>
      <div class="section">Qualificação</div>
      <table>{row("🌡️ Temperatura", temp)}{row("⭐ Score", score)}{row("💬 Atendido por", atendido)}{row("🕐 Registrado em", ocorr)}</table>
      {resumo_block}
    </div>
    <div class="footer">Bot SDR PJ · alerta automático de turma fechada</div>
    </div></body></html>"""


def _sd_esc(v):
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")) if v else ""


def _sd_ligar(ligar):
    """Renderiza a lista 'ligar hoje' de um segmento."""
    if not ligar:
        return ""
    rows = ""
    for l in ligar[:6]:
        if isinstance(l, dict):
            tel = _sd_esc(l.get("telefone"))
            curso = _sd_esc(l.get("curso"))
            curso_tag = (f'<span style="display:inline-block;background:#fee2e2;color:#991b1b;font-size:11px;'
                         f'font-weight:700;padding:1px 8px;border-radius:10px;margin-left:6px;">📘 {curso}</span>'
                         if curso else "")
            rows += (f'<div style="padding:9px 13px;margin:7px 0;background:#fef2f2;border-left:4px solid #dc2626;'
                     f'border-radius:0 8px 8px 0;"><div style="font-weight:700;color:#991b1b;font-size:13.5px;">'
                     f'📞 {_sd_esc(l.get("quem"))}{f" — {tel}" if tel else ""}{curso_tag}</div>'
                     f'<div style="font-size:13px;color:#374151;margin-top:2px;">{_sd_esc(l.get("motivo"))}</div></div>')
        else:
            rows += f'<div style="font-size:13.5px;margin:5px 0;">📞 {_sd_esc(l)}</div>'
    return rows


def _sd_oportunidades(ops):
    if not ops:
        return ""
    rows = ""
    for o in ops[:5]:
        if isinstance(o, dict):
            rows += (f'<div style="background:#f0fdf4;border-left:4px solid #16a34a;border-radius:0 8px 8px 0;'
                     f'padding:9px 13px;margin:7px 0;"><div style="font-weight:700;color:#166534;font-size:13.5px;">'
                     f'{_sd_esc(o.get("titulo"))}</div><div style="font-size:13px;color:#374151;margin-top:2px;">'
                     f'{_sd_esc(o.get("detalhe"))}</div></div>')
        else:
            rows += (f'<div style="background:#f0fdf4;border-left:4px solid #16a34a;border-radius:0 8px 8px 0;'
                     f'padding:9px 13px;margin:7px 0;font-size:13.5px;color:#374151;">{_sd_esc(o)}</div>')
    return rows


def _build_operacional_html(op: Dict) -> str:
    """Segunda visão do email — OPERACIONAL (supervisor de call center). Estilo escuro, separado."""
    if not op:
        return ""
    esc = _sd_esc
    resumo = op.get("resumo", {}) or {}
    sup = op.get("supervisor", {}) or {}
    casos = op.get("casos", []) or []
    abandonos = op.get("abandonos", []) or []

    def exp_badge(exp):
        if exp:
            return ('<span style="display:inline-block;background:#334155;color:#e2e8f0;font-size:10.5px;'
                    'font-weight:700;padding:1px 8px;border-radius:10px;">no expediente</span>')
        return ('<span style="display:inline-block;background:#7c2d12;color:#fed7aa;font-size:10.5px;'
                'font-weight:700;padding:1px 8px;border-radius:10px;">🌙 fora do expediente</span>')

    # Cards de resumo (operacional)
    def opcard(num, label, cor="#f59e0b"):
        return (f'<td style="padding:5px;"><div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;'
                f'padding:12px 8px;text-align:center;"><div style="font-size:22px;font-weight:800;color:{cor};">{num}</div>'
                f'<div style="font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:.4px;">{label}</div></div></td>')

    cards = ("<table style='width:100%;border-collapse:collapse;'><tr>"
             + opcard(resumo.get("demoras", 0), "Demoras", "#f59e0b")
             + opcard(resumo.get("demoras_sem_retorno", 0), "Sem retorno", "#f87171")
             + opcard(resumo.get("abandonos", 0), "Abandonos", "#ef4444")
             + opcard(resumo.get("maior_espera", "—"), "Maior espera", "#fbbf24")
             + "</tr></table>")

    def _hora_tag(h):
        return (f'<span style="color:#64748b;font-variant-numeric:tabular-nums;font-weight:600;">{esc(h)}</span> '
                if h else "")

    def _msg_lines(msgs, color, prefix):
        # msgs = lista de (hora, texto)
        return "".join(
            f'<div style="font-size:12px;color:{color};margin-top:3px;line-height:1.45;">{prefix} {_hora_tag(h)}“{esc((m or "")[:170])}”</div>'
            for h, m in msgs if m
        )

    def _rotulo(txt):
        return (f'<div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;'
                f'margin-top:6px;">{txt}</div>')

    # Casos de demora — com 2 falas do cliente + 2 do consultor
    casos_html = ""
    for c in casos[:10]:
        cli = _msg_lines((c.get("cliente_msgs") or [])[-2:], "#cbd5e1", "🗣️")
        con = _msg_lines((c.get("consultor_msgs") or [])[:2], "#93c5fd", "↩️")
        cons_nome = esc(c.get("consultor")) or "consultor não identificado"
        cont = c.get("continuou")
        if cont is True:
            desf = ('<div style="margin-top:9px;font-size:12px;font-weight:700;color:#4ade80;">'
                    '✅ Cliente continuou a conversa depois — a demora não travou o atendimento</div>')
        elif cont is False:
            desf = ('<div style="margin-top:9px;font-size:12px;font-weight:700;color:#f87171;">'
                    '⚠️ Cliente NÃO voltou após a resposta — possível abandono pela demora</div>')
        else:
            desf = ''
        casos_html += (
            '<div style="border-left:4px solid #f59e0b;background:#0f172a;border-radius:0 8px 8px 0;padding:11px 14px;margin:9px 0;">'
            f'<div style="font-size:13.5px;color:#f1f5f9;font-weight:700;">🕐 {esc(c.get("hora"))} · '
            f'<span style="color:#fbbf24;">esperou {esc(c.get("espera"))}</span> &nbsp;{exp_badge(c.get("exp"))}</div>'
            f'<div style="font-size:12.5px;color:#cbd5e1;margin-top:4px;">👤 atendido por '
            f'<b style="color:#e2e8f0;">({cons_nome})</b> · {esc(c.get("label"))}</div>'
            f'<div style="margin-top:8px;padding-top:8px;border-top:1px solid #1e293b;">'
            f'{_rotulo("Cliente")}{cli}{_rotulo(f"Consultor ({cons_nome})")}{con}</div>'
            f'{desf}</div>'
        )
    if not casos_html:
        casos_html = '<div style="font-size:13px;color:#94a3b8;">Nenhuma demora acima do SLA. 👏</div>'

    # Abandonos — com contexto do que se tratava
    ab_html = ""
    for a in abandonos[:8]:
        ctx = a.get("contexto") or []
        ctx_html = "".join(
            f'<div style="font-size:12px;color:{"#cbd5e1" if r == "user" else "#93c5fd"};margin-top:3px;line-height:1.45;">'
            f'{"🗣️" if r == "user" else "↩️"} {_hora_tag(h)}“{esc((m or "")[:160])}”</div>'
            for r, h, m in ctx[-4:] if m
        )
        ab_html += (
            '<div style="border-left:4px solid #ef4444;background:#0f172a;border-radius:0 8px 8px 0;padding:10px 14px;margin:8px 0;">'
            f'<div style="font-size:13px;color:#fecaca;font-weight:700;">🚪 {esc(a.get("hora"))} · sem resposta &nbsp;{exp_badge(a.get("exp"))}</div>'
            f'<div style="font-size:12.5px;color:#cbd5e1;margin-top:3px;">👤 atendido por '
            f'<b style="color:#e2e8f0;">({esc(a.get("consultor")) or "não identificado"})</b> · {esc(a.get("label"))}</div>'
            f'<div style="margin-top:8px;padding-top:8px;border-top:1px solid #1e293b;">'
            f'{_rotulo("Do que se tratava")}{ctx_html}</div></div>'
        )
    if not ab_html:
        ab_html = '<div style="font-size:13px;color:#94a3b8;">Nenhum abandono registrado. 👏</div>'

    def op_ul(items, cor="#cbd5e1"):
        if not items:
            return ""
        lis = "".join(f'<li style="margin:5px 0;line-height:1.5;">{esc(x)}</li>' for x in items if x)
        return f'<ul style="margin:6px 0 0;padding-left:20px;font-size:13.5px;color:{cor};">{lis}</ul>'

    def op_section(title, inner):
        return (f'<div style="font-size:12px;font-weight:800;color:#fbbf24;text-transform:uppercase;'
                f'letter-spacing:.5px;margin:22px 0 8px;">{title}</div>{inner}') if inner else ""

    leitura = esc(sup.get("leitura"))
    leitura_box = (f'<div style="background:#1e293b;border-radius:10px;padding:14px 16px;font-size:14px;'
                   f'color:#e2e8f0;line-height:1.5;">🎧 {leitura}</div>') if leitura else ""

    return f"""
    <div style="background:#0b1220;padding:2px 0;">
      <div style="background:#111c34;color:#fff;padding:20px 30px;border-top:3px solid #f59e0b;">
        <div style="font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#fbbf24;font-weight:700;">⏱️ Visão operacional · separada da estratégica</div>
        <div style="font-size:19px;font-weight:800;margin-top:4px;">Supervisão de Atendimento</div>
        <div style="font-size:12.5px;color:#94a3b8;margin-top:3px;">Tempos reais de resposta · SLA {op.get('sla_min','?')} min · Expediente {esc(op.get('expediente',''))}</div>
      </div>
      <div style="background:#0b1220;padding:22px 30px 28px;">
        {leitura_box}
        <div style="margin:16px 0 4px;">{cards}</div>
        {op_section("⏳ Maiores demoras de resposta", casos_html)}
        {op_section("🚪 Abandonos (cliente sem resposta)", ab_html)}
        {op_section("📌 Padrões observados", op_ul(sup.get("padroes")))}
        {op_section("🛠️ Recomendações operacionais", op_ul(sup.get("recomendacoes")))}
      </div>
    </div>"""


def _build_sales_digest_html(digest: Dict) -> str:
    """Email 'Copiloto do Gestor' — resumo diário de Treinamentos, separado PF × PJ + funil do CRM."""
    from datetime import datetime as _dt
    m = digest.get("metrics", {}) or {}
    a = digest.get("analysis", {}) or {}
    dia = digest.get("dia", "")
    _WD = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
    try:
        _d = _dt.strptime(dia, "%Y-%m-%d")
        dia_fmt = f"{_WD[_d.weekday()]}, {_d.strftime('%d/%m/%Y')}"
    except Exception:
        dia_fmt = dia

    esc = _sd_esc
    pf = m.get("pf", {}) or {}
    pj = m.get("pj", {}) or {}
    atend = m.get("atendimento", {}) or {}
    quentes = pf.get("quentes", 0) + pj.get("quentes", 0)

    def card(num, label, color):
        return (f'<td style="padding:6px;"><div style="background:#f8fafc;border:1px solid #e5e7eb;'
                f'border-radius:10px;padding:14px 8px;text-align:center;">'
                f'<div style="font-size:26px;font-weight:800;color:{color};">{num}</div>'
                f'<div style="font-size:10.5px;color:#6b7280;text-transform:uppercase;letter-spacing:.4px;">{label}</div>'
                f'</div></td>')

    cards = (
        "<table style='width:100%;border-collapse:collapse;'><tr>"
        + card(m.get("total_conversas", 0), "Conversas", "#1e40af")
        + card(pf.get("conversas", 0), "PF", "#0891b2")
        + card(pj.get("conversas", 0), "PJ", "#7c3aed")
        + card(quentes, "Quentes", "#dc2626")
        + "</tr></table>"
    )

    def seg_line(nome, d, cor):
        parts = [f"{d.get('novos',0)} novos", f"{d.get('quentes',0)} quentes"]
        if d.get("turma_fechada"):
            parts.append(f"{d['turma_fechada']} turma fechada")
        return (f'<span style="color:{cor};font-weight:700;">{nome}</span> '
                f'<span style="color:#6b7280;">{d.get("conversas",0)} conversas · {" · ".join(parts)}</span>')

    split_line = (f'<div style="font-size:12.5px;text-align:center;margin-top:8px;line-height:1.9;">'
                  f'{seg_line("PF", pf, "#0891b2")}<br>{seg_line("PJ", pj, "#7c3aed")}</div>')

    def section(title, inner, color="#1e3a8a"):
        if not inner:
            return ""
        return (f'<div style="font-size:12px;font-weight:800;color:{color};text-transform:uppercase;'
                f'letter-spacing:.5px;margin:24px 0 8px;">{title}</div>{inner}')

    def ul(items, color="#374151"):
        if not items:
            return ""
        lis = "".join(f'<li style="margin:5px 0;line-height:1.5;">{esc(x)}</li>' for x in items if x)
        return f'<ul style="margin:0;padding-left:20px;font-size:14px;color:{color};">{lis}</ul>'

    # (Funil do CRM removido do relatório diário — é estado histórico e confunde a visão do dia.)
    funil_html = ""

    # Blocos por segmento (PF / PJ)
    def seg_block(a_seg, cor, cor_soft, titulo):
        leitura = esc((a_seg or {}).get("leitura"))
        ops = _sd_oportunidades((a_seg or {}).get("oportunidades"))
        ligar = _sd_ligar((a_seg or {}).get("ligar_hoje"))
        if not (leitura or ops or ligar):
            return ""
        inner = ""
        if leitura:
            inner += (f'<div style="background:{cor_soft};border-radius:8px;padding:11px 14px;font-size:13.5px;'
                      f'color:#334155;line-height:1.5;">{leitura}</div>')
        if ops:
            inner += f'<div style="font-size:11.5px;font-weight:700;color:#166534;margin:12px 0 2px;">Oportunidades</div>{ops}'
        if ligar:
            inner += f'<div style="font-size:11.5px;font-weight:700;color:#991b1b;margin:12px 0 2px;">Ligar hoje</div>{ligar}'
        return (f'<div style="border:1px solid #e5e7eb;border-radius:12px;padding:14px 16px;margin:10px 0;">'
                f'<div style="font-size:13px;font-weight:800;color:{cor};margin-bottom:6px;">{titulo}</div>{inner}</div>')

    pf_block = seg_block(a.get("pf"), "#0891b2", "#ecfeff", "👤 PF · Pessoa Física (individual / turma aberta)")
    pj_block = seg_block(a.get("pj"), "#7c3aed", "#f5f3ff", "🏢 PJ · Corporativo (turma fechada / in company)")

    # Objeções
    objs = a.get("objecoes") or []
    objs_html = ""
    if objs:
        rows = ""
        for o in objs[:5]:
            if isinstance(o, dict):
                rows += (f'<tr><td style="padding:7px 0;border-bottom:1px solid #f3f4f6;font-size:14px;'
                         f'color:#111827;font-weight:600;width:45%;">🚧 {esc(o.get("objecao"))}</td>'
                         f'<td style="padding:7px 0 7px 12px;border-bottom:1px solid #f3f4f6;font-size:13px;'
                         f'color:#374151;">→ {esc(o.get("sugestao"))}</td></tr>')
            else:
                rows += f'<tr><td colspan="2" style="font-size:14px;padding:6px 0;">{esc(o)}</td></tr>'
        objs_html = f'<table style="width:100%;border-collapse:collapse;">{rows}</table>'

    # Cursos mais procurados (IA consolida; fallback pro contador dos campos)
    cursos_ia = a.get("cursos_procurados") or []
    cursos_html = ""
    if cursos_ia:
        chips = ""
        for c in cursos_ia[:10]:
            if isinstance(c, dict):
                nome = esc(c.get("curso"))
                qtd = c.get("qtd")
                obs = esc(c.get("obs"))
                if not nome:
                    continue
                chips += (f'<span style="display:inline-block;background:#eff6ff;color:#1e40af;padding:5px 12px;'
                          f'border-radius:20px;font-size:13px;font-weight:600;margin:3px 5px 3px 0;" '
                          f'title="{obs}">📘 {nome}{f" · <b>{qtd}</b>" if qtd else ""}</span>')
            elif c:
                chips += (f'<span style="display:inline-block;background:#eff6ff;color:#1e40af;padding:5px 12px;'
                          f'border-radius:20px;font-size:13px;font-weight:600;margin:3px 5px 3px 0;">📘 {esc(c)}</span>')
        cursos_html = f'<div>{chips}</div>'
    else:
        cursos_field = m.get("cursos") or {}
        if cursos_field:
            chips = "".join(
                f'<span style="display:inline-block;background:#eff6ff;color:#1e40af;padding:5px 12px;'
                f'border-radius:20px;font-size:13px;font-weight:600;margin:3px 5px 3px 0;">📘 {esc(k)} · <b>{v}</b></span>'
                for k, v in list(cursos_field.items())[:10]
            )
            cursos_html = f'<div>{chips}</div>'

    # Risco de perda (análise + perdidos do CRM)
    risco = list(a.get("risco_perda") or [])
    perdidos = m.get("perdidos") or []
    if perdidos:
        risco = risco + [f"❌ {p} (CRM)" for p in perdidos[:5]]

    destaque = esc(a.get("destaque"))
    termometro = esc(a.get("termometro"))
    atend_line = " · ".join(f"{k}: {v}" for k, v in atend.items() if v and k != "—")

    op_html = _build_operacional_html(digest.get("operacional"))

    return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><style>
    body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:#f3f4f6; margin:0; padding:20px; }}
    .container {{ max-width:660px; margin:0 auto; background:#fff; border-radius:14px; overflow:hidden; box-shadow:0 2px 10px rgba(0,0,0,.08); }}
    .header {{ background:linear-gradient(135deg,#16295f,#1e40af); color:#fff; padding:26px 30px; }}
    .header h1 {{ margin:0; font-size:21px; }}
    .header p {{ margin:6px 0 0; font-size:13px; color:#bfdbfe; }}
    .body {{ padding:24px 30px; }}
    .destaque {{ background:#eff6ff; border-radius:10px; padding:16px 18px; font-size:15px; color:#1e3a8a; font-weight:600; line-height:1.5; }}
    .footer {{ background:#f9fafb; padding:16px 30px; font-size:12px; color:#9ca3af; text-align:center; border-top:1px solid #f3f4f6; }}
    </style></head><body><div class="container">
      <div class="header"><h1>🧭 Copiloto do Gestor — Treinamentos</h1>
        <p>Resumo estratégico do dia {dia_fmt} · PF + PJ</p></div>
      <div class="body">
        {f'<div class="destaque">💡 {destaque}</div>' if destaque else ''}
        {f'<div style="font-size:13px;color:#6b7280;margin:10px 2px 4px;">🌡️ {termometro}</div>' if termometro else ''}
        <div style="margin:16px 0 4px;">{cards}</div>
        {split_line}
        {f'<div style="font-size:12px;color:#9ca3af;text-align:center;margin-top:8px;">Atendimento — {esc(atend_line)}</div>' if atend_line else ''}
        {section("📚 Cursos mais procurados", cursos_html)}
        {section("🎯 Por segmento", pf_block + pj_block, "#1e3a8a")}
        {section("⚠️ Risco de perda (dinheiro na mesa)", ul(risco), "#b45309")}
        {section("🚧 Objeções recorrentes", objs_html, "#6b21a8")}
        {section("✅ Recomendações", ul(a.get("recomendacoes")))}
      </div>
      {op_html}
      <div class="footer">Bot SDR PJ · Copiloto do Gestor · 1ª visão estratégica (vendas) · 2ª visão operacional (atendimento)</div>
    </div></body></html>"""


def _build_sales_digest_plain(digest: Dict) -> str:
    m = digest.get("metrics", {}) or {}
    a = digest.get("analysis", {}) or {}
    pf = m.get("pf", {}) or {}
    pj = m.get("pj", {}) or {}
    lines = [f"Copiloto do Gestor — Treinamentos — dia {digest.get('dia','')}", ""]
    lines.append(f"Total: {m.get('total_conversas',0)} conversas")
    lines.append(f"PF: {pf.get('conversas',0)} conversas · {pf.get('novos',0)} novos · {pf.get('quentes',0)} quentes")
    lines.append(f"PJ: {pj.get('conversas',0)} conversas · {pj.get('novos',0)} novos · "
                 f"{pj.get('quentes',0)} quentes · {pj.get('turma_fechada',0)} turma fechada")
    if a.get("destaque"):
        lines += ["", f"Destaque: {a['destaque']}"]
    if a.get("recomendacoes"):
        lines += ["", "Recomendações:"] + [f"- {r}" for r in a["recomendacoes"]]
    lines += ["", "Veja o email em HTML para o relatório completo (PF/PJ + funil)."]
    return "\n".join(lines)


async def send_sales_digest(digest: Dict, config: Dict) -> bool:
    """Envia o resumo estratégico diário para o grupo de gestão (digest_recipients).

    Respeita email_notifications_enabled. Cai para email_recipients se digest_recipients vazio.
    """
    import asyncio

    enabled = str(config.get("email_notifications_enabled", "false")).lower()
    if enabled not in ("true", "1", "yes", "sim"):
        logger.debug("[SalesDigest] Notificações por email desabilitadas.")
        return False

    sender   = (config.get("gmail_sender") or "").strip()
    password = (config.get("gmail_app_password") or "").strip()
    recipients = _parse_recipients(config.get("digest_recipients") or config.get("email_recipients") or "")
    if not sender or not password or not recipients:
        logger.info("[SalesDigest] Não enviado: sender/senha/destinatários incompletos.")
        return False

    dia = digest.get("dia", "")
    total = (digest.get("metrics") or {}).get("total_conversas", 0)
    _WD = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
    try:
        _d = datetime.strptime(dia, "%Y-%m-%d")
        dia_label = f"{_WD[_d.weekday()]} {_d.strftime('%d/%m')}"
    except Exception:
        dia_label = dia

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🧭 Resumo de {dia_label} — {total} conversas (Treinamentos)"
    msg["From"]    = f"Copiloto do Gestor <{sender}>"
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(_build_sales_digest_plain(digest), "plain", "utf-8"))
    msg.attach(MIMEText(_build_sales_digest_html(digest),  "html",  "utf-8"))

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _smtp_send, sender, password, recipients, msg)
        logger.info(f"[SalesDigest] Enviado para {recipients} (dia {dia}, {total} conversas)")
        return True
    except Exception as e:
        logger.error(f"[SalesDigest] Falha ao enviar: {e}")
        return False


async def send_turma_fechada_alert(lead: Dict, config: Dict) -> bool:
    """Alerta de TURMA FECHADA (corporativo/in company) para um grupo separado.

    Destinatários: config['turma_fechada_recipients']. Respeita
    email_notifications_enabled. Reusa o layout do email de lead.
    """
    import asyncio

    enabled = str(config.get("email_notifications_enabled", "false")).lower()
    if enabled not in ("true", "1", "yes", "sim"):
        return False

    sender   = (config.get("gmail_sender") or "").strip()
    password = (config.get("gmail_app_password") or "").strip()
    recipients = _parse_recipients(config.get("turma_fechada_recipients") or "")
    if not sender or not password or not recipients:
        logger.info("[TurmaFechada] Alerta não enviado: sender/senha/destinatários incompletos.")
        return False

    empresa = (lead.get("company") or lead.get("empresa") or "").strip()
    contato = (lead.get("contact_name") or lead.get("nome") or lead.get("phone_number") or "Contato").strip()
    origem  = (lead.get("origem") or "").strip()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎯 ALERTA — Turma Fechada: {empresa or contato}" + (f" ({origem})" if origem else "")
    msg["From"]    = f"Alerta Turma Fechada <{sender}>"
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(_build_plain(lead), "plain", "utf-8"))
    msg.attach(MIMEText(_build_turma_fechada_html(lead), "html", "utf-8"))

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _smtp_send, sender, password, recipients, msg)
        logger.info(f"[TurmaFechada] 🎯 Alerta enviado para {recipients} (empresa: {empresa or '—'})")
        return True
    except Exception as e:
        logger.error(f"[TurmaFechada] Falha ao enviar alerta: {e}")
        return False
