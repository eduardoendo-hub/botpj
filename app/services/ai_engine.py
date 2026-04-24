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
            f"{response.usage.input_tokens}in/{response.usage.output_tokens}out"
        )

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
        return response.content[0].text.strip().upper() == "SIM"
    except Exception as e:
        logger.error(f"[{phone_number}] Erro classify_context: {e}")
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
