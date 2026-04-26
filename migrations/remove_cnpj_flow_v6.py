#!/usr/bin/env python3
"""
Migração v6 — Remove CNPJ completamente do fluxo de atendimento.

O bot NÃO deve pedir CNPJ em nenhum momento: nem na qualificação inicial,
nem no fechamento da proposta. O consultor coleta o CNPJ diretamente,
quando necessário, fora do chat.

Uso:
    cd /opt/bot-sdr-pj
    venv/bin/python3 migrations/remove_cnpj_flow_v6.py
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

# Cada tupla: (texto_atual_no_banco, substituição)
PATCHES = [
    # --- Resultado da v5: instrução de pedir CNPJ no fechamento ---
    (
        "• CNPJ: peça SOMENTE ao fechar os dados para proposta formal — nunca no início da conversa",
        "• CNPJ: NÃO solicite em nenhum momento. O consultor coleta o CNPJ diretamente, quando necessário."
    ),
    # --- Trilha B: remover CNPJ do fechamento ---
    (
        "TRILHA B (equipe/empresa/in company): colete nome completo, empresa, curso, número de alunos e modalidade. CNPJ apenas ao fechar os dados para proposta.",
        "TRILHA B (equipe/empresa/in company): colete nome completo, empresa, curso, número de alunos e modalidade."
    ),
    # --- Instrução de peça a empresa + CNPJ no final ---
    (
        "• Se lead PJ selecionar empresa → peça apenas o nome da empresa. CNPJ é pedido somente ao final, quando o bot vai compilar os dados para a proposta formal.",
        "• Se lead PJ selecionar empresa → peça apenas o nome da empresa."
    ),
    # --- Regra de fluxo que menciona CNPJ ---
    (
        "Nunca pule para pedir empresa/CNPJ antes de perguntar a modalidade.",
        "Nunca pule para pedir empresa antes de perguntar a modalidade."
    ),
    # --- Dados mínimos para proposta (objeção e-mail) ---
    (
        "empresa, CNPJ, curso, número de alunos, modalidade e prazo",
        "empresa, curso, número de alunos, modalidade e prazo"
    ),
    # --- Variante com vírgula diferente ---
    (
        "empresa, CNPJ, curso, número de alunos, modalidade",
        "empresa, curso, número de alunos, modalidade"
    ),
    # --- Dados mínimos com razão social ---
    (
        "nome completo, empresa, CNPJ, razão social, curso",
        "nome completo, empresa, curso"
    ),
    # --- Passo 3 com "sem CNPJ ainda" (caso v5 parcial) ---
    (
        "3. Nome da empresa (sem CNPJ ainda)",
        "3. Nome da empresa"
    ),
    # --- Caso alguma versão ainda tenha a instrução original da v4 ---
    (
        "3. Nome da empresa (apenas — NÃO peça CNPJ agora)",
        "3. Nome da empresa"
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
                print(f"  ✅ Patch aplicado: '{old[:70]}...'")
            else:
                print(f"  ℹ️  Não encontrado (já corrigido ou ausente): '{old[:70]}...'")

        # Verificação final: checar se sobrou alguma instrução de pedir CNPJ
        remaining = [l.strip() for l in prompt.split('\n')
                     if 'cnpj' in l.lower() and any(v in l.lower() for v in ['peça', 'pedir', 'solicite', 'colete', 'informe', 'preciso'])]
        if remaining:
            print(f"\n⚠️  Ainda há instruções de coleta de CNPJ no prompt:")
            for r in remaining:
                print(f"    → {r}")
        else:
            print(f"\n✅ Nenhuma instrução de coleta de CNPJ restante no prompt.")

        if changes > 0:
            await db.execute(
                "UPDATE system_config SET value=?, updated_at=CURRENT_TIMESTAMP WHERE key='system_prompt'",
                (prompt,)
            )
            await db.commit()
            print(f"\n✅ {changes} patch(es) aplicado(s). Prompt: {original_len} → {len(prompt)} chars")
        else:
            print("\nℹ️  Nenhuma alteração necessária — prompt já está correto.")

        print("   ➡️  Execute: systemctl restart bot-sdr-pj")


if __name__ == "__main__":
    asyncio.run(run())
