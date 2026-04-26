#!/usr/bin/env python3
"""
Migração v8 — Semeia a lista inicial de identificadores com bypass de horário.

Leads que chegam via estes identificadores (LPs/formulários) são atendidos
pelo bot a qualquer hora, mesmo durante o horário comercial.

Uso:
    cd /opt/bot-sdr-pj
    venv/bin/python3 migrations/seed_bypass_identificadores_v8.py
    systemctl restart bot-sdr-pj
"""
import asyncio, sys
from pathlib import Path

try:
    import aiosqlite
except ImportError:
    print("❌ aiosqlite não encontrado.")
    sys.exit(1)

DB_PATH = Path(__file__).parent.parent / "data" / "bot_pj.db"

# Lista inicial — um por linha
INITIAL_LIST = """LP - Incompany
PJ - Treinamento Empresa
LP - Treinamento Office
Mais Informação - Mensal - PJ
Cadastro - Impacta
Contato – Corporativo – Locacao"""


async def run():
    print(f"📂 Banco: {DB_PATH}")
    if not DB_PATH.exists():
        print("❌ Banco não encontrado.")
        sys.exit(1)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT value FROM bot_config WHERE key='bypass_hours_identificadores'"
        )
        row = await cursor.fetchone()

        if row and row[0] and row[0].strip():
            print(f"ℹ️  Configuração já existe — não sobrescrevo.")
            print(f"   Valor atual:\n{row[0]}")
            print("\n   Se quiser redefinir, apague a chave e rode novamente.")
            return

        await db.execute(
            """INSERT INTO bot_config (key, value, updated_at)
               VALUES ('bypass_hours_identificadores', ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
            (INITIAL_LIST.strip(),)
        )
        await db.commit()

        items = [i.strip() for i in INITIAL_LIST.strip().split("\n") if i.strip()]
        print(f"\n✅ {len(items)} identificadores cadastrados com bypass de horário:")
        for item in items:
            print(f"   • {item}")
        print("\n   ➡️  Execute: systemctl restart bot-sdr-pj")


if __name__ == "__main__":
    asyncio.run(run())
