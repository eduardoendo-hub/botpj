"""
Inteligência de empresa — pesquisa na web via Claude com web_search tool.

Dado o nome de uma empresa, busca informações relevantes para o contexto
comercial de treinamentos corporativos e retorna dados estruturados:
  - descricao: parágrafo descritivo
  - funcionarios: estimativa de nº de funcionários (string)
  - setor: segmento / setor de atuação
  - cidade: cidade e estado sede
  - porte: micro / pequena / média / grande / enterprise

Cache em duas camadas:
  1. Memória (dict) — acesso instantâneo enquanto o processo está rodando
  2. SQLite (company_intel_cache) — persistente entre restarts do servidor
     A pesquisa web só é feita quando NÃO existe entrada no banco.
"""

import json
import logging
import re
import time
from typing import Dict, Optional, Tuple

from anthropic import AsyncAnthropic

import asyncio
from app.core.config import settings
from app.services.token_tracker import track as _track_tokens

logger = logging.getLogger(__name__)

client = AsyncAnthropic(api_key=settings.anthropic_api_key)

# ── Cache em memória (camada 1 — rápida, volátil) ───────────────────────────────
_CACHE: Dict[str, dict] = {}


def _normalize_name(name: str) -> str:
    return name.strip().lower()


def _mem_get(name: str) -> Optional[dict]:
    return _CACHE.get(_normalize_name(name))


def _mem_set(name: str, value: dict) -> None:
    _CACHE[_normalize_name(name)] = value


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

async def get_company_intel_cached_only(company_name: str) -> dict:
    """
    Retorna dados de empresa APENAS do cache (memória ou banco).
    Nunca dispara pesquisa web — usado para não bloquear o bot.
    """
    if not company_name or company_name in ("—", ""):
        return {}
    mem = _mem_get(company_name)
    if mem is not None:
        return mem
    try:
        from app.core.database import get_company_intel_cached
        db_cached = await get_company_intel_cached(company_name)
        if db_cached:
            _mem_set(company_name, db_cached)
            return db_cached
    except Exception:
        pass
    return {}


async def get_company_intel(company_name: str) -> dict:
    """
    Retorna dict estruturado com informações da empresa.

    Ordem de busca:
      1. Cache em memória  → resposta imediata (processo em execução)
      2. Cache no banco     → resposta rápida, persiste entre restarts
      3. Pesquisa web (Claude Sonnet + web_search) → salva em memória + banco
         Só executa quando a empresa ainda não foi pesquisada.
    """
    if not company_name or company_name in ("—", ""):
        return _EMPTY.copy()

    # 1. Memória
    mem = _mem_get(company_name)
    if mem is not None:
        logger.debug(f"[CompanyIntel] Cache MEM HIT: {company_name}")
        return mem

    # 2. Banco de dados (persistente)
    from app.core.database import get_company_intel_cached, set_company_intel_cached
    db_cached = await get_company_intel_cached(company_name)
    if db_cached is not None:
        logger.info(f"[CompanyIntel] Cache DB HIT: {company_name}")
        _mem_set(company_name, db_cached)   # promove para memória
        return db_cached

    # 3. Pesquisa web — empresa ainda não foi pesquisada
    logger.info(f"[CompanyIntel] Pesquisando na web: {company_name}")
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

        asyncio.ensure_future(_track_tokens("company_intel", "get_company_intel_sonnet", response.usage, "claude-sonnet-4-20250514"))
        _mem_set(company_name, result)
        await set_company_intel_cached(company_name, result)
        logger.info(
            f"[CompanyIntel] ✅ {company_name} — "
            f"func={result.get('funcionarios')} porte={result.get('porte')} [salvo no banco]"
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
            asyncio.ensure_future(_track_tokens("company_intel", "get_company_intel_haiku", fallback.usage, "claude-haiku-4-5-20251001"))
            raw = fallback.content[0].text.strip()
            result = _parse_json(raw)
            if not result:
                result = _EMPTY.copy()
                result["descricao"] = raw or ""
            _mem_set(company_name, result)
            await set_company_intel_cached(company_name, result)
            return result
        except Exception as e2:
            logger.error(f"[CompanyIntel] Fallback também falhou: {e2}")
            return _EMPTY.copy()
