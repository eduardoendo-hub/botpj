"""
Serviço de notificação por email — Gmail SMTP — Bot SDR PJ.

Enviado automaticamente quando um novo lead PJ é registrado
(Nome, WhatsApp/Telefone, Cargo, Data/Hora, Produto, Origem).
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _build_html(lead: Dict) -> str:
    nome       = lead.get("contact_name") or lead.get("nome") or "—"
    whatsapp   = lead.get("phone_number") or lead.get("whatsapp") or "—"
    email      = lead.get("email") or "—"
    empresa    = lead.get("company") or lead.get("empresa") or "—"
    cargo      = lead.get("job_title") or lead.get("cargo") or "—"
    produto    = lead.get("produto") or lead.get("training_interest") or lead.get("treinamento") or "—"
    origem     = lead.get("origem") or lead.get("source_channel") or "—"
    ocorrencia = lead.get("ocorrencia") or datetime.now().strftime("%d/%m/%Y %H:%M")

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
    .footer {{ background: #f9fafb; padding: 16px 28px; font-size: 12px;
               color: #9ca3af; text-align: center; border-top: 1px solid #f3f4f6; }}
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
      </table>
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
    ocorrencia = lead.get("ocorrencia") or datetime.now().strftime("%d/%m/%Y %H:%M")

    return (
        f"Novo Lead PJ — Bot SDR PJ\n\n"
        f"Nome:              {nome}\n"
        f"WhatsApp/Telefone: {whatsapp}\n"
        f"Email:             {email}\n"
        f"Empresa:           {empresa}\n"
        f"Cargo:             {cargo}\n"
        f"Data / Hora:       {ocorrencia}\n"
        f"Produto:           {produto}\n"
        f"Origem:            {origem}\n\n"
        "Enviado automaticamente pelo Bot SDR PJ"
    )


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
