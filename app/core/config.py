"""Configurações centrais da aplicação Bot SDR PJ."""

from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # ── Canal RD Conversas (Tallos) ───────────────────────────────────
    tallos_api_token: str = ""             # API Token do RD Conversas (painel > Configurações > API)
    tallos_api_url: str = "https://api.tallos.com.br/v2"
    tallos_webhook_secret: str = ""        # Secret para validar autenticidade dos webhooks Tallos

    # Anthropic
    anthropic_api_key: str = ""

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8001                   # Porta diferente do BOT MBA (8000)
    app_secret_key: str = "change-me"
    admin_username: str = "admin"
    admin_password: str = "change-me"
    consultant_password: str = "change-me"

    # Horário SDRs
    sdr_start_hour: int = 8
    sdr_end_hour: int = 18
    sdr_work_days: str = "0,1,2,3,4"

    # Bot sempre ativo
    bot_always_active: bool = False

    @property
    def sdr_work_days_list(self) -> List[int]:
        return [int(d.strip()) for d in self.sdr_work_days.split(",")]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
