"""
Classificador de produto via IA (Claude Haiku).

Analisa todos os dados disponíveis do lead (campos do banco, formulário original,
últimas mensagens da conversa) e retorna um label curto do produto que o lead
está tentando comprar.

Exemplos de saída:
  "Power BI - Turma Fechada"
  "Excel Avançado - Calendário Regular"
  "Locação - Sala de Treinamento"
  "In Company Customizado - Liderança"
  "SAP - Turma Aberta"

Cache em memória: TTL de 2 horas por phone, com invalidação inteligente:
  - Se o resultado cacheado era "A definir" e chegaram mais mensagens → re-executa
  - Se o número de mensagens cresceu significativamente (>3 novas) → re-executa
  - Resultado "A definir" com 0 mensagens não é cacheado (sempre re-tenta)
"""

import logging
import time
import json
from typing import Dict, Optional, Tuple

from anthropic import AsyncAnthropic

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Cache: phone → (produto, timestamp, msg_count) ────────────────────────────
_cache: Dict[str, Tuple[str, float, int]] = {}
_CACHE_TTL = 7_200  # 2 horas


def _cache_get(phone: str, current_msg_count: int = 0) -> Optional[str]:
    entry = _cache.get(phone)
    if not entry:
        return None
    produto, ts, cached_msg_count = entry
    # Cache expirado
    if (time.time() - ts) >= _CACHE_TTL:
        return None
    # Se cacheou "A definir" e agora há mais mensagens → re-executa
    if produto == "A definir" and current_msg_count > cached_msg_count:
        logger.info(f"[ProductClassifier] Cache invalidado para {phone}: "
                    f"'A definir' + {current_msg_count} msgs > {cached_msg_count} antes")
        return None
    # Se chegaram 3+ novas mensagens → re-executa para capturar novas informações
    if current_msg_count >= cached_msg_count + 3:
        logger.info(f"[ProductClassifier] Cache invalidado para {phone}: "
                    f"{current_msg_count} msgs vs {cached_msg_count} no cache")
        return None
    return produto


def _cache_set(phone: str, produto: str, msg_count: int = 0):
    # Não cacheia "A definir" quando não há nenhuma mensagem — sempre re-tenta
    if produto == "A definir" and msg_count == 0:
        return
    _cache[phone] = (produto, time.time(), msg_count)


# ── Prompt ─────────────────────────────────────────────────────────────────────
_SYSTEM = """Você é um especialista comercial sênior da Impacta Tecnologia, escola de TI e gestão.

PORTFÓLIO:
- Calendário Regular / Matrícula Avulsa: aluno se matricula em turma já agendada. Indicado para 1 a 4 pessoas.
- Turma Fechada (In Company): curso exclusivo para a empresa, mínimo ~6-8 pessoas, conteúdo fixo do catálogo.
- Customizado / Consultoria: conteúdo desenvolvido sob medida para a empresa.
- Locação de Espaço: aluguel de sala de treinamento, laboratório de informática, auditório.

REGRAS DE INFERÊNCIA (aplique sempre):
1. Quantidade de pessoas:
   - "2p", "2 pessoas", "2 participantes", "para 2" → Calendário Regular (poucos para fechar turma)
   - "3p", "4p" → provavelmente Calendário Regular, a menos que haja indicação de in-company
   - "5p" a "10p" → pode ser Turma Fechada, verifique outros sinais
   - "10p+" ou "15p", "20p" etc → Turma Fechada / In Company
2. Palavras-chave que indicam Turma Fechada: "in company", "turma fechada", "exclusivo", "personalizado", "nossa equipe", "para o time"
3. Palavras-chave que indicam Calendário Regular: "matrícula", "calendário", "turma aberta", "próxima turma", "agenda"
4. Palavras-chave que indicam Locação: "sala", "espaço", "laboratório", "lab", "auditório", "aluguel"
5. Palavras-chave que indicam Customizado: "sob medida", "customizado", "consultoria", "adaptar conteúdo"

TAREFA: analise todos os dados do lead (nome da negociação, quantidade de pessoas, tipo, tema, conversa) e retorne UM ÚNICO label curto (máx 60 chars) no formato:
"[Tecnologia/Tema] - [Modalidade]"

Exemplos corretos:
- Nome "Power BI 2p" → "Power BI - Calendário Regular (2 pessoas)"
- Nome "Excel 15p In Company" → "Excel - Turma Fechada (15 pessoas)"
- Nome "Locação Lab Informática" → "Locação - Laboratório de Informática"
- Nome "Python Avançado" + tema "Python" + tipo "In Company" → "Python Avançado - Turma Fechada"
- Sem dados suficientes → "A definir"

Responda APENAS com o label, sem aspas, sem ponto final, sem explicação."""


async def classify_product(lead: dict, last_messages: list[dict] | None = None) -> str:
    """
    Classifica o produto do lead usando Claude Haiku.

    Args:
        lead: dicionário com todos os campos do lead (do banco + CRM)
        last_messages: últimas mensagens da conversa (opcional)

    Returns:
        String curta com o produto identificado.
    """
    phone = lead.get("phone_number") or lead.get("telefone") or lead.get("id") or ""
    msg_count = len(last_messages) if last_messages else 0

    # Verifica cache (invalidação inteligente por contagem de mensagens)
    cached = _cache_get(phone, msg_count)
    if cached:
        return cached

    if not settings.anthropic_api_key:
        return "A definir"

    # ── Claude Haiku interpreta todos os dados, incluindo nome da negociação ───
    # Monta contexto compacto para o modelo
    context_parts = []

    # Campos estruturados do lead — CRM primeiro (mais confiável)
    deal_name     = lead.get("_crm_deal_name") or lead.get("deal_name") or ""
    deal_products = lead.get("_crm_deal_products") or lead.get("deal_products") or []

    product_names = []
    if isinstance(deal_products, list):
        for dp in deal_products:
            if isinstance(dp, dict):
                n = dp.get("name") or dp.get("product_name") or dp.get("title") or ""
                if n:
                    product_names.append(n)

    fields = {
        # CRM — dados mais confiáveis vêm primeiro
        "Nome da negociação (CRM)": deal_name,
        "Produtos vinculados (CRM)": ", ".join(product_names) if product_names else "",
        "Etapa CRM":               lead.get("_crm_etapa") or lead.get("funil") or "",
        # Dados capturados pelo bot
        "Tema/Interesse":          lead.get("tema_interesse") or lead.get("training_interest") or "",
        "Tipo de serviço":         lead.get("servico") or lead.get("tipo_interesse") or "",
        "Formato":                 lead.get("formato") or "",
        "Qtd participantes":       str(lead.get("qtd_participantes") or ""),
        "Qtd colaboradores":       str(lead.get("qtd_colaboradores") or ""),
        "Objetivo":                lead.get("objetivo_negocio") or "",
        "Urgência":                lead.get("urgencia") or "",
        "Prazo":                   lead.get("prazo") or "",
        "Próximo passo":           lead.get("proximo_passo") or "",
        "Empresa":                 lead.get("company") or "",
        "Cargo":                   lead.get("job_title") or "",
    }
    for k, v in fields.items():
        if v and v not in ("—", "Não informado"):
            context_parts.append(f"{k}: {v}")

    # Raw form data (formulário original)
    raw = lead.get("raw_form_data") or ""
    if raw:
        try:
            form = json.loads(raw)
            form_txt = "; ".join(f"{k}={v}" for k, v in form.items() if v)
            if form_txt:
                context_parts.append(f"Formulário original: {form_txt[:400]}")
        except Exception:
            context_parts.append(f"Formulário: {raw[:300]}")

    # Últimas mensagens da conversa (máx 12 — inclui Tallos + bot)
    if last_messages:
        msgs = last_messages[-12:]
        chat_lines = []
        for m in msgs:
            role = m.get("role", "")
            txt  = (m.get("message") or m.get("content") or "")[:200]
            label = "Lead" if role == "user" else "Bot" if role == "assistant" else "Consultor"
            if txt:
                chat_lines.append(f"{label}: {txt}")
        if chat_lines:
            context_parts.append("Trecho da conversa:\n" + "\n".join(chat_lines))

    if not context_parts:
        return "A definir"

    user_msg = "\n".join(context_parts)

    try:
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=60,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        produto = (resp.content[0].text or "").strip().strip('"').strip("'")
        if not produto:
            produto = "A definir"
    except Exception as e:
        logger.warning(f"[ProductClassifier] Erro para phone={phone}: {e}")
        produto = "A definir"

    _cache_set(phone, produto, msg_count)
    logger.info(f"[ProductClassifier] {phone} → {produto} (msgs={msg_count})")
    return produto


def invalidate_cache(phone: str):
    """Remove entrada do cache para forçar re-classificação."""
    _cache.pop(phone, None)
