"""
Token Tracker — rastreia e persiste o consumo de tokens da API Anthropic.

Usado por todos os serviços que chamam Claude:
  ai_engine, farol_engine, company_intel, product_classifier, lead_enricher

Uso:
    from app.services.token_tracker import track
    await track("ai_engine", "generate_response", response.usage, model_id, phone)
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Preço por milhão de tokens (USD) — atualizado Mai/2025
# https://www.anthropic.com/pricing
# ─────────────────────────────────────────────────────────────────────────────
_PRICING: dict[str, tuple[float, float]] = {
    # (input_per_M_USD, output_per_M_USD)
    "claude-haiku-4-5":   (0.80,  4.00),
    "claude-haiku-3-5":   (0.80,  4.00),
    "claude-haiku-3":     (0.25,  1.25),
    "claude-sonnet-4-6":  (3.00, 15.00),
    "claude-sonnet-4":    (3.00, 15.00),
    "claude-sonnet-3-7":  (3.00, 15.00),
    "claude-sonnet-3-5":  (3.00, 15.00),
    "claude-opus-4-6":    (15.00, 75.00),
    "claude-opus-4":      (15.00, 75.00),
    "claude-opus-3":      (15.00, 75.00),
}

# Taxa de câmbio aproximada USD → BRL (atualizar conforme necessário)
USD_TO_BRL: float = 5.70


def get_cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Calcula o custo em USD para uma chamada à API."""
    inp_price, out_price = 3.00, 15.00  # default: Sonnet
    for key, prices in _PRICING.items():
        if key in model_id:
            inp_price, out_price = prices
            break
    return (input_tokens * inp_price + output_tokens * out_price) / 1_000_000


async def track(
    service: str,
    function: str,
    usage,           # response.usage object from Anthropic SDK
    model_id: str,
    phone_number: str = "",
) -> None:
    """
    Registra o consumo de tokens de uma chamada à API Claude no banco de dados.
    Chamada fire-and-forget — nunca lança exceção para o chamador.
    """
    try:
        input_tokens  = getattr(usage, "input_tokens",  0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        total_tokens  = input_tokens + output_tokens
        cost_usd      = get_cost_usd(model_id, input_tokens, output_tokens)

        # Import lazy para evitar circular imports
        from app.core.database import log_token_usage
        await log_token_usage(
            service=service,
            function=function,
            model=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            phone_number=phone_number,
        )
    except Exception as e:
        logger.warning(f"[TokenTracker] Falha ao registrar uso ({service}.{function}): {e}")
