"""
Enriquecimento de leads via LLM (Claude Haiku).

Analisa o transcript de conversa vindo das atividades do RD CRM e extrai:
  - Campos estruturados: nome, empresa, email, interesse, qtd participantes, trilha, temperatura
  - Resumo executivo da conversa
  - Insights e observações para o consultor

Usado principalmente para leads importados via tallos_crm_sync que não passaram
pelo fluxo normal do bot (ex: vieram direto para atendente humano).

Cache em memória por phone + hash do texto (TTL 24h).
"""

import json
import logging
import hashlib
import time
from typing import Dict, Optional

import asyncio
from anthropic import AsyncAnthropic
from app.core.config import settings
from app.services.token_tracker import track as _track_tokens

logger = logging.getLogger(__name__)

_client: Optional[AsyncAnthropic] = None
_cache: Dict[str, tuple] = {}   # phone → (hash, result, ts)
_TTL = 86400                     # 24 horas


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


_PROMPT = """Você é um analista de CRM especializado em treinamentos corporativos.

Analise o transcript de conversa abaixo e retorne um JSON com as informações extraídas.

TRANSCRIPT:
{transcript}

Retorne APENAS um JSON válido (sem markdown, sem explicações) com esta estrutura:
{{
  "nome": "nome do lead (string ou null)",
  "empresa": "nome da empresa (string ou null)",
  "email": "email@exemplo.com (string ou null)",
  "interesse": "o que o lead quer contratar/aprender (string curta, ou null)",
  "qtd_participantes": "número ou faixa de participantes mencionada (string ou null)",
  "trail": "A, B, C, D ou null — A=turma aberta/individual, B=in company/corporativo, C=consultoria, D=locação de espaço",
  "temperatura": "quente, morno ou frio",
  "resumo": "resumo executivo da conversa em 1-2 frases objetivas",
  "insights": "observações relevantes para o consultor: urgência, objeções, perfil da empresa, decisor, etc. (string ou null)"
}}

Regras:
- Use null quando a informação não estiver disponível no transcript
- "temperatura" quente = lead qualificado, interesse claro, engajado; morno = interesse mas sem urgência; frio = apenas curiosidade
- "trail" B se mencionou empresa, equipe ou in company; A se é para ele mesmo ou 1-3 pessoas; C se quer diagnóstico/consultoria; D se mencionou locação/espaço/sala
- "insights" deve ser prático para o consultor — evite repetir o que já está em outros campos"""


async def enrich_lead_from_activity(
    phone: str,
    activity_text: str,
) -> Dict:
    """
    Enriquece um lead analisando o transcript de conversa via Claude Haiku.

    Retorna dict com campos extraídos + resumo + insights.
    Usa cache de 24h por phone + hash do texto.
    """
    if not settings.anthropic_api_key or not activity_text:
        return {}

    text_hash = hashlib.md5(activity_text.encode()).hexdigest()[:12]
    cached = _cache.get(phone)
    if cached and cached[0] == text_hash and (time.time() - cached[2]) < _TTL:
        return cached[1]

    try:
        resp = await _get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": _PROMPT.format(transcript=activity_text[:4000]),
            }],
        )
        asyncio.ensure_future(_track_tokens("lead_enricher", "enrich_lead", resp.usage, "claude-haiku-4-5-20251001", phone))
        raw = resp.content[0].text.strip()

        # Remove markdown se vier com ```json
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        result = json.loads(raw)
        _cache[phone] = (text_hash, result, time.time())
        logger.info(f"[LeadEnricher] {phone} → empresa={result.get('empresa')} trail={result.get('trail')} temp={result.get('temperatura')}")
        return result

    except json.JSONDecodeError as e:
        logger.warning(f"[LeadEnricher] JSON inválido para {phone}: {e} | raw={raw[:200]}")
        return {}
    except Exception as e:
        logger.error(f"[LeadEnricher] Erro para {phone}: {e}")
        return {}


def map_enriched_to_lead_fields(enriched: Dict) -> Dict:
    """
    Converte o resultado do LLM para os campos do banco de leads.
    Retorna apenas campos não-nulos para uso em upsert_lead.
    """
    mapping = {
        "nome":              "contact_name",
        "empresa":           "company",
        "email":             "email",
        "interesse":         "tema_interesse",
        "qtd_participantes": "qtd_participantes",
        "trail":             "trail",
        "temperatura":       "lead_temperature",
        "resumo":            "proximo_passo",   # usa proximo_passo como resumo temporário
    }
    result = {}
    for src, dst in mapping.items():
        val = enriched.get(src)
        if val and val != "null":
            result[dst] = str(val)

    # Temperatura → formato do banco
    temp_map = {"quente": "quente", "morno": "morno", "frio": "frio"}
    if "lead_temperature" in result:
        result["lead_temperature"] = temp_map.get(result["lead_temperature"].lower(), "frio")

    # Insights ficam em notes (prefixado para não sobrescrever tallos_contact_id)
    insights = enriched.get("insights")
    if insights and insights != "null":
        result["_insights"] = insights   # campo especial — tratado separadamente

    return result
