"""
Motor de IA — Claude (Anthropic) para geração de respostas — Bot SDR PJ.

Estratégia de economia de tokens (camadas):
  L1 · Roteamento por complexidade: saudações → resposta fixa sem IA;
       perguntas simples → Haiku; perguntas complexas → Sonnet.
  L2 · max_tokens dinâmico: 256 / 512 / 800 conforme complexidade estimada.
  L3 · Compressão de histórico: últimas 5 msgs verbatim + resumo das anteriores.
  L4 · Cache de respostas FAQ: TTL 1h, evita recalcular perguntas repetidas.
  L5 · Knowledge relevante (7 000 chars max) — ver database.get_relevant_knowledge_text.
  L6 · Follow-up com base reduzida (2 500 chars) e Haiku.
  L7 · Prompt de sistema enxuto (remoção de instruções redundantes).
"""

import logging
import hashlib
import time
from typing import List, Dict, Tuple, Optional

from anthropic import AsyncAnthropic

from app.core.config import settings
from app.core.database import (
    get_relevant_knowledge_text,
    get_conversation_history,
    get_system_prompt,
)

logger = logging.getLogger(__name__)

client = AsyncAnthropic(api_key=settings.anthropic_api_key)


# ─────────────────────────────────────────────────────────────
# L4 · Cache simples em memória
# ─────────────────────────────────────────────────────────────

_CACHE: Dict[str, Tuple[str, float]] = {}
_CACHE_TTL = 3_600  # 1 hora

_SYSPROMPT_CACHE: Optional[Tuple[str, float]] = None
_SYSPROMPT_TTL = 300  # 5 minutos


async def _get_system_prompt_cached() -> str:
    global _SYSPROMPT_CACHE
    if _SYSPROMPT_CACHE and (time.time() - _SYSPROMPT_CACHE[1]) < _SYSPROMPT_TTL:
        return _SYSPROMPT_CACHE[0]
    prompt = await get_system_prompt() or ""
    _SYSPROMPT_CACHE = (prompt, time.time())
    return prompt


def clear_cache():
    global _CACHE
    _CACHE = {}
    logger.info("Cache de respostas limpo.")


def _cache_key(phone: str, message: str, knowledge_hash: str) -> str:
    raw = f"{message.lower().strip()}::{knowledge_hash}"
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_get(key: str) -> Optional[str]:
    entry = _CACHE.get(key)
    if entry and (time.time() - entry[1]) < _CACHE_TTL:
        return entry[0]
    if entry:
        del _CACHE[key]
    return None


def _cache_set(key: str, value: str):
    _CACHE[key] = (value, time.time())


# ─────────────────────────────────────────────────────────────
# L1 · Detecção de complexidade
# ─────────────────────────────────────────────────────────────

_GREETINGS = {
    "oi", "olá", "ola", "hello", "hi", "hey",
    "boa tarde", "bom dia", "boa noite",
    "tudo bem", "tudo bom", "oi tudo bem",
    "bom dia!", "boa tarde!", "boa noite!",
    "oii", "oiii", "oi!", "olá!", "ola!", "hey!",
}

_SHORT_FOLLOWUPS = {
    "ok", "sim", "não", "nao", "certo", "entendi", "entendido",
    "claro", "pode ser", "quero", "gostei", "tá", "ta", "blz",
    "beleza", "obrigado", "obrigada", "valeu", "vlw", "perfeito",
    "ótimo", "otimo", "excelente", "legal", "show", "maravilha",
    "combinado", "fechado", "vamos", "bora", "continua", "continue",
    "mais", "e aí", "e ai", "próximo", "proximo",
}

# Perguntas de preço/valor → Sonnet obrigatório
_SONNET_PATTERNS = [
    "qual o valor", "qual a mensalidade", "quanto custa", "quanto é",
    "tem desconto", "qual o preço", "qual o investimento",
    "formas de pagamento", "parcelamento", "parcelas",
    "desconto", "promoção", "campanha",
    "valor do treinamento", "valor do curso", "custa quanto",
    "orçamento", "orcamento", "tabela de preços",
]

# Perguntas simples → Haiku
_SIMPLE_PATTERNS = [
    "qual a carga horária", "quantas horas", "qual a duração",
    "tem certificado", "como funciona", "qual o formato",
    "tem turma aberta", "tem turma fechada", "turma in company",
    "é presencial", "é online", "é gravado", "é ao vivo",
    "tem plataforma", "qual a plataforma", "onde fica",
    "quais cursos", "que cursos", "quais treinamentos",
    "tem sala", "tem auditório", "locação", "aluguel",
    "quais documentos", "como contratar", "como funciona",
]


def _classify_complexity(message: str) -> str:
    msg = message.lower().strip().rstrip("!?.,:;")

    if msg in _SHORT_FOLLOWUPS:
        return "simple"
    if msg in _GREETINGS:
        return "greeting"

    for pattern in _SONNET_PATTERNS:
        if pattern in msg:
            return "complex"

    if len(msg) <= 20:
        return "simple"

    for pattern in _SIMPLE_PATTERNS:
        if pattern in msg:
            return "simple"

    if len(msg) > 120 or any(w in msg for w in [
        "explique", "explica", "detalhe", "diferença", "comparar",
        "qual melhor", "customizar", "personalizar",
    ]):
        return "complex"

    return "simple"


def _max_tokens(complexity: str) -> int:
    return {"greeting": 150, "simple": 300, "complex": 700}.get(complexity, 400)


def _model(complexity: str) -> str:
    if complexity == "complex":
        return "claude-sonnet-4-20250514"
    return "claude-haiku-4-5-20251001"


# ─────────────────────────────────────────────────────────────
# L3 · Compressão de histórico
# ─────────────────────────────────────────────────────────────

_VERBATIM_MSGS = 5
_MAX_SUMMARY_CHARS = 600


async def _build_compressed_history(
    phone_number: str,
    prefetched: Optional[List[Dict]] = None,
) -> Tuple[List[Dict], str]:
    history = prefetched if prefetched is not None else await get_conversation_history(phone_number, limit=20)

    recent = history[-_VERBATIM_MSGS:]
    older  = history[:-_VERBATIM_MSGS] if len(history) > _VERBATIM_MSGS else []

    summary = ""
    if older:
        lines = [
            f"{'Lead' if m['role'] == 'user' else 'Bot'}: {m['message'][:120]}"
            for m in older
        ]
        raw = "\n".join(lines)
        if len(raw) > _MAX_SUMMARY_CHARS:
            raw = raw[:_MAX_SUMMARY_CHARS] + "…"
        summary = f"[Resumo de {len(older)} mensagens anteriores]\n{raw}"

    return recent, summary


# ─────────────────────────────────────────────────────────────
# Respostas de saudação fixas (zero tokens)
# ─────────────────────────────────────────────────────────────

_GREETING_RESPONSES = [
    "Olá! 👋 Tudo bem? Sou o assistente virtual do departamento de Treinamentos Corporativos. Como posso te ajudar hoje?",
    "Oi! 😊 Bem-vindo(a)! Posso te ajudar com informações sobre nossos treinamentos para empresas — presenciais, online ao vivo, gravados, turmas fechadas e muito mais. O que você gostaria de saber?",
    "Olá! 🎯 Que ótimo ter você aqui! Sou o assistente de Treinamentos PJ. Quer saber mais sobre nossas soluções corporativas de treinamento? É só perguntar!",
]

_greeting_idx = 0


def _get_greeting(contact_name: str = "") -> str:
    global _greeting_idx
    base = _GREETING_RESPONSES[_greeting_idx % len(_GREETING_RESPONSES)]
    _greeting_idx += 1
    if contact_name:
        first = contact_name.split()[0]
        base = base.replace("Olá!", f"Olá, {first}!").replace("Oi!", f"Oi, {first}!")
    return base


# ─────────────────────────────────────────────────────────────
# Resposta principal
# ─────────────────────────────────────────────────────────────

async def generate_response(
    phone_number: str,
    user_message: str,
    contact_name: str = "",
    is_returning_lead: bool = False,
    prefetched_history: Optional[List[Dict]] = None,
) -> Tuple[str, bool]:
    """Gera resposta para o lead PJ. Retorna (texto, needs_escalation)."""
    try:
        complexity = _classify_complexity(user_message)

        if complexity == "greeting" and not is_returning_lead:
            resp = _get_greeting(contact_name)
            logger.info(f"[{phone_number}] Saudação detectada → resposta fixa (0 tokens)")
            return resp, False

        knowledge = await get_relevant_knowledge_text(user_message, max_chars=7000)

        knowledge_hash = hashlib.md5(knowledge.encode()).hexdigest()[:8]
        cache_key = _cache_key(phone_number, user_message, knowledge_hash)

        msg_normalized = user_message.lower().strip().rstrip("!?.,:;")
        is_short_followup = msg_normalized in _SHORT_FOLLOWUPS

        if complexity == "simple" and not is_short_followup:
            cached = _cache_get(cache_key)
            if cached:
                logger.info(f"[{phone_number}] Cache HIT ({complexity}) → 0 tokens")
                return cached, False

        recent_history, history_summary = await _build_compressed_history(
            phone_number, prefetched=prefetched_history
        )

        system_prompt = await _get_system_prompt_cached()
        full_system = _build_system_prompt(
            system_prompt, knowledge, contact_name,
            is_returning_lead, history_summary
        )
        messages = _build_messages(recent_history, user_message)

        model_id   = _model(complexity)
        max_tokens = _max_tokens(complexity)

        response = await client.messages.create(
            model=model_id,
            max_tokens=max_tokens,
            system=full_system,
            messages=messages,
        )

        text = _clean_whatsapp(response.content[0].text)
        logger.info(
            f"[{phone_number}] [{complexity.upper()}] [{model_id.split('-')[1]}] "
            f"{response.usage.input_tokens}in/{response.usage.output_tokens}out tokens"
        )

        needs_escalation = _detect_escalation_needed(text)
        if needs_escalation:
            logger.info(f"[{phone_number}] 🚨 ESCALAÇÃO DETECTADA. Trecho: {text[:120]!r}")

        if complexity == "simple" and not needs_escalation:
            _cache_set(cache_key, text)

        return text, needs_escalation

    except Exception as e:
        logger.error(f"[{phone_number}] Erro ao gerar resposta: {e}")
        return (
            "Desculpe, estou com uma dificuldade técnica no momento. "
            "Um de nossos consultores entrará em contato com você em breve! 😊",
            False,
        )


def _clean_whatsapp(text: str) -> str:
    """Remove/converte formatação Markdown para WhatsApp."""
    import re
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    text = re.sub(r'__(.+?)__', r'_\1_', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[\-\*]\s+', '• ', text, flags=re.MULTILINE)
    return text.strip()


def _detect_escalation_needed(text: str) -> bool:
    """
    Detecta se a resposta indica necessidade de escalação para consultor.
    Dois grupos: bot não sabe (A) ou bot concluiu coleta de dados (B).
    """
    text_lower = text.lower()

    signals_a = [
        "não tenho essa informação",
        "não possuo essa informação",
        "não consigo responder",
        "não sei informar",
        "precisaria verificar",
        "melhor falar com um consultor",
        "vou encaminhar para",
        "um especialista irá",
        "nossa equipe irá",
    ]

    signals_b = [
        "anotei todos os seus dados",
        "já tenho todos os seus dados",
        "registrei todos os seus dados",
        "tenho todas as informações",
        "em breve um consultor entrará em contato",
        "em breve nossa equipe entrará em contato",
        "em breve alguém da equipe entrará em contato",
        "nosso consultor entrará em contato",
    ]

    for signal in signals_a:
        if signal in text_lower:
            logger.debug(f"[ESCALAÇÃO-A] sinal: {signal!r}")
            return True

    b_hits = sum(1 for s in signals_b if s in text_lower)
    if b_hits >= 1:
        data_signals = [
            "whatsapp", "e-mail", "email", "linkedin",
            "nome completo", "empresa", "cargo",
        ]
        if any(d in text_lower for d in data_signals) or b_hits >= 2:
            logger.debug(f"[ESCALAÇÃO-B] {b_hits} sinal(is) + dados coletados")
            return True

    return False


# ─────────────────────────────────────────────────────────────
# Extração de dados do lead para o email de notificação
# ─────────────────────────────────────────────────────────────

async def extract_lead_data(phone_number: str, history: List[Dict]) -> Dict:
    """
    Usa Claude Haiku para extrair os dados coletados na conversa.
    Retorna dict com: nome, whatsapp, email, linkedin, empresa, cargo, treinamento.
    """
    if not history:
        return {}

    conv_text = "\n".join(
        f"[{'Lead' if m['role'] == 'user' else 'Bot'}]: {m['message']}"
        for m in history[-20:]
    )

    prompt = (
        "Extraia os dados pessoais/profissionais do lead PJ da conversa abaixo.\n\n"
        f"{conv_text}\n\n"
        "Retorne SOMENTE JSON com os campos encontrados (use '' para os não encontrados):\n"
        '{"nome": "", "whatsapp": "", "email": "", "linkedin": "", '
        '"empresa": "", "cargo": "", "treinamento": ""}'
    )

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        import json, re
        text = response.content[0].text.strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        logger.error(f"[{phone_number}] Erro ao extrair dados do lead: {e}")

    return {}


async def classify_conversation_context(phone_number: str) -> bool:
    """
    Verifica se o lead está em modo 'vou pensar / buscar aprovação interna'.
    Retorna True se o lead está aguardando e o bot não precisa fazer follow-up.
    """
    try:
        history = await get_conversation_history(phone_number, limit=10)
        if not history:
            return False

        recent = history[-5:]
        history_text = "\n".join(
            f"[{'LEAD' if m['role'] == 'user' else 'BOT'}]: {m['message']}"
            for m in recent
        )

        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{
                "role": "user",
                "content": (
                    f"CONVERSA:\n{history_text}\n\n"
                    "Responda SIM ou NAO.\n"
                    "SIM = lead disse que vai pensar, buscar aprovação, consultar diretoria, verificar orçamento ou vai voltar.\n"
                    "NAO = conversa parou sem essa indicação.\nResponda SOMENTE: SIM ou NAO"
                ),
            }],
        )

        return response.content[0].text.strip().upper() == "SIM"

    except Exception as e:
        logger.error(f"[{phone_number}] Erro ao classificar contexto: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────────

def _build_system_prompt(
    base_prompt: str,
    knowledge: str,
    contact_name: str,
    is_returning_lead: bool,
    history_summary: str = "",
) -> str:
    """L7 · Prompt enxuto para Bot SDR PJ."""
    parts = [base_prompt]

    if contact_name:
        parts.append(f"Contato: {contact_name}. Use o nome ocasionalmente.")

    if is_returning_lead:
        parts.append(
            "LEAD RECORRENTE: não trate como primeiro contato. "
            "Retome naturalmente, sem se reapresentar."
        )

    parts.append(
        "\nREGRAS: português BR | cordial e profissional | máx 3 parágrafos curtos | "
        "sem markdown/asteriscos (WhatsApp) | 1-2 emojis por msg | "
        "SEMPRE use os valores, preços e informações da BASE DE CONHECIMENTO | "
        "quando o lead perguntar preço/orçamento, informe os valores da base diretamente | "
        "só diga 'não tenho essa informação' se o assunto NÃO estiver na base | "

        "FOCO NO PÚBLICO PJ: trate o interlocutor como representante de empresa. "
        "Explore o contexto: quantos colaboradores serão treinados, qual a necessidade, "
        "se preferem turma aberta/fechada, presencial/online. "

        "COLETA DE DADOS — regras críticas: "
        "(1) NUNCA peça dados pessoais durante conversa informativa normal. "
        "Respostas curtas como 'ok', 'entendi', 'sim' são continuação da conversa — responda naturalmente. "
        "(2) Inicie o fluxo de coleta SOMENTE quando o lead pedir EXPLICITAMENTE para falar com consultor, "
        "fazer orçamento ou fechar negócio. "
        "(3) Se o lead pedir contato, colete UM DADO DE CADA VEZ nesta ordem: "
        "Nome completo → WhatsApp → Email → Empresa → Cargo. "
        "(4) Se o lead mudar de assunto durante a coleta, responda SOMENTE a nova pergunta. "
        "NÃO mencione coleta de dados nessa resposta. Retome numa mensagem posterior SEPARADA. "
        "(5) NUNCA diga 'vou acionar consultor' enquanto ainda estiver coletando dados. "
        "Use essas frases SOMENTE após confirmar TODOS os dados. "
        "CRÍTICO — ENCERRAMENTO: após confirmar o 5º dado (Cargo), envie UMA mensagem OBRIGATORIAMENTE "
        "contendo 'Anotei todos os seus dados' E 'em breve um consultor entrará em contato'. "
        "ENCERRE sem fazer mais perguntas."
    )

    if history_summary:
        parts.append(f"\nCONTEXTO ANTERIOR (resumo):\n{history_summary}")

    if knowledge:
        parts.append(f"\nBASE DE CONHECIMENTO:\n{knowledge}")

    return "\n".join(parts)


def _build_messages(history: List[Dict], current_message: str) -> List[Dict]:
    """Constrói lista de mensagens para a API do Claude."""
    messages = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "assistant"
        messages.append({"role": role, "content": msg["message"]})
    messages.append({"role": "user", "content": current_message})
    return messages
