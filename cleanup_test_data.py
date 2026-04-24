#!/usr/bin/env python3
"""
Script de limpeza dos dados gerados pelos testes automatizados — Bot SDR PJ

Remove do banco SQLite (data/bot_pj.db) todos os registros criados pelos runners:
  • run_scenarios.py      → phones 5511900000001 a 5511900000016
  • run_csv_scenarios.py  → phones 5511920000001 a 5511920000099

Uso (sempre com o venv do projeto):
    cd /opt/bot-sdr-pj
    venv/bin/python3 cleanup_test_data.py

Modo dry-run (mostra o que seria apagado sem apagar):
    venv/bin/python3 cleanup_test_data.py --dry-run

Apagar apenas os cenários do CSV:
    venv/bin/python3 cleanup_test_data.py --source csv

Apagar apenas os cenários do run_scenarios.py:
    venv/bin/python3 cleanup_test_data.py --source scenarios
"""

import asyncio
import argparse
import sys
from pathlib import Path

try:
    import aiosqlite
except ImportError:
    print("❌ aiosqlite não encontrado. Execute com o venv do projeto:")
    print("   venv/bin/python3 cleanup_test_data.py")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# PREFIXOS DE TELEFONE DOS TESTES
# ─────────────────────────────────────────────────────────────────────────────

# run_scenarios.py: 5511900000001 → 5511900000016  (16 cenários de trilhas A-D e EDGE)
SCENARIOS_PHONES = [f"5511900000{i:03d}" for i in range(1, 17)]

# run_csv_scenarios.py: 5511920000001 → 5511920000099  (91 cenários do CSV)
CSV_PHONES = [f"551192000{i:04d}" for i in range(1, 100)]

# Telefone padrão da interface de teste manual (/test)
MANUAL_TEST_PHONE = "5511999990000"

ALL_TEST_PHONES = SCENARIOS_PHONES + CSV_PHONES

# Tabelas que recebem dados durante os testes
TABLES = [
    "conversations",
    "bot_sessions",
    "leads",
]

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def resolve_db_path() -> Path:
    """Tenta localizar o banco de dados em caminhos típicos do projeto."""
    candidates = [
        Path("data/bot_pj.db"),
        Path("/opt/bot-sdr-pj/data/bot_pj.db"),
        Path(__file__).parent / "data" / "bot_pj.db",
    ]
    for p in candidates:
        if p.exists():
            return p
    # Se nenhum encontrado, retorna o caminho relativo (pode criar erro descritivo depois)
    return Path("data/bot_pj.db")


async def count_records(db, table: str, phones: list[str]) -> int:
    placeholders = ",".join("?" * len(phones))
    row = await db.execute_fetchall(
        f"SELECT COUNT(*) FROM {table} WHERE phone_number IN ({placeholders})",
        phones,
    )
    return row[0][0] if row else 0


async def delete_records(db, table: str, phones: list[str]) -> int:
    placeholders = ",".join("?" * len(phones))
    cursor = await db.execute(
        f"DELETE FROM {table} WHERE phone_number IN ({placeholders})",
        phones,
    )
    return cursor.rowcount


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Limpa dados de teste do Bot SDR PJ")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Apenas mostra quantos registros seriam apagados, sem apagar",
    )
    parser.add_argument(
        "--source", choices=["all", "csv", "scenarios"], default="all",
        help="Qual fonte de testes limpar (padrão: all)",
    )
    parser.add_argument(
        "--db", default=None,
        help="Caminho explícito para o banco SQLite (ex: data/bot_pj.db)",
    )
    parser.add_argument(
        "--include-manual", action="store_true",
        help=f"Incluir também o phone da interface manual ({MANUAL_TEST_PHONE})",
    )
    args = parser.parse_args()

    # Seleciona phones conforme a fonte
    if args.source == "csv":
        phones = CSV_PHONES[:]
    elif args.source == "scenarios":
        phones = SCENARIOS_PHONES[:]
    else:
        phones = ALL_TEST_PHONES[:]

    if args.include_manual:
        phones.append(MANUAL_TEST_PHONE)

    # Resolve path do banco
    db_path = Path(args.db) if args.db else resolve_db_path()

    if not db_path.exists():
        print(f"❌ Banco de dados não encontrado: {db_path}")
        print("   Verifique se está na pasta correta do projeto.")
        sys.exit(1)

    print(f"\n🧹 Cleanup de dados de teste — Bot SDR PJ")
    print(f"   Banco:     {db_path}")
    print(f"   Fonte:     {args.source}")
    print(f"   Phones:    {len(phones)} números de teste")
    print(f"   Modo:      {'DRY-RUN (nada será apagado)' if args.dry_run else 'EXECUÇÃO REAL'}\n")

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        # ── Contagem prévia ────────────────────────────────────────────────
        print("  Contando registros de teste existentes...")
        total_count = 0
        counts: dict[str, int] = {}
        for table in TABLES:
            try:
                n = await count_records(db, table, phones)
                counts[table] = n
                total_count += n
                print(f"    {table:<20} → {n} registro(s)")
            except Exception as e:
                print(f"    {table:<20} → ⚠️  erro: {e}")
                counts[table] = 0

        if total_count == 0:
            print(f"\n  ✅ Nenhum dado de teste encontrado. Banco já está limpo.")
            return 0

        print(f"\n  Total a remover: {total_count} registro(s)")

        if args.dry_run:
            print("\n  ⚠️  DRY-RUN — nenhum dado foi apagado.")
            print("  Para apagar de verdade, rode sem --dry-run.")
            return 0

        # ── Confirmação ────────────────────────────────────────────────────
        print()
        confirm = input("  ⚠️  Confirma a exclusão? (s/N): ").strip().lower()
        if confirm not in ("s", "sim", "yes", "y"):
            print("  Operação cancelada.")
            return 0

        # ── Exclusão ───────────────────────────────────────────────────────
        print()
        total_deleted = 0
        for table in TABLES:
            if counts[table] == 0:
                continue
            try:
                n = await delete_records(db, table, phones)
                total_deleted += n
                print(f"    🗑  {table:<20} → {n} linha(s) removida(s)")
            except Exception as e:
                print(f"    ❌ {table:<20} → erro: {e}")

        await db.commit()

        print(f"\n  ✅ Limpeza concluída — {total_deleted} registro(s) removido(s).")
        print(f"  Banco: {db_path}\n")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
