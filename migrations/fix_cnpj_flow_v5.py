#!/usr/bin/env python3
"""
Migração v5 — Remove CNPJ do fluxo inicial de qualificação.

CNPJ agora é pedido apenas no momento de fechar os dados para a proposta,
não logo quando o lead informa que é de uma empresa.

Uso:
    cd /opt/bot-sdr-pj
    venv/bin/python3 migrations/fix_cnpj_flow_v5.py
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

# Patches a aplicar no system prompt (busca → substitui)
PATCHES = [
    (
        "3. Empresa + CNPJ (juntos, se PJ)",
        "3. Nome da empresa (apenas — NÃO peça CNPJ agora)"
    ),
    (
        "• CNPJ: sempre mencionar quando lead for PJ (empresa)",
        "• CNPJ: peça SOMENTE ao fechar os dados para proposta formal — nunca no início da conversa"
    ),
    (
        "3. Empresa + CNPJ (juntos, se PJ)\n4.",
        "3. Nome da empresa (sem CNPJ ainda)\n4."
    ),
    # Regra de escalação Trilha B — remover CNPJ da lista de coleta inicial
    (
        "TRILHA B (equipe/empresa/in company): colete nome completo, empresa, CNPJ e número de alunos.",
        "TRILHA B (equipe/empresa/in company): colete nome completo, empresa, curso, número de alunos e modalidade. CNPJ apenas ao fechar os dados para proposta."
    ),
    (
        "• Se lead PJ selecionar empresa → peça nome da empresa E CNPJ juntos.",
        "• Se lead PJ selecionar empresa → peça apenas o nome da empresa. CNPJ é pedido somente ao final, quando o bot vai compilar os dados para a proposta formal."
    ),
]


async def run():
    print(f"📂 Banco: {DB_PATH}")
    if not DB_PATH.exists():
        print("❌ Banco não encontrado.")
        sys.exit(1)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT value FROM system_config WHERE key='system_prompt'")
        row = await cursor.fetchone()
        if not row:
            print("❌ system_prompt não encontrado no banco.")
            sys.exit(1)

        prompt = row[0]
        original_len = len(prompt)
        changes = 0

        for old, new in PATCHES:
            if old in prompt:
                prompt = prompt.replace(old, new)
                changes += 1
                print(f"  ✅ Patch aplicado: '{old[:60]}...'")
            else:
                print(f"  ℹ️  Trecho não encontrado (pode já estar corrigido): '{old[:60]}...'")

        if changes > 0:
            await db.execute(
                "UPDATE system_config SET value=?, updated_at=CURRENT_TIMESTAMP WHERE key='system_prompt'",
                (prompt,)
            )
            await db.commit()
            print(f"\n✅ {changes} patch(es) aplicado(s). Prompt: {original_len} → {len(prompt)} chars")
        else:
            print("\nℹ️  Nenhuma alteração necessária.")

        print("   ➡️  Execute: systemctl restart bot-sdr-pj")


if __name__ == "__main__":
    asyncio.run(run())
