"""
Inteligência de empresa — pesquisa na web via Claude com web_search tool.

Dado o nome de uma empresa, busca informações relevantes para o contexto
comercial de treinamentos corporativos e retorna dados estruturados:
  - descricao: parágrafo descritivo
  - funcionarios: estimativa de nº de funcionários (string)
  - setor: segmento / setor de atuação
  - cidade: cidade e estado sede
  - porte: micro / pequena / média / grande / enterprise

Cache em memória: TTL de 24h por nome de empresa.
"""

import json
import logging
import re
import time
from typing import Dict, Optional, Tuple

from anthropic import AsyncAnthropic

from app.core.config import settings

logger = logging.getLogger(__name__)

client = AsyncAnthropic(api_key=settings.anthropic_api_key)

# ── Cache em memória ────────────────────────────────────────────────────────────
_CACHE: Dict[str, Tuple[dict, float]] = {}
_CACHE_TTL = 86_400  # 24 horas


def _normalize_name(name: str) -> str:
    return name.strip().lower()


def _cache_get(name: str) -> Optional[dict]:
    key = _normalize_name(name)
    entry = _CACHE.get(key)
    if entry and (time.time() - entry[1]) < _CACHE_TTL:
        return entry[0]
    if entry:
        del _CACHE[key]
    return None


def _cache_set(name: str, value: dict):
    _CACHE[_normalize_name(name)] = (value, time.time())


_EMPTY = {"descricao": "", "funcionarios": "", "setor": "", "cidade": "", "porte": ""}

_SCHEMA = """{
  "descricao":    "parágrafo de 2-3 linhas descrevendo a empresa",
  "funcionarios": "estimativa do número de funcionários (ex: ~500, 1.000-5.000, mais de 10.000)",
  "setor":        "segmento/setor principal de atuação",
  "cidade":       "cidade e estado sede (ex: São Paulo, SP)",
  "porte":        "micro | pequena | média | grande | enterprise"
}"""


def _build_prompt(company_name: str) -> str:
    return (
        f"Pesquise informações sobre a empresa brasileira \"{company_name}\".\n\n"
        "IMPORTANTE: tente encontrar o número de funcionários usando fontes como LinkedIn, "
        "Glassdoor, Gupy, Indeed, RAIS, Exame, Valor Econômico, ou o próprio site da empresa.\n\n"
        "Retorne SOMENTE um JSON válido com os campos abaixo:\n"
        f"{_SCHEMA}\n\n"
        "Regras:\n"
        "- Para 'funcionarios': se não encontrar o número exato, use uma faixa estimada "
        "(ex: '100-500', 'mais de 1.000', '~200'). Se realmente não souber, use 'Não identificado'.\n"
        "- Para 'descricao': texto corrido em português, sem bullets nem markdown.\n"
        "- Se a empresa não for encontrada, preencha descricao com "
        "'Não foram encontradas informações públicas suficientes sobre esta empresa.' "
        "e deixe os demais campos vazios.\n"
        "Retorne APENAS o JSON, sem explicações."
    )


def _parse_json(text: str) -> dict:
    """Extrai JSON da resposta (pode ter texto ao redor)."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group())
        return {
            "descricao":    str(data.get("descricao", "") or ""),
            "funcionarios": str(data.get("funcionarios", "") or ""),
            "setor":        str(data.get("setor", "") or ""),
            "cidade":       str(data.get("cidade", "") or ""),
            "porte":        str(data.get("porte", "") or ""),
        }
    except (json.JSONDecodeError, TypeError):
        return {}


# ── Pesquisa principal ──────────────────────────────────────────────────────────

async def get_company_intel(company_name: str) -> dict:
    """
    Retorna dict estruturado com informações da empresa.
    Usa Claude Sonnet + web_search para dados atualizados.
    Resultado em cache por 24h.
    """
    if not company_name or company_name in ("—", ""):
        return _EMPTY.copy()

    cached = _cache_get(company_name)
    if cached is not None:
        logger.info(f"[CompanyIntel] Cache HIT: {company_name}")
        return cached

    logger.info(f"[CompanyIntel] Pesquisando: {company_name}")
    prompt = _build_prompt(company_name)

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )

        # Extrai texto final (ignora tool_use blocks)
        text_parts = [
            block.text for block in response.content
            if hasattr(block, "text") and block.text
        ]
        raw = " ".join(text_parts).strip()
        result = _parse_json(raw)

        if not result:
            result = _EMPTY.copy()
            result["descricao"] = raw or "Não foram encontradas informações públicas suficientes."

        _cache_set(company_name, result)
        logger.info(
            f"[CompanyIntel] ✅ {company_name} — "
            f"func={result.get('funcionarios')} porte={result.get('porte')}"
        )
        return result

    except Exception as e:
        logger.warning(f"[CompanyIntel] Falha com web_search para '{company_name}': {e}")

        # Fallback: Haiku sem web search (conhecimento do modelo)
        try:
            fallback = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = fallback.content[0].text.strip()
            result = _parse_json(raw)
            if not result:
                result = _EMPTY.copy()
                result["descricao"] = raw or ""
            _cache_set(company_name, result)
            return result
        except Exception as e2:
            logger.error(f"[CompanyIntel] Fallback também falhou: {e2}")
            return _EMPTY.copy()
