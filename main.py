"""Ponto de entrada da aplicação Bot SDR PJ."""

import logging
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

from app.core.config import settings
from app.core.database import init_db, get_bot_config_full
from app.api.webhook_tallos import router as webhook_tallos_router
from app.api.admin import router as admin_router
from app.api.test_chat import router as test_router
from app.api.radar import router as radar_router
from app.services.report_service import build_daily_report, send_report_whatsapp

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def _daily_report_job():
    """Job executado diariamente no horário configurado (padrão 18h BRT)."""
    try:
        config = await get_bot_config_full()
        cfg = {c["key"]: c["value"] for c in config}

        chatpro_token  = cfg.get("chatpro_token", "").strip()
        chatpro_url    = cfg.get("chatpro_url", "").strip()
        recipients_raw = cfg.get("report_recipients", "")
        recipients     = [n.strip() for n in recipients_raw.replace("\n", ",").split(",") if n.strip()]

        if not chatpro_token or not recipients:
            logger.info("[REPORT] Job ignorado: token ou destinatários não configurados.")
            return

        template_name  = cfg.get("report_template_name", "radarpj").strip()
        language_code  = cfg.get("report_language_code", "pt_BR").strip()
        report = await build_daily_report()
        results = await send_report_whatsapp(
            report, recipients, chatpro_url, chatpro_token,
            template_name=template_name, language_code=language_code
        )
        ok = sum(1 for r in results.values() if r.get("ok"))
        logger.info(f"[REPORT] Relatório diário enviado para {ok}/{len(results)} número(s).")
    except Exception as e:
        logger.error(f"[REPORT] Erro no job diário: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicialização e finalização da aplicação."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    logger.info("Inicializando Bot SDR PJ...")
    await init_db()
    logger.info("Banco de dados inicializado.")

    # ── Scheduler do relatório diário ────────────────────────────────────
    scheduler = AsyncIOScheduler(timezone="America/Sao_Paulo")

    async def _scheduled_report():
        """Lê horário do banco em tempo de execução (permite alterar sem reiniciar)."""
        try:
            config = await get_bot_config_full()
            cfg = {c["key"]: c["value"] for c in config}
            hour = int(cfg.get("report_hour", "18"))
            # Só executa se estiver na hora certa (APScheduler já filtra, mas dupla garantia)
            from datetime import datetime
            import pytz
            brt_now = datetime.now(pytz.timezone("America/Sao_Paulo"))
            if brt_now.hour == hour:
                await _daily_report_job()
        except Exception as e:
            logger.error(f"[REPORT] Erro no scheduler: {e}")

    # Verifica a cada hora cheia se é o horário de envio
    scheduler.add_job(_scheduled_report, CronTrigger(minute=0), id="daily_report")
    scheduler.start()
    logger.info("Scheduler do relatório diário iniciado (verifica a cada hora cheia).")

    logger.info("Bot SDR PJ pronto!")
    logger.info(f"Tela de teste: http://{settings.app_host}:{settings.app_port}/test")
    logger.info(f"Painel admin:  http://{settings.app_host}:{settings.app_port}/admin")
    logger.info(f"Radar:         http://{settings.app_host}:{settings.app_port}/radar")
    yield
    scheduler.shutdown(wait=False)
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
