"""
Corrige o nome/deal de um lead que foi importado com dados errados do CRM.

Para o caso do Wesley (5511996471803):
  - O sync pegou o telefone do Adilson Souza (contato secundário no deal)
  - O nome correto vem do deal name: "Wesley - IA para C-Level"
  - O deal correto do Wesley é: 69e920943ae1d2001e7319f1

Rodar no servidor:
  cd /opt/bot-sdr-pj && source venv/bin/activate
  python3 fix_lead_name.py
"""

import asyncio
import aiosqlite
import httpx
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from app.core.config import settings

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "bot_pj.db")
BASE    = "https://crm.rdstation.com/api/v1"
TOKEN   = settings.rd_crm_token

# ── Parâmetros do caso a corrigir ────────────────────────────────────────────
PHONE         = "5511996471803"
WESLEY_DEAL   = "69e920943ae1d2001e7319f1"   # deal correto do Wesley no CRM


async def main():
    # 1. Mostra o que está no banco hoje
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT phone_number, contact_name, company, rd_crm_deal_id, source_channel "
            "FROM leads WHERE phone_number=?", (PHONE,)
        )
        row = await cur.fetchone()

    if not row:
        print(f"❌ Nenhum lead encontrado com telefone {PHONE}")
        return

    print("📋 ESTADO ATUAL NO BANCO:")
    for k in row.keys():
        print(f"   {k}: {row[k]!r}")

    # 2. Busca o deal correto no CRM para pegar nome e empresa
    print(f"\n🔍 Buscando deal {WESLEY_DEAL} no CRM...")
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{BASE}/deals/{WESLEY_DEAL}",
            params={"token": TOKEN}
        )
        if r.status_code != 200:
            print(f"❌ Deal não encontrado: {r.status_code}")
            return

        deal = r.json()
        deal_name = deal.get("name", "")
        org = deal.get("organization") or {}
        empresa = org.get("name", "") if isinstance(org, dict) else ""

        # Nome: parte antes do " - " do deal name
        nome_correto = deal_name.split(" - ")[0].strip() if " - " in deal_name else deal_name
        print(f"   deal_name   : {deal_name!r}")
        print(f"   nome_correto: {nome_correto!r}")
        print(f"   empresa     : {empresa!r}")

    # 3. Confirma antes de alterar
    print(f"\n🔧 ALTERAÇÕES PROPOSTAS para {PHONE}:")
    print(f"   contact_name : {row['contact_name']!r} → {nome_correto!r}")
    print(f"   company      : {row['company']!r} → {empresa!r}")
    print(f"   rd_crm_deal_id: {row['rd_crm_deal_id']!r} → {WESLEY_DEAL!r}")

    resp = input("\nConfirmar? (s/N): ").strip().lower()
    if resp != "s":
        print("Cancelado.")
        return

    # 4. Aplica a correção
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE leads
               SET contact_name=?, company=?, rd_crm_deal_id=?
               WHERE phone_number=?""",
            (nome_correto, empresa, WESLEY_DEAL, PHONE)
        )
        await db.commit()

    print("✅ Lead corrigido com sucesso!")


asyncio.run(main())
