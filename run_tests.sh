#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  run_tests.sh — Executa todos os testes do Bot SDR PJ e abre o relatório
#  Uso: ./run_tests.sh [URL_BASE]
#  Ex:  ./run_tests.sh http://localhost:8001
#       ./run_tests.sh http://204.168.224.108/pj
# ─────────────────────────────────────────────────────────────────────────────

set -e

BASE_URL="${1:-http://localhost:8001}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║      Bot SDR PJ — Suite de Testes Automatizados      ║"
echo "╚══════════════════════════════════════════════════════╝"
echo "  URL: $BASE_URL"
echo ""

# ── Detecta Python / venv ────────────────────────────────────────────────────
if [ -f "venv/bin/python3" ]; then
    PYTHON="venv/bin/python3"
elif [ -f ".venv/bin/python3" ]; then
    PYTHON=".venv/bin/python3"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
else
    echo "❌ Python 3 não encontrado. Instale Python ou ative o venv."
    exit 1
fi

echo "  Python: $($PYTHON --version)"
echo ""

# ── Rodando run_scenarios.py (trilhas A-D + EDGE) ────────────────────────────
echo "► [1/2] Trilhas A-D (cenários de conversa completos)..."
$PYTHON tests/run_scenarios.py --base-url "$BASE_URL" \
    --output tests/relatorio_trilhas.html \
    --json   tests/relatorio_trilhas.json \
    || echo "⚠️  run_scenarios.py encerrou com erros (verifique o relatório)"

# ── Rodando run_csv_scenarios.py (91 casos do CSV) ───────────────────────────
echo ""
echo "► [2/2] Casos unitários do CSV (91 cenários T01→T13)..."
$PYTHON tests/run_csv_scenarios.py --base-url "$BASE_URL" \
    --output tests/relatorio_csv_testes.html \
    --json   tests/relatorio_csv_testes.json \
    || echo "⚠️  run_csv_scenarios.py encerrou com erros (verifique o relatório)"

# ── Abre relatórios no navegador ─────────────────────────────────────────────
echo ""
echo "✅ Testes concluídos! Abrindo relatórios..."

if [[ "$OSTYPE" == "darwin"* ]]; then
    open tests/relatorio_trilhas.html    2>/dev/null || true
    open tests/relatorio_csv_testes.html 2>/dev/null || true
elif command -v xdg-open &>/dev/null; then
    xdg-open tests/relatorio_trilhas.html    2>/dev/null || true
    xdg-open tests/relatorio_csv_testes.html 2>/dev/null || true
fi

echo ""
echo "  Relatórios salvos em:"
echo "  • tests/relatorio_trilhas.html"
echo "  • tests/relatorio_csv_testes.html"
echo ""
