"""Ponto de entrada da aplicação Bot SDR PJ."""

import logging
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
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

    # Relatório diário via WhatsApp (Radar) — DESATIVADO em favor do Copiloto do Gestor
    # (resumo estratégico por email, 07h30). Reativar descomentando a linha abaixo.
    # scheduler.add_job(_scheduled_report, CronTrigger(minute=0), id="daily_report")

    # Ingestor automático de conversas do RD Conversas (cliente + consultor)
    from app.services.ingestion import run_full_sync, acquire_singleton_lock
    from app.services.rag_curation import curate_recent
    from app.services.turma_fechada import scan_recent as tf_scan
    from app.services.sales_digest import run_daily_digest
    from app.api.radar import scheduled_crm_refresh
    scheduler.add_job(run_full_sync, CronTrigger(hour="1,7,13,19", minute=30), id="ingest_tallos")
    scheduler.add_job(curate_recent, CronTrigger(hour=3, minute=0), id="curate_knowledge")
    scheduler.add_job(tf_scan, CronTrigger(hour="2,8,14,20", minute=0), id="turma_fechada_scan")
    # Refresh do funil do CRM (etapa dos leads ativos) — mantém Radar e Copiloto frescos
    scheduler.add_job(scheduled_crm_refresh, CronTrigger(hour="6,13,19", minute=50), id="crm_refresh")
    # Copiloto do Gestor — resumo estratégico do dia anterior, 07h30 BRT (após o refresh das 06h50)
    scheduler.add_job(run_daily_digest, CronTrigger(hour=7, minute=30), id="sales_digest")

    # Trava de instância única: com vários workers, só um roda o scheduler
    # (evita relatório diário e ingestor rodarem em dobro).
    if acquire_singleton_lock("botpj_scheduler"):
        scheduler.start()
        logger.info("Scheduler iniciado (relatório diário + ingestor 01h30/07h30/13h30/19h30 BRT).")
    else:
        logger.info("Scheduler já ativo em outro worker — este worker não agenda.")

    logger.info("Bot SDR PJ pronto!")
    logger.info(f"Tela de teste: http://{settings.app_host}:{settings.app_port}/test")
    logger.info(f"Painel admin:  http://{settings.app_host}:{settings.app_port}/admin")
    logger.info(f"Radar:         http://{settings.app_host}:{settings.app_port}/radar")
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)
    logger.info("Encerrando Bot SDR PJ...")


class PrefixRedirectMiddleware(BaseHTTPMiddleware):
    """Mantém o prefixo configurado nos redirects internos quando servido atrás de um
    proxy que expõe o app sob esse prefixo (ex.: nginx antigo em /pj).
    Só é registrado quando settings.app_url_prefix não está vazio."""
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        prefix = settings.app_url_prefix
        if prefix and response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location", "")
            if location.startswith("/") and not location.startswith(prefix + "/"):
                response.headers["location"] = prefix + location
        return response


app = FastAPI(
    title="Bot SDR PJ",
    description="Chatbot IA para atendimento de leads PJ — Departamento de Treinamentos",
    version="1.0.0",
    lifespan=lifespan,
)

# Só necessário atrás de um proxy que sirva o app sob um prefixo (setup nginx antigo).
if settings.app_url_prefix:
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
async def root(request: Request):
    # No subdomínio radar.technowhub.ai a raiz leva direto ao login do Radar.
    host = request.headers.get("host", "").split(":")[0].lower()
    if host.startswith("radar."):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/radar/login", status_code=307)
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
