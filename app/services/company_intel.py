"""
Inteligência de empresa — pesquisa na web via Claude com web_search tool.

Dado o nome de uma empresa, busca informações relevantes para o contexto
comercial de treinamentos corporativos (setor, porte, localização, etc.)
e retorna um parágrafo descritivo pronto para exibição no Radar.

Cache em memória: TTL de 24h por nome de empresa.
"""

import logging
import time
from typing import Optional, Tuple, Dict

from anthropic import AsyncAnthropic

from app.core.config import settings

logger = logging.getLogger(__name__)

client = AsyncAnthropic(api_key=settings.anthropic_api_key)

# ── Cache em memória ────────────────────────────────────────────────────────────
# { nome_normalizado: (resultado, timestamp) }
_CACHE: Dict[str, Tuple[str, float]] = {}
_CACHE_TTL = 86_400  # 24 horas


def _normalize_name(name: str) -> str:
    return name.strip().lower()


def _cache_get(name: str) -> Optional[str]:
    key = _normalize_name(name)
    entry = _CACHE.get(key)
    if entry and (time.time() - entry[1]) < _CACHE_TTL:
        return entry[0]
    if entry:
        del _CACHE[key]
    return None


def _cache_set(name: str, value: str):
    _CACHE[_normalize_name(name)] = (value, time.time())


# ── Pesquisa principal ──────────────────────────────────────────────────────────

async def get_company_intel(company_name: str) -> str:
    """
    Retorna um parágrafo descritivo sobre a empresa para exibição no Radar.
    Usa Claude com web_search para buscar informações atualizadas.
    Resultado em cache por 24h.
    """
    if not company_name or company_name in ("—", ""):
        return ""

    cached = _cache_get(company_name)
    if cached is not None:
        logger.info(f"[CompanyIntel] Cache HIT: {company_name}")
        return cached

    logger.info(f"[CompanyIntel] Pesquisando: {company_name}")

    prompt = (
        f"Pesquise informações sobre a empresa \"{company_name}\" e escreva um parágrafo "
        "descritivo de 2 a 4 linhas em português, focando nos seguintes aspectos:\n"
        "- Segmento / setor de atuação\n"
        "- Localização (cidade/estado sede)\n"
        "- Porte aproximado (número de funcionários ou faturamento se disponível)\n"
        "- Diferenciais ou destaques relevantes\n\n"
        "Escreva apenas o parágrafo, sem títulos, listas ou explicações extras. "
        "Se não encontrar informações confiáveis, escreva apenas: "
        "\"Não foram encontradas informações públicas suficientes sobre esta empresa.\""
    )

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )

        # Extrai o texto da resposta (pode ter tool_use blocks intermediários)
        text_parts = [
            block.text for block in response.content
            if hasattr(block, "text") and block.text
        ]
        result = " ".join(text_parts).strip()

        if not result:
            result = "Não foram encontradas informações públicas suficientes sobre esta empresa."

        _cache_set(company_name, result)
        logger.info(f"[CompanyIntel] ✅ {company_name} — {len(result)} chars")
        return result

    except Exception as e:
        logger.warning(f"[CompanyIntel] Falha na pesquisa de '{company_name}': {e}")
        # Fallback: tenta sem web_search (apenas conhecimento do modelo)
        try:
            fallback = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            result = fallback.content[0].text.strip()
            _cache_set(company_name, result)
            return result
        except Exception as e2:
            logger.error(f"[CompanyIntel] Fallback também falhou: {e2}")
            return ""
