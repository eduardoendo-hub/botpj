"""Ponto de entrada da aplicação Bot SDR PJ."""

import logging
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

from app.core.config import settings
from app.core.database import init_db
from app.api.webhook_tallos import router as webhook_tallos_router
from app.api.admin import router as admin_router
from app.api.test_chat import router as test_router
from app.api.radar import router as radar_router

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicialização e finalização da aplicação."""
    logger.info("Inicializando Bot SDR PJ...")
    await init_db()
    logger.info("Banco de dados inicializado.")
    logger.info("Bot SDR PJ pronto!")
    logger.info(f"Tela de teste: http://{settings.app_host}:{settings.app_port}/test")
    logger.info(f"Painel admin:  http://{settings.app_host}:{settings.app_port}/admin")
    logger.info(f"Radar:         http://{settings.app_host}:{settings.app_port}/radar")
    yield
    logger.info("Encerrando Bot SDR PJ...")


class PrefixRedirectMiddleware(BaseHTTPMiddleware):
    """Garante que redirects internos mantenham o prefixo /pj quando servido via nginx."""
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location", "")
            if location.startswith("/") and not location.startswith("/pj/"):
                response.headers["location"] = "/pj" + location
        return response


app = FastAPI(
    title="Bot SDR PJ",
    description="Chatbot IA para atendimento de leads PJ — Departamento de Treinamentos",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(PrefixRedirectMiddleware)

# ── Rotas ─────────────────────────────────────────────────────────────
# Canal RD Conversas (Tallos) — webhook de monitoramento e registro PJ
app.include_router(webhook_tallos_router, tags=["Webhook - Tallos PJ"])
# Admin e testes
app.include_router(admin_router,          tags=["Admin"])
app.include_router(test_router,           tags=["Teste"])
# Radar — painel de monitoramento em tempo real
app.include_router(radar_router,          tags=["Radar"])


@app.get("/")
async def root():
    return {"status": "ok", "app": "Bot SDR PJ", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )
