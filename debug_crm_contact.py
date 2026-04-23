"""
Diagnóstico: verifica o que a API do RD CRM retorna para o telefone 5511972554971.
Mostra cada campo relevante do contato e do deal para entender onde está a empresa.

Rodar no servidor:
  cd /opt/bot-sdr-pj
  python3 debug_crm_contact.py
"""

import asyncio
import json
import httpx
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from app.core.config import settings

BASE = "https://crm.rdstation.com/api/v1"
TOKEN = settings.rd_crm_token
PHONE = "5511996471803"


def p(extra=None):
    params = {"token": TOKEN}
    if extra:
        params.update(extra)
    return params


def show(label, obj, indent=2):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(json.dumps(obj, indent=indent, ensure_ascii=False, default=str))


async def main():
    if not TOKEN:
        print("❌ RD_CRM_TOKEN não configurado")
        return

    async with httpx.AsyncClient(timeout=15) as client:
        # ── 1. Busca contato pelo telefone ───────────────────────────────────────
        variants = [PHONE, PHONE[2:] if PHONE.startswith("55") else "55" + PHONE]
        contact = None
        for phone_var in variants:
            print(f"\n🔍 Buscando contato pelo telefone: {phone_var}")
            r = await client.get(f"{BASE}/contacts", params=p({"phone": phone_var}))
            print(f"   Status: {r.status_code}")
            data = r.json()
            contacts = data.get("contacts", data) if isinstance(data, dict) else data
            if isinstance(contacts, list) and contacts:
                cid = contacts[0].get("_id") or contacts[0].get("id")
                print(f"   ✅ Encontrado! ID: {cid}")
                # Busca contato completo
                r2 = await client.get(f"{BASE}/contacts/{cid}", params=p())
                if r2.status_code == 200:
                    contact = r2.json()
                else:
                    contact = contacts[0]
                break
            else:
                print(f"   ⚠️  Nenhum contato encontrado com este telefone")

        if not contact:
            print("\n❌ Contato não encontrado em nenhuma variante do telefone")
            return

        # ── 2. Campos relevantes do contato ─────────────────────────────────────
        print("\n📋 CAMPOS DO CONTATO:")
        fields_to_check = [
            "_id", "id", "name", "email",
            "organization_name", "company",
            "organization", "deal_ids", "phones",
        ]
        for f in fields_to_check:
            val = contact.get(f)
            if val is not None:
                print(f"   {f}: {json.dumps(val, ensure_ascii=False, default=str)[:120]}")

        # Campos customizados
        custom = contact.get("custom_fields") or contact.get("cf_custom_fields") or []
        if custom:
            print(f"\n   custom_fields ({len(custom)} campos):")
            for cf in custom:
                print(f"     - {cf}")

        # ── 3. Deals do contato ──────────────────────────────────────────────────
        deal_ids = contact.get("deal_ids") or []
        print(f"\n📦 DEALS ASSOCIADOS: {deal_ids}")

        for deal_id in deal_ids[:3]:
            r = await client.get(f"{BASE}/deals/{deal_id}", params=p())
            if r.status_code != 200:
                print(f"   ❌ Deal {deal_id}: {r.status_code}")
                continue
            deal = r.json()

            print(f"\n  Deal {deal_id}:")
            deal_fields = [
                "name", "organization_name", "company",
                "organization", "deal_organization",
                "win", "deal_lost_reason",
            ]
            for f in deal_fields:
                val = deal.get(f)
                if val is not None:
                    print(f"    {f}: {json.dumps(val, ensure_ascii=False, default=str)[:120]}")

            # Contatos embutidos no deal
            contacts_in_deal = deal.get("contacts") or []
            print(f"\n    contacts no deal: {len(contacts_in_deal)} item(ns)")
            for i, c in enumerate(contacts_in_deal[:2]):
                if isinstance(c, dict):
                    print(f"      [{i}] organization_name={c.get('organization_name')!r}  "
                          f"company={c.get('company')!r}  "
                          f"organization={c.get('organization')!r}")
                else:
                    print(f"      [{i}] (somente ID) = {c!r}")

        # ── 4. Busca contatos via deal_id (fallback) ─────────────────────────────
        if deal_ids:
            deal_id = deal_ids[0]
            print(f"\n🔍 GET /contacts?deal_id={deal_id}")
            r = await client.get(f"{BASE}/contacts", params=p({"deal_id": deal_id}))
            print(f"   Status: {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                conts = data.get("contacts", data) if isinstance(data, dict) else data
                if isinstance(conts, list):
                    for i, c in enumerate(conts[:2]):
                        print(f"   [{i}] organization_name={c.get('organization_name')!r}  "
                              f"company={c.get('company')!r}  "
                              f"phones={c.get('phones')}")


asyncio.run(main())
