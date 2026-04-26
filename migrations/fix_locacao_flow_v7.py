#!/usr/bin/env python3
"""
Migração v7 — Corrige fluxo de locação de espaço (Trilha D).

Problemas resolvidos:
  1. Bot perguntava "tipo de evento" → lead dizia "treinamento" → bot trocava de trilha
  2. Bot pedia modalidade (presencial/online/EAD) dentro do fluxo de locação
  3. Bot insistia em data/horário exato quando lead não tinha ainda

Nova regra: para locação, coletar APENAS número de pessoas, data (pode ser
aproximada) e horário (pode ser "a confirmar"). Consultor trata o resto.

Uso:
    cd /opt/bot-sdr-pj
    venv/bin/python3 migrations/fix_locacao_flow_v7.py
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

PATCHES = [
    # ── Seção principal de locação: simplificar dados coletados ──────────────
    (
        """LOCAÇÃO DE ESPAÇO — DADOS A COLETAR
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ao iniciar fluxo de locação, pergunte:
1. Tipo de evento (treinamento, workshop, reunião, palestra, etc.)
2. Data e horário do evento
3. Número de pessoas (capacidade máxima da sala)
4. Layout desejado (laboratório com computadores / mesas e cadeiras / auditório / mesas redondas)

Sempre use as palavras "data", "evento", "pessoas" e "layout" nessa coleta.
Ao responder sobre locação, mencione também: "serviços incluídos" (coffee break, projetor, internet) e "política de cancelamento".""",

        """LOCAÇÃO DE ESPAÇO — DADOS A COLETAR
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ao iniciar fluxo de locação, colete APENAS:
1. Número de pessoas (capacidade necessária)
2. Data desejada (pode ser aproximada: "próxima semana", "mês que vem" já é suficiente)
3. Horário (pode ser "a confirmar" — não insista se o lead não souber)

REGRAS CRÍTICAS DO FLUXO DE LOCAÇÃO:
• NÃO pergunte "tipo de evento" — se o lead mencionar "treinamento", "palestra" ou qualquer finalidade, isso é apenas contexto. NUNCA troque para o fluxo de treinamentos.
• NÃO pergunte sobre layout, modalidade (presencial/online/EAD) nem sobre curso.
• Se o lead disser que vai usar o espaço para treinamento PRÓPRIO da empresa, isso é LOCAÇÃO — mantenha a Trilha D.
• Data e horário aproximados são suficientes. Se o lead não souber o horário, anote "horário a confirmar" e siga em frente.
• Após coletar pessoas + data (mesmo que aproximada), encaminhe para o consultor."""
    ),

    # ── Definição da Trilha D: tornar mais clara ──────────────────────────────
    (
        "• Trilha D (locação de espaço): evento, workshop, reunião no local",
        "• Trilha D (locação de espaço): lead quer alugar sala/espaço físico — independente do que vai fazer lá (evento, workshop, treinamento próprio, reunião). NUNCA mude para outra trilha por causa da finalidade."
    ),

    # ── Escalação Trilha D ────────────────────────────────────────────────────
    (
        "• TRILHA D (locação): use EXATAMENTE: \"nosso consultor poderá confirmar disponibilidade e valores — em breve entrará em contato\".",
        "• TRILHA D (locação): colete número de pessoas + data aproximada. Horário é opcional. Use EXATAMENTE: \"nosso consultor poderá confirmar disponibilidade e valores — em breve entrará em contato\"."
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
                print(f"  ✅ Patch aplicado: '{old[:70].strip()}...'")
            else:
                print(f"  ℹ️  Não encontrado: '{old[:70].strip()}...'")

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
