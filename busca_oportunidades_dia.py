"""
Busca todas as oportunidades criadas em um determinado dia no RD CRM.

Uso:
  python busca_oportunidades_dia.py              → ontem
  python busca_oportunidades_dia.py 2026-04-17   → data específica
  python busca_oportunidades_dia.py 2026-04-17 PIPELINE_ID

A API v1 suporta:
  created_at_period=true + start_date + end_date + deal_pipeline_id
"""

import asyncio
import sys
import json
from datetime import datetime, timedelta, timezone

import httpx

TOKEN = "650976837effcb000df7b64e"
BASE  = "https://crm.rdstation.com/api/v1"


def params(extra: dict = None) -> dict:
    p = {"token": TOKEN}
    if extra:
        p.update(extra)
    return p


async def buscar_oportunidades(data_iso: str, pipeline_id: str = None):
    """
    Busca deals criados em data_iso (formato YYYY-MM-DD).
    Opcional: filtrar por pipeline_id.
    """
    start = f"{data_iso}T00:00:00"
    end   = f"{data_iso}T23:59:59"

    query = {
        "created_at_period": "true",
        "start_date":        start,
        "end_date":          end,
        "page":              1,
        "limit":             200,
    }
    if pipeline_id:
        query["deal_pipeline_id"] = pipeline_id

    print(f"\n🔍 Buscando oportunidades criadas em {data_iso}...")
    if pipeline_id:
        print(f"   Funil: {pipeline_id}")
    print(f"   Período: {start} → {end}\n")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{BASE}/deals", params=params(query))

        if resp.status_code != 200:
            print(f"❌ Erro HTTP {resp.status_code}: {resp.text[:300]}")
            return

        data = resp.json()
        deals = data if isinstance(data, list) else data.get("deals", [])
        total = data.get("total", len(deals)) if isinstance(data, dict) else len(deals)

        print(f"✅ Total encontrado: {total} oportunidade(s)\n")
        print("=" * 70)

        if not deals:
            print("Nenhuma oportunidade encontrada para este período.")
            return

        for i, deal in enumerate(deals, 1):
            nome       = deal.get("name") or "—"
            created_at = deal.get("created_at") or "—"
            stage      = deal.get("deal_stage", {}) or {}
            etapa      = stage.get("name", "—") if isinstance(stage, dict) else "—"
            pipeline   = deal.get("deal_pipeline", {}) or {}
            funil      = pipeline.get("name", "—") if isinstance(pipeline, dict) else "—"
            user       = deal.get("user", {}) or {}
            consultor  = user.get("name", "—") if isinstance(user, dict) else "—"
            valor      = deal.get("amount_total") or deal.get("amount_unique") or 0
            deal_id    = deal.get("_id") or deal.get("id") or "—"
            win        = deal.get("win")
            lost       = bool(deal.get("deal_lost_reason"))
            status     = "🏆 Ganho" if win else ("❌ Perdido" if lost else "🔄 Em aberto")

            print(f"[{i:02d}] {nome}")
            print(f"      ID:        {deal_id}")
            print(f"      Status:    {status}")
            print(f"      Funil:     {funil}")
            print(f"      Etapa:     {etapa}")
            print(f"      Consultor: {consultor}")
            print(f"      Valor:     R$ {float(valor):.2f}")
            print(f"      Criado em: {created_at}")
            print()

        print("=" * 70)
        print(f"Total: {len(deals)} de {total} oportunidades exibidas")

        # Salva resultado completo em JSON para inspeção
        output_file = f"resultado_oportunidades_{data_iso}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(deals, f, ensure_ascii=False, indent=2)
        print(f"\n💾 Resultado completo salvo em: {output_file}")


async def listar_pipelines():
    """Lista todos os funis disponíveis para descobrir o ID correto."""
    print("\n📋 Listando funis disponíveis no RD CRM...\n")
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{BASE}/deal_pipelines", params=params())
        if resp.status_code != 200:
            print(f"❌ Erro {resp.status_code}: {resp.text[:200]}")
            return
        data = resp.json()
        pipelines = data if isinstance(data, list) else data.get("deal_pipelines", [])
        for p in pipelines:
            pid  = p.get("_id") or p.get("id")
            name = p.get("name", "—")
            print(f"  ID: {pid}  →  {name}")
    print()


async def main():
    args = sys.argv[1:]

    # Sem argumentos → mostra funis + ontem
    if not args or args[0] == "--pipelines":
        await listar_pipelines()
        if args and args[0] == "--pipelines":
            return

    # Data
    if args and args[0] != "--pipelines":
        data_iso = args[0]
    else:
        ontem = datetime.now(timezone.utc) - timedelta(days=1)
        data_iso = ontem.strftime("%Y-%m-%d")

    # Pipeline (opcional)
    pipeline_id = args[1] if len(args) >= 2 else None

    await buscar_oportunidades(data_iso, pipeline_id)


if __name__ == "__main__":
    asyncio.run(main())
