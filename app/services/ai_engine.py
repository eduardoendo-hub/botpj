"""
Motor de IA — Claude (Anthropic) — Bot SDR PJ v2.

Estratégia de tokens:
  L1 · Roteamento por complexidade: saudações → resposta fixa sem IA.
  L2 · max_tokens dinâmico: 256 / 512 / 800 conforme complexidade.
  L3 · Compressão de histórico: últimas 5 msgs verbatim + resumo das anteriores.
  L4 · Cache de respostas FAQ: TTL 1h.
  L5 · Knowledge relevante (7 000 chars max).
  L6 · Follow-up com base reduzida e Haiku.
  L7 · Prompt de sistema enxuto.

Fase 2 — inteligência conversacional:
  · Detecção de trilha (A/B/C/D/E)
  · Score de temperatura do lead (quente/morno/frio)
  · Extração estruturada de todos os campos de qualificação
  · Análise assíncrona após cada resposta (não bloqueia o fluxo principal)
"""

import logging
import hashlib
import time
import json
import re
from typing import List, Dict, Tuple, Optional

from anthropic import AsyncAnthropic

from app.core.config import settings
from app.core.database import (
    get_relevant_knowledge_text,
    get_conversation_history,
    get_system_prompt,
    get_lead_by_phone,
)
from app.services.token_tracker import track as _track_tokens

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


def invalidate_system_prompt_cache() -> None:
    """Força reload do system prompt no próximo request (chamar após edição via admin)."""
    global _SYSPROMPT_CACHE
    _SYSPROMPT_CACHE = None


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
    "oii", "oiii", "oi!", "olá!", "ola!",
}

_SHORT_FOLLOWUPS = {
    "ok", "sim", "não", "nao", "certo", "entendi", "entendido",
    "claro", "pode ser", "quero", "gostei", "tá", "ta", "blz",
    "beleza", "obrigado", "obrigada", "valeu", "vlw", "perfeito",
    "ótimo", "otimo", "excelente", "legal", "show", "maravilha",
    "combinado", "fechado", "vamos", "bora", "continua", "continue",
    "mais", "e aí", "e ai", "próximo", "proximo",
}

_SONNET_PATTERNS = [
    "qual o valor", "qual a mensalidade", "quanto custa", "quanto é",
    "tem desconto", "qual o preço", "qual o investimento",
    "formas de pagamento", "parcelamento", "parcelas",
    "desconto", "promoção", "campanha",
    "valor do treinamento", "valor do curso", "custa quanto",
    "orçamento", "orcamento", "tabela de preços", "proposta",
    "quero fechar", "quero contratar", "como faço para contratar",
]

_SIMPLE_PATTERNS = [
    "qual a carga horária", "quantas horas", "qual a duração",
    "tem certificado", "como funciona", "qual o formato",
    "tem turma aberta", "tem turma fechada", "turma in company",
    "é presencial", "é online", "é gravado", "é ao vivo",
    "tem plataforma", "qual a plataforma", "onde fica",
    "quais cursos", "que cursos", "quais treinamentos",
    "tem sala", "tem auditório", "locação", "aluguel",
    "quais documentos", "como contratar",
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
        "qual melhor", "customizar", "personalizar", "in company",
        "turma fechada", "exclusivo", "quantas pessoas",
        # B2B / corporativo
        "equipe", "funcionários", "colaboradores", "empresa", "corporativo",
        "treinamento para", "curso para", "programa de", "proposta",
        "in-company", "fechado", "exclusiva", "exclusivo",
        # Locação
        "locação", "alugar", "aluguel", "sala para", "auditório", "laboratório",
        "estúdio", "espaço para", "evento",
        # Consultoria
        "não sei", "não tenho certeza", "qual seria", "o que recomendam",
        "me ajudem a entender", "diagnóstico",
    ]):
        return "complex"

    return "simple"


def _max_tokens(complexity: str) -> int:
    return {"greeting": 150, "simple": 350, "complex": 750}.get(complexity, 400)


def _model(complexity: str) -> str:
    if complexity == "complex":
        return "claude-sonnet-4-20250514"
    return "claude-haiku-4-5-20251001"


# ─────────────────────────────────────────────────────────────
# L3 · Compressão de histórico
# ─────────────────────────────────────────────────────────────

_VERBATIM_MSGS = 6
_MAX_SUMMARY_CHARS = 700


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
            f"{'Lead' if m['role'] == 'user' else 'Bot'}: {m['message'][:130]}"
            for m in older
        ]
        raw = "\n".join(lines)
        if len(raw) > _MAX_SUMMARY_CHARS:
            raw = raw[:_MAX_SUMMARY_CHARS] + "…"
        summary = f"[Resumo de {len(older)} msgs anteriores]\n{raw}"

    return recent, summary


# ─────────────────────────────────────────────────────────────
# Respostas de saudação fixas (zero tokens)
# ─────────────────────────────────────────────────────────────

_GREETING_RESPONSES = [
    "Seja bem-vindo(a) ao Universo IMPACTA! 🎓 Sou o assistente de Treinamentos Corporativos PJ. Para começar, qual é o seu nome?",
    "Seja bem-vindo(a) ao Universo IMPACTA! 😊 Aqui você encontra soluções em treinamentos corporativos, turmas fechadas, online ao vivo e muito mais. Qual é o seu nome?",
    "Seja bem-vindo(a) ao Universo IMPACTA! 🏢 Sou o SDR de Treinamentos PJ e estou aqui para te ajudar. Me conta o seu nome para começarmos!",
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

# ─────────────────────────────────────────────────────────────
# Validação de e-mail em código (pré-IA)
# ─────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_EMAIL_INVALID_RESPONSE = (
    "Ops! Informe um E-mail válido. Por favor, digite novamente "
    "seu e-mail completo (ex: nome@empresa.com.br)."
)
_NO_EMAIL_PHRASES = [
    "não tenho e-mail", "nao tenho email", "sem e-mail", "não uso e-mail",
    "nao uso email", "não possuo e-mail", "não tenho", "nao tenho",
]
_EMAIL_TRIGGER_WORDS = ["e-mail", "email", "seu e-mail", "seu email"]


def _last_bot_message(history: Optional[List[Dict]]) -> str:
    """Retorna a última mensagem do bot no histórico."""
    if not history:
        return ""
    for msg in reversed(history):
        if msg.get("role") == "assistant":
            return msg.get("message", "").lower()
    return ""


def _bot_asked_for_email(last_bot_msg: str) -> bool:
    return any(w in last_bot_msg for w in _EMAIL_TRIGGER_WORDS)


def _is_no_email_phrase(message: str) -> bool:
    msg = message.lower().strip()
    return any(phrase in msg for phrase in _NO_EMAIL_PHRASES)


def _validate_email_precheck(
    user_message: str,
    history: Optional[List[Dict]],
) -> Optional[str]:
    """
    Se o bot acabou de pedir e-mail e o lead enviou algo inválido,
    retorna a resposta de erro antes de chamar a IA.
    Retorna None se não for caso de validação.
    """
    last = _last_bot_message(history)
    if not _bot_asked_for_email(last):
        return None
    if _is_no_email_phrase(user_message):
        return None  # Lead não tem e-mail — deixar a IA tratar
    candidate = user_message.strip()
    if not _EMAIL_RE.match(candidate):
        return _EMAIL_INVALID_RESPONSE
    return None


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
            logger.info(f"[{phone_number}] Saudação → resposta fixa (0 tokens)")
            return resp, False

        # Pré-check de validação de e-mail (antes de chamar a IA)
        email_error = _validate_email_precheck(user_message, prefetched_history)
        if email_error:
            logger.info(f"[{phone_number}] E-mail inválido detectado → resposta fixa (0 tokens)")
            return email_error, False

        knowledge = await get_relevant_knowledge_text(user_message, max_chars=7000)
        knowledge_hash = hashlib.md5(knowledge.encode()).hexdigest()[:8]
        cache_key = _cache_key(phone_number, user_message, knowledge_hash)

        msg_normalized = user_message.lower().strip().rstrip("!?.,:;")
        is_short_followup = msg_normalized in _SHORT_FOLLOWUPS

        if complexity == "simple" and not is_short_followup:
            cached = _cache_get(cache_key)
            if cached:
                logger.info(f"[{phone_number}] Cache HIT → 0 tokens")
                return cached, False

        recent_history, history_summary = await _build_compressed_history(
            phone_number, prefetched=prefetched_history
        )

        system_prompt = await _get_system_prompt_cached()
        lead_context  = await _build_lead_context(phone_number)
        full_system = _build_system_prompt(
            system_prompt, knowledge, contact_name,
            is_returning_lead, history_summary, lead_context
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
            f"{response.usage.input_tokens}in/{response.usage.output_tokens}out"
        )
        asyncio.ensure_future(_track_tokens("ai_engine", "generate_response", response.usage, model_id, phone_number))

        needs_escalation = _detect_escalation_needed(text)
        if needs_escalation:
            logger.info(f"[{phone_number}] 🚨 ESCALAÇÃO detectada")

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
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    text = re.sub(r'__(.+?)__', r'_\1_', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[\-\*]\s+', '• ', text, flags=re.MULTILINE)
    return text.strip()


def _detect_escalation_needed(text: str) -> bool:
    text_lower = text.lower()

    # Sinais de redirecionamento explícito para humano
    signals_redirect = [
        "melhor falar com um consultor",
        "vou encaminhar para",
        "encaminharei para",
        "encaminhar seus dados",
        "vou passar seus dados",
        "vou passar suas informações",
        "um especialista irá",
        "um especialista vai",
        "nossa equipe irá",
        "nossa equipe vai",
        "nosso time vai",
        "nosso time irá",
        "nosso consultor vai",
        "alguém da nossa equipe",
        "alguém do nosso time",
        "transferindo para",
        "transferir para",
        "transferido para",
    ]

    # Sinais de encerramento com registro — coletou os dados e vai acionar humano
    signals_closing = [
        "anotei todos os seus dados",
        "já tenho todos os seus dados",
        "registrei todos os seus dados",
        "registrei sua solicitação",
        "registrei suas informações",
        "recebi todos os dados",
        "tenho todas as informações",
        "tenho os dados necessários",
        "em breve um consultor entrará em contato",
        "em breve nossa equipe entrará em contato",
        "em breve alguém entrará em contato",
        "nosso consultor entrará em contato",
        "nossa equipe entrará em contato",
        "nosso time entrará em contato",
        "entrar em contato com você",
        "retornar com você em breve",
        "retornaremos em breve",
        "receberá um contato",
        "enviarei uma proposta",
        "enviaremos uma proposta",
        "preparar uma proposta",
        "elaborar uma proposta",
        "nosso consultor vai montar",
        "nossa equipe comercial",
        # variações naturais que o bot usa
        "nosso consultor poderá",
        "nosso consultor pode te ajudar",
        "nosso consultor pode ajudar",
        "nossos consultores podem",
        "nossos consultores vão",
        "nossos consultores estão",
        "um consultor especializado",
        "consultor especializado poderá",
        "equipe de locação",
        "equipe especializada entrará",
        "equipe especializada vai",
    ]

    # Sinais de limitação — bot não tem a info e deve escalar
    signals_limit = [
        "não tenho essa informação",
        "não possuo essa informação",
        "não tenho acesso",          # ampliado: pega "não tenho acesso às disponibilidades"
        "não tenho os valores",
        "não tenho as datas",
        "precisaria verificar",
        "não consigo confirmar",
        "não posso confirmar",
    ]

    if any(s in text_lower for s in signals_redirect):
        return True
    if any(s in text_lower for s in signals_closing):
        return True
    if any(s in text_lower for s in signals_limit):
        return True

    return False


# ─────────────────────────────────────────────────────────────
# Análise estruturada da conversa (Fase 2 — chamada assíncrona)
# ─────────────────────────────────────────────────────────────

_ANALYSIS_SCHEMA = """{
  "trail": "",
  "lead_temperature": "",
  "urgencia": "",
  "tipo_interesse": "",
  "nome": "",
  "empresa": "",
  "job_title": "",
  "email": "",
  "tema_interesse": "",
  "training_interest": "",
  "qtd_participantes": "",
  "formato": "",
  "cidade": "",
  "prazo": "",
  "objetivo_negocio": "",
  "score": "",
  "proximo_passo": "",
  "status_conversa": "",
  "needs_escalation": false
}"""

_ANALYSIS_INSTRUCTIONS = """Analise a conversa e extraia os dados estruturados do lead PJ.

TRILHAS:
- A = curso individual / turma aberta (poucas pessoas)
- B = corporativo / turma fechada / in company (equipe ou empresa)
- C = consultoria (lead não sabe o que quer)
- D = locação de espaço / evento
- E = transferência imediata (urgente, VIP, pediu humano)

TEMPERATURA:
- quente = quer proposta, tem prazo curto, grupo definido, forte intenção de fechar
- morno = interesse real mas exploratório, sem urgência
- frio = dúvida genérica, baixa intenção, sem dados claros

URGÊNCIA: alta / media / baixa

TIPO_INTERESSE: curso_corporativo / turma_fechada / turma_aberta / locacao / outro

SCORE: número de 0 a 10 (10 = lead mais quente possível)

STATUS_CONVERSA: em_atendimento / qualificado / parcialmente_qualificado /
                 encaminhado_consultor / aguardando_lead / transferido_humano /
                 concluido / perdido

PROXIMO_PASSO: ação recomendada em uma linha curta

NEEDS_ESCALATION: true se deve transferir para humano agora

Retorne SOMENTE o JSON preenchido, sem explicações."""


async def analyze_and_update_lead(
    phone_number: str,
    history: List[Dict],
) -> Dict:
    """
    Analisa a conversa completa e retorna estrutura de qualificação do lead.
    Chamada assíncrona após cada resposta — não bloqueia o fluxo principal.
    """
    if not history or len(history) < 2:
        return {}

    conv_text = "\n".join(
        f"[{'Lead' if m['role'] == 'user' else 'Bot'}]: {m['message'][:200]}"
        for m in history[-20:]
    )

    prompt = (
        f"{_ANALYSIS_INSTRUCTIONS}\n\n"
        f"CONVERSA:\n{conv_text}\n\n"
        f"JSON esperado:\n{_ANALYSIS_SCHEMA}"
    )

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        asyncio.ensure_future(_track_tokens("ai_engine", "analyze_lead", response.usage, "claude-haiku-4-5-20251001", phone_number))
        text = response.content[0].text.strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            data = json.loads(match.group())
            logger.info(
                f"[{phone_number}] Análise → trilha={data.get('trail')} "
                f"temp={data.get('lead_temperature')} score={data.get('score')}"
            )
            return data
    except Exception as e:
        logger.warning(f"[{phone_number}] Análise estruturada falhou: {e}")

    return {}


async def extract_lead_data(phone_number: str, history: List[Dict]) -> Dict:
    """
    Extrai os dados de contato do lead da conversa.
    Mantém compatibilidade com o fluxo de email de notificação.
    """
    if not history:
        return {}

    data = await analyze_and_update_lead(phone_number, history)
    if data:
        return {
            "nome":         data.get("nome", ""),
            "whatsapp":     phone_number,
            "email":        data.get("email", ""),
            "empresa":      data.get("empresa", ""),
            "cargo":        data.get("job_title", ""),
            "treinamento":  data.get("training_interest", "") or data.get("tema_interesse", ""),
            # campos extras
            "qtd_participantes": data.get("qtd_participantes", ""),
            "formato":           data.get("formato", ""),
            "trail":             data.get("trail", ""),
            "lead_temperature":  data.get("lead_temperature", ""),
            "urgencia":          data.get("urgencia", ""),
            "objetivo_negocio":  data.get("objetivo_negocio", ""),
            "proximo_passo":     data.get("proximo_passo", ""),
        }

    # Fallback: extração simples via Haiku
    conv_text = "\n".join(
        f"[{'Lead' if m['role'] == 'user' else 'Bot'}]: {m['message']}"
        for m in history[-15:]
    )
    prompt = (
        "Extraia os dados do lead PJ da conversa.\n\n"
        f"{conv_text}\n\n"
        "Retorne SOMENTE JSON:\n"
        '{"nome":"","whatsapp":"","email":"","empresa":"","cargo":"","treinamento":""}'
    )
    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        asyncio.ensure_future(_track_tokens("ai_engine", "extract_lead_data", response.usage, "claude-haiku-4-5-20251001", phone_number))
        text = response.content[0].text.strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        logger.error(f"[{phone_number}] Erro fallback extração: {e}")

    return {}


async def generate_conversation_summary(
    phone_number: str,
    history: List[Dict],
) -> str:
    """
    Gera um resumo textual da conversa para ser incluído no email de notificação.
    Destaca: perfil do lead, necessidade identificada, trilha, dados coletados e próximo passo.
    """
    if not history or len(history) < 2:
        return "Conversa sem histórico suficiente para gerar resumo."

    conv_text = "\n".join(
        f"[{'Lead' if m['role'] == 'user' else 'Bot'}]: {m['message'][:300]}"
        for m in history[-30:]
    )

    prompt = (
        "Você é um assistente comercial. Analise a conversa abaixo entre o Bot SDR PJ e um lead corporativo "
        "e escreva um RESUMO EXECUTIVO em português, em texto corrido (sem bullet points, sem markdown), "
        "com no máximo 5 linhas. O resumo deve destacar:\n"
        "1) Quem é o lead (nome, empresa, cargo se disponíveis)\n"
        "2) O que ele quer (tipo de treinamento, locação ou consultoria)\n"
        "3) Principais dados coletados (quantidade de participantes, formato, prazo, cidade etc.)\n"
        "4) Nível de interesse e urgência\n"
        "5) Próximo passo recomendado\n\n"
        f"CONVERSA:\n{conv_text}\n\n"
        "Escreva apenas o resumo, sem títulos ou cabeçalhos."
    )

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        asyncio.ensure_future(_track_tokens("ai_engine", "generate_summary", response.usage, "claude-haiku-4-5-20251001", phone_number))
        summary = response.content[0].text.strip()
        logger.info(f"[{phone_number}] Resumo da conversa gerado ({len(summary)} chars)")
        return summary
    except Exception as e:
        logger.warning(f"[{phone_number}] Falha ao gerar resumo: {e}")
        return "Não foi possível gerar o resumo automático da conversa."


async def classify_conversation_context(phone_number: str) -> bool:
    """Verifica se o lead está aguardando (vai pensar, buscar aprovação interna)."""
    try:
        history = await get_conversation_history(phone_number, limit=8)
        if not history:
            return False

        recent_text = "\n".join(
            f"[{'LEAD' if m['role'] == 'user' else 'BOT'}]: {m['message']}"
            for m in history[-5:]
        )

        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{
                "role": "user",
                "content": (
                    f"CONVERSA:\n{recent_text}\n\n"
                    "Lead disse que vai pensar, buscar aprovação, consultar alguém ou vai voltar?\n"
                    "Responda SOMENTE: SIM ou NAO"
                ),
            }],
        )
        asyncio.ensure_future(_track_tokens("ai_engine", "classify_context", response.usage, "claude-haiku-4-5-20251001", phone_number))
        return response.content[0].text.strip().upper() == "SIM"
    except Exception as e:
        logger.error(f"[{phone_number}] Erro classify_context: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────────

async def _build_lead_context(phone_number: str) -> str:
    """
    Monta um bloco de contexto com tudo que sabemos sobre o lead:
    dados do banco, enriquecimento de CRM e inteligência de empresa.
    Retorna string vazia se não houver dados relevantes.
    Nunca bloqueia — usa apenas caches já existentes.
    """
    lines: list[str] = []

    # ── 1. Dados do lead no banco ──────────────────────────────────────
    try:
        lead = await get_lead_by_phone(phone_number)
    except Exception:
        lead = None

    if lead:
        campo = lambda k: (lead.get(k) or "").strip()
        dados_coletados: list[str] = []

        if campo("contact_name"):  dados_coletados.append(f"Nome: {campo('contact_name')}")
        if campo("email"):         dados_coletados.append(f"E-mail: {campo('email')}")
        if campo("company"):       dados_coletados.append(f"Empresa: {campo('company')}")
        if campo("job_title"):     dados_coletados.append(f"Cargo: {campo('job_title')}")
        if campo("training_interest") or campo("servico"):
            interesse = campo("training_interest") or campo("servico")
            dados_coletados.append(f"Interesse: {interesse}")
        if campo("qtd_colaboradores") or campo("qtd_participantes"):
            qtd = campo("qtd_colaboradores") or campo("qtd_participantes")
            dados_coletados.append(f"Nº de alunos informado: {qtd}")
        if campo("trail"):
            trilha_map = {"A": "A — turma aberta/individual", "B": "B — corporativo/in company",
                          "C": "C — consultoria", "D": "D — locação de espaço"}
            dados_coletados.append(f"Trilha: {trilha_map.get(campo('trail'), campo('trail'))}")
        if campo("lead_temperature"):
            dados_coletados.append(f"Temperatura: {campo('lead_temperature')}")
        if lead.get("score"):
            dados_coletados.append(f"Score: {lead['score']}")

        if dados_coletados:
            lines.append("━━━ PERFIL DO LEAD (dados pré-carregados — podem estar desatualizados) ━━━")
            lines.extend(dados_coletados)
            lines.append(
                "\n⚠️  REGRA CRÍTICA DE SOBERANIA DA CONVERSA: "
                "Estes dados são um ponto de partida, NÃO uma verdade absoluta. "
                "Se em qualquer momento o lead corrigir, contradizer ou atualizar qualquer informação "
                "(nome, empresa, email, interesse, quantidade de alunos, etc.), "
                "SEMPRE acredite no lead e use o que ele disser na conversa. "
                "A conversa em tempo real tem prioridade máxima sobre qualquer dado pré-carregado. "
                "Se houver dúvida se a informação está correta, pergunte gentilmente para confirmar."
            )

        # Instruções de comportamento baseadas no que já sabemos
        instrucoes: list[str] = []
        campos_coletados = {k for k in ("contact_name", "email", "company") if campo(k)}
        if "contact_name" in campos_coletados:
            instrucoes.append(f"• Já temos o nome — NÃO peça novamente. Use '{campo('contact_name')}' naturalmente.")
        if "email" in campos_coletados:
            instrucoes.append("• Já temos o e-mail — NÃO peça novamente.")
        if "company" in campos_coletados:
            instrucoes.append(f"• Já sabemos a empresa ({campo('company')}) — NÃO peça novamente.")

        trail = campo("trail")
        if trail == "B":
            missing = []
            if not (campo("qtd_colaboradores") or campo("qtd_participantes")):
                missing.append("número de alunos")
            if not campo("training_interest") and not campo("servico"):
                missing.append("curso/treinamento de interesse")
            if missing:
                instrucoes.append(f"• Trilha B confirmada — colete: {', '.join(missing)} e depois encaminhe para consultor.")
            else:
                instrucoes.append("• Trilha B com dados completos — encaminhe para consultor com a frase exata.")
        elif trail == "A":
            instrucoes.append("• Trilha A — NÃO transfira para consultor. Informe site e grade aberta.")
        elif trail == "D":
            instrucoes.append("• Trilha D (locação) — colete dados do evento e encaminhe para consultor.")

        temp = campo("lead_temperature")
        if temp == "quente":
            instrucoes.append("• Lead QUENTE — priorize agilidade e ofereça próximo passo concreto imediatamente.")
        elif temp == "frio":
            instrucoes.append("• Lead frio — seja mais consultivo, entenda a necessidade antes de propor solução.")

        if instrucoes:
            lines.append("\nCOMPORTAMENTO ESPERADO COM ESTE LEAD:")
            lines.extend(instrucoes)
            lines.append(
                "• SEMPRE que o lead corrigir qualquer dado (nome, empresa, email, interesse, "
                "quantidade de alunos, etc.) — recue imediatamente, agradeça a correção e use "
                "a informação nova. Nunca insista em dados pré-carregados."
            )

    # ── 2. Inteligência de empresa (só cache — não bloqueia) ───────────
    company_name = (lead.get("company") or "").strip() if lead else ""
    if company_name:
        try:
            from app.services.company_intel import get_company_intel_cached_only
            intel = await get_company_intel_cached_only(company_name)
            if intel and intel.get("descricao"):
                lines.append(f"\nCONTEXTO DA EMPRESA ({company_name}):")
                if intel.get("porte"):    lines.append(f"  Porte: {intel['porte']}")
                if intel.get("setor"):    lines.append(f"  Setor: {intel['setor']}")
                if intel.get("funcionarios"): lines.append(f"  Funcionários: {intel['funcionarios']}")
                desc = (intel.get("descricao") or "")[:300]
                if desc: lines.append(f"  {desc}")
        except Exception:
            pass

    # ── 3. Enriquecimento CRM (só cache — não bloqueia) ────────────────
    try:
        from app.services.lead_enricher import get_cached_enrichment
        crm = get_cached_enrichment(phone_number)
        if crm:
            lines.append("\nHISTÓRICO / ENRIQUECIMENTO CRM:")
            if crm.get("resumo"):   lines.append(f"  Resumo: {crm['resumo']}")
            if crm.get("insights"): lines.append(f"  Insights: {crm['insights']}")
            if crm.get("temperatura") and not (lead and lead.get("lead_temperature")):
                lines.append(f"  Temperatura (CRM): {crm['temperatura']}")
    except Exception:
        pass

    return "\n".join(lines)


def _build_system_prompt(
    base_prompt: str,
    knowledge: str,
    contact_name: str,
    is_returning_lead: bool,
    history_summary: str = "",
    lead_context: str = "",
) -> str:
    parts = [base_prompt]

    if contact_name:
        parts.append(f"\nContato: {contact_name}. Use o nome ocasionalmente.")

    if is_returning_lead:
        parts.append(
            "\nLEAD RECORRENTE: não trate como primeiro contato. "
            "Retome naturalmente sem se reapresentar."
        )

    parts.append(
        "\nREGRAS DE FORMATO: português BR | cordial e consultivo | "
        "máx 3 parágrafos curtos | sem markdown/asteriscos | 1-2 emojis | "
        "NUNCA invente preços, datas ou disponibilidade — use apenas a base de conhecimento | "
        "se o lead mencionar espaço/evento/sala → trilha de locação | "
        "se for para equipe/empresa/turma fechada → qualificação B2B completa | "
        "faça uma pergunta por vez | nunca termine sem indicar próximo passo."
        "\n\nREGRAS DE ESCALAÇÃO — siga rigorosamente:"
        "\n• TRILHA A (turma aberta, 1-3 pessoas): NUNCA transfira para consultor. Responda com as informações da base de conhecimento, informe como se inscrever e indique o site. Mesmo sem saber preço ou data exata, diga que as informações estão disponíveis no site e convide para se inscrever — não mencione consultor."
        "\n• TRILHA B (equipe/empresa/in company): colete nome, empresa e quantidade de pessoas. Após isso, use EXATAMENTE: 'em breve um consultor entrará em contato para montar a proposta'."
        "\n• TRILHA C (consultoria/dúvida): após entender o problema, use EXATAMENTE: 'vou encaminhar para um consultor especializado que poderá fazer um diagnóstico'."
        "\n• TRILHA D (locação): após confirmar que temos o espaço desejado, use EXATAMENTE: 'nosso consultor poderá confirmar disponibilidade e valores — em breve entrará em contato'."
        "\n• Se não souber a resposta: use 'não tenho acesso a essa informação, mas nosso consultor poderá te ajudar'."
    )

    # Contexto do lead — injetado antes do histórico para máxima influência
    if lead_context:
        parts.append(f"\n{lead_context}")

    if history_summary:
        parts.append(f"\nCONTEXTO ANTERIOR:\n{history_summary}")

    if knowledge:
        parts.append(f"\nBASE DE CONHECIMENTO:\n{knowledge}")

    return "\n".join(parts)


def _build_messages(history: List[Dict], current_message: str) -> List[Dict]:
    messages = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "assistant"
        messages.append({"role": role, "content": msg["message"]})
    messages.append({"role": "user", "content": current_message})
    return messages
