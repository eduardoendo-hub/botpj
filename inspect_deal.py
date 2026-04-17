"""
Script de inspeção: mostra todos os campos disponíveis nas tasks e annotations de um deal.
Uso: python inspect_deal.py <phone>
Ex:  python inspect_deal.py 5511973235156
"""
import asyncio, sys, json, re
import httpx

TOKEN = "650976837effcb000df7b64e"
BASE  = "https://crm.rdstation.com/api/v1"

def p(extra=None):
    d = {"token": TOKEN}
    if extra: d.update(extra)
    return d

def clean(phone):
    return re.sub(r"[^\d]", "", phone)

def variants(phone):
    v = [phone]
    if phone.startswith("55") and len(phone) >= 12:
        v.append(phone[2:])
    elif len(phone) <= 11:
        v.append("55" + phone)
    return v

async def main(phone):
    phone = clean(phone)
    async with httpx.AsyncClient(timeout=15) as client:

        # 1. Acha contato
        contact = None
        for variant in variants(phone):
            r = await client.get(f"{BASE}/contacts", params=p({"phone": variant}))
            data = r.json()
            contacts = data.get("contacts", data) if isinstance(data, dict) else data
            if contacts and isinstance(contacts, list):
                cid = contacts[0].get("_id") or contacts[0].get("id")
                r2 = await client.get(f"{BASE}/contacts/{cid}", params=p())
                if r2.status_code == 200:
                    contact = r2.json()
                    break

        if not contact:
            print("❌ Contato não encontrado")
            return

        deal_ids = contact.get("deal_ids", []) or []
        print(f"✅ Contato encontrado. deal_ids: {deal_ids}")

        if not deal_ids:
            print("Sem deals")
            return

        # 2. Pega o primeiro deal
        deal_id = deal_ids[0]
        r = await client.get(f"{BASE}/deals/{deal_id}", params=p())
        deal = r.json()
        print(f"\n── DEAL {deal_id} ──────────────────────────")
        print(f"Etapa: {deal.get('deal_stage', {}).get('name')}")
        print(f"Pipeline: {deal.get('deal_pipeline', {}).get('name')}")
        print(f"Chaves do deal: {sorted(deal.keys())}")

        # 3. Tasks
        print(f"\n── TASKS (/tasks?deal_id={deal_id}) ──────")
        r = await client.get(f"{BASE}/tasks", params=p({"deal_id": deal_id}))
        print(f"Status: {r.status_code}")
        tasks_data = r.json()
        tasks = tasks_data if isinstance(tasks_data, list) else tasks_data.get("tasks", [])
        print(f"Total tasks: {len(tasks)}")
        for i, t in enumerate(tasks[:5]):
            print(f"\n  Task {i+1} — chaves: {sorted(t.keys())}")
            print(f"  subject: {t.get('subject') or t.get('name')}")
            print(f"  type: {t.get('type')}")
            print(f"  done: {t.get('done')}")
            print(f"  done_date: {t.get('done_date')}")
            print(f"  description: {repr(t.get('description'))}")
            print(f"  body: {repr(t.get('body'))}")
            print(f"  note: {repr(t.get('note'))}")
            print(f"  text: {repr(t.get('text'))}")
            # Mostra todos os campos não-nulos
            extras = {k: v for k, v in t.items() if v and k not in ('subject','name','type','done','done_date','description','body','note','text','_id','id')}
            if extras:
                print(f"  outros campos com valor: {json.dumps(extras, ensure_ascii=False, indent=4)}")

        # 4. Annotations
        print(f"\n── ANNOTATIONS (/annotations?deal_id={deal_id}) ──")
        r = await client.get(f"{BASE}/annotations", params=p({"deal_id": deal_id}))
        print(f"Status: {r.status_code}")
        ann_data = r.json()
        anns = ann_data if isinstance(ann_data, list) else ann_data.get("annotations", [])
        print(f"Total annotations: {len(anns)}")
        for i, a in enumerate(anns[:3]):
            print(f"\n  Annotation {i+1} — chaves: {sorted(a.keys())}")
            print(f"  text: {repr(a.get('text', ''))[:200]}")
            print(f"  body: {repr(a.get('body', ''))[:200]}")
            print(f"  note: {repr(a.get('note', ''))[:200]}")
            print(f"  content: {repr(a.get('content', ''))[:200]}")

        # 5. Tenta /deal_activities ou /activities
        for endpoint in ["/deal_activities", "/activities", "/deal_notes"]:
            r = await client.get(f"{BASE}{endpoint}", params=p({"deal_id": deal_id}))
            print(f"\n── {endpoint} → status {r.status_code}")
            if r.status_code == 200:
                d = r.json()
                items = d if isinstance(d, list) else list(d.values())[0] if d else []
                print(f"  Total: {len(items) if isinstance(items, list) else '?'}")
                if isinstance(items, list) and items:
                    print(f"  Chaves do primeiro: {sorted(items[0].keys())}")
                    print(f"  Primeiro: {json.dumps(items[0], ensure_ascii=False, indent=4)[:500]}")

asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "5511999999999"))
