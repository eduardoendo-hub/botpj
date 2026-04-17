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

Cache em memória: TTL de 2 horas por phone. O classificador NÃO é re-executado
a cada refresh do Radar — só quando o lead muda ou o cache expira.
"""

import logging
import time
import json
from typing import Dict, Optional, Tuple

from anthropic import AsyncAnthropic

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Cache: phone → (produto, timestamp) ───────────────────────────────────────
_cache: Dict[str, Tuple[str, float]] = {}
_CACHE_TTL = 7_200  # 2 horas


def _cache_get(phone: str) -> Optional[str]:
    entry = _cache.get(phone)
    if entry and (time.time() - entry[1]) < _CACHE_TTL:
        return entry[0]
    return None


def _cache_set(phone: str, produto: str):
    _cache[phone] = (produto, time.time())


# ── Prompt ─────────────────────────────────────────────────────────────────────
_SYSTEM = """Você é um especialista comercial da Impacta Tecnologia, escola de TI e gestão.

Portfólio de produtos que vendemos:
- Cursos avulsos / turmas abertas: cursos com datas fixas no calendário regular (Python, Power BI, Excel, SAP, Java, AWS, Project, etc.)
- Turmas fechadas (In Company): cursos exclusivos para a empresa, presencial ou online
- Customizado / Consultoria: conteúdo desenvolvido sob medida
- Locação de espaço: aluguel de salas de treinamento, laboratórios de informática

Sua tarefa: com base nos dados do lead, retorne UM ÚNICO label curto (máx 60 chars) identificando o produto.
Formato ideal: "[Tema/Tecnologia] - [Modalidade]"
Exemplos: "Power BI - Turma Fechada", "Excel - Calendário Regular", "Locação - Sala de Treinamento", "SAP - In Company Customizado", "Liderança - Turma Fechada", "AWS - Turma Aberta"

Se não houver informação suficiente, retorne "A definir".
Responda APENAS com o label, sem aspas, sem explicação."""


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

    # Verifica cache
    cached = _cache_get(phone)
    if cached:
        return cached

    # ── Fonte primária: nome e produtos do deal no CRM (mais confiável) ────────
    deal_name = lead.get("_crm_deal_name") or lead.get("deal_name") or ""
    deal_products = lead.get("_crm_deal_products") or lead.get("deal_products") or []

    # Tenta extrair produto dos deal_products (lista de produtos vinculados)
    if deal_products and isinstance(deal_products, list):
        product_names = []
        for dp in deal_products:
            if isinstance(dp, dict):
                name = dp.get("name") or dp.get("product_name") or dp.get("title") or ""
                if name:
                    product_names.append(name)
        if product_names:
            produto = " + ".join(product_names[:2])
            _cache_set(phone, produto)
            logger.info(f"[ProductClassifier] {phone} → {produto} (deal_products)")
            return produto

    # Se tem nome da negociação no CRM, usa como base (ainda pode complementar com IA)
    if deal_name and len(deal_name) > 4:
        _cache_set(phone, deal_name)
        logger.info(f"[ProductClassifier] {phone} → {deal_name} (deal_name)")
        return deal_name

    if not settings.anthropic_api_key:
        return "A definir"

    # ── Fallback: Claude Haiku analisa os demais dados ─────────────────────────
    # Monta contexto compacto para o modelo
    context_parts = []

    # Campos estruturados do lead
    fields = {
        "Tema/Interesse":      lead.get("tema_interesse") or lead.get("training_interest") or "",
        "Tipo de serviço":     lead.get("servico") or lead.get("tipo_interesse") or "",
        "Formato":             lead.get("formato") or "",
        "Trail":               lead.get("trail") or "",
        "Objetivo":            lead.get("objetivo_negocio") or "",
        "Prazo":               lead.get("prazo") or "",
        "Qtd participantes":   str(lead.get("qtd_participantes") or ""),
        "Qtd colaboradores":   str(lead.get("qtd_colaboradores") or ""),
        "Empresa":             lead.get("company") or "",
        "Cargo":               lead.get("job_title") or "",
        "Urgência":            lead.get("urgencia") or "",
        "Próximo passo":       lead.get("proximo_passo") or "",
        "Etapa CRM":           lead.get("_crm_etapa") or lead.get("funil") or "",
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

    # Últimas mensagens da conversa (máx 5)
    if last_messages:
        msgs = last_messages[-5:]
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

    _cache_set(phone, produto)
    logger.info(f"[ProductClassifier] {phone} → {produto}")
    return produto


def invalidate_cache(phone: str):
    """Remove entrada do cache para forçar re-classificação."""
    _cache.pop(phone, None)
