"""
Teste manual da API Tallos v2 — histórico COMPLETO de conversas.
Inclui mensagens do bot/automação (sent_by=bot).

Uso:
    python test_tallos_history.py [telefone]
    python test_tallos_history.py 5511916010159
"""
import sqlite3, urllib.request, urllib.parse, json, sys

phone_arg = sys.argv[1] if len(sys.argv) > 1 else "5511916010159"
phone_fragment = phone_arg[-9:]  # últimos 9 dígitos para busca no banco

print(f"\n{'='*60}")
print(f"  Teste Tallos History API — sent_by: customer + operator + bot")
print(f"  Telefone: {phone_arg} (busca fragment: {phone_fragment})")
print(f"{'='*60}\n")

# ── 1. Busca lead no banco local ──────────────────────────────────────────────
db = sqlite3.connect("/opt/bot-sdr-pj/data/bot_pj.db")
row = db.execute(
    "SELECT phone_number, contact_name, notes FROM leads WHERE phone_number LIKE ?",
    (f"%{phone_fragment}%",)
).fetchone()

if not row:
    print(f"[AVISO] Lead nao encontrado no banco local para fragment={phone_fragment!r}")
    print("        Tentando buscar contact_id diretamente pela API...")
    phone, contact_name, notes = phone_arg, "", ""
else:
    phone, contact_name, notes = row
    print(f"  Lead encontrado: {contact_name} | {phone}")
    print(f"  Notes: {notes}")

# ── 2. Extrai contact_id das notes (separador pode ser ; ou |) ────────────────
contact_id = ""
for sep in (";", "|"):
    for part in (notes or "").split(sep):
        part = part.strip()
        if part.startswith("tallos_contact_id:"):
            contact_id = part.replace("tallos_contact_id:", "").strip()
            break
    if contact_id:
        break

print(f"\n  contact_id nas notes: {contact_id!r}")

# ── 3. Lê credenciais do .env ─────────────────────────────────────────────────
token = ""
jwk_str = ""
with open("/opt/bot-sdr-pj/.env") as f:
    for line in f:
        if line.startswith("TALLOS_API_TOKEN="):
            token = line.strip().split("=", 1)[1].strip().strip('"')
        elif line.startswith("TALLOS_JWK_KEY="):
            jwk_str = line.strip().split("=", 1)[1].strip().strip('"')

print(f"  Token: {token[:12]}...{token[-6:] if len(token) > 18 else ''}")
print(f"  JWK  : {'configurada (' + str(len(jwk_str)) + ' chars)' if jwk_str else 'NAO configurada'}\n")

if not token:
    print("[ERRO] TALLOS_API_TOKEN nao encontrado no .env")
    sys.exit(1)

# ── 4. Lookup contact_id pela API se nao veio das notes ──────────────────────
if not contact_id:
    print("── Buscando contact_id via GET /contacts/{phone}/exists ──")
    lookup_url = f"https://api.tallos.com.br/v2/contacts/{phone}/exists?channel=whatsapp"
    req = urllib.request.Request(lookup_url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req) as r:
            body = json.loads(r.read().decode())
        contact = body.get("date") or body.get("data") or body
        contact_id = str(contact.get("_id", "")) if isinstance(contact, dict) else ""
        print(f"  _id obtido: {contact_id!r}")
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.read()[:200]}")
    except Exception as e:
        print(f"  Erro: {e}")

if not contact_id:
    print("[ERRO] Nao foi possivel obter o contact_id. Verifique o telefone e o token.")
    sys.exit(1)

# ── 5. Chama a API de histórico com sent_by=customer+operator+bot ─────────────
print(f"── Buscando histórico completo para customer_id={contact_id} ──")

params = urllib.parse.urlencode([
    ("customer_id", contact_id),
    ("limit", 100),
    ("page", 1),
    ("sent_by", "customer"),
    ("sent_by", "operator"),
    ("sent_by", "bot"),
    ("type", "text"),
], doseq=False)

# urllib.parse.urlencode nao suporta multi-value diretamente sem doseq
# Monta manualmente para garantir múltiplos sent_by e type
params = (
    f"customer_id={urllib.parse.quote(contact_id)}"
    f"&limit=100&page=1"
    f"&sent_by=customer&sent_by=operator&sent_by=bot"
    f"&type=text"
)

history_url = f"https://api.tallos.com.br/v2/messages/history?{params}"
print(f"  URL: {history_url}\n")

req = urllib.request.Request(history_url, headers={"Authorization": f"Bearer {token}"})
try:
    with urllib.request.urlopen(req) as r:
        raw_bytes = r.read()
        print(f"  HTTP {r.status} | {len(raw_bytes)} bytes recebidos")
        data = json.loads(raw_bytes.decode("utf-8"), strict=False)
        print(f"  Chaves do response: {list(data.keys())}")

    msgs_field = data.get("messages", "")
    print(f"  Tipo de 'messages': {type(msgs_field).__name__}")

    if isinstance(msgs_field, list):
        # Sem criptografia — lista direta
        print(f"\n  Total mensagens (lista): {len(msgs_field)}")
        msgs_field.sort(key=lambda m: m.get("created_at", ""))
        for i, m in enumerate(msgs_field, 1):
            sent_by = m.get("sent_by", "?")
            content = m.get("message", m.get("content", m.get("text", "")))
            ts      = str(m.get("created_at", ""))[:19]
            print(f"  [{i:03d}] {sent_by:<10} {ts} | {str(content)[:80]!r}")

    elif isinstance(msgs_field, str) and msgs_field:
        # String criptografada — descriptografa
        print(f"\n  Descriptografando...")
        if not jwk_str:
            print("[ERRO] TALLOS_JWK_KEY nao configurada — nao e possivel descriptografar.")
            print(f"  encrypted (primeiros 100 chars): {msgs_field[:100]}")
            sys.exit(1)

        sys.path.insert(0, "/opt/bot-sdr-pj/venv/lib/python3.12/site-packages")
        from jwcrypto import jwe, jwk as jwklib

        key = jwklib.JWK(**json.loads(jwk_str))
        t = jwe.JWE()
        t.deserialize(msgs_field, key)
        plain = t.payload.decode("latin-1", "replace")
        msgs = json.loads(plain, strict=False)
        msgs.sort(key=lambda m: m.get("created_at", ""))

        print(f"\n{'='*60}")
        print(f"  CONVERSA COMPLETA — {len(msgs)} mensagens")
        print(f"{'='*60}")

        from collections import Counter
        counts = Counter(m.get("sent_by", "?") for m in msgs)

        for i, m in enumerate(msgs, 1):
            sent_by  = m.get("sent_by", "?")
            content  = m.get("message", m.get("content", m.get("text", "")))
            ts       = str(m.get("created_at", ""))[:19].replace("T", " ")
            op_name  = m.get("operator_name", "")
            label    = f"{sent_by}" + (f" ({op_name})" if op_name else "")
            icon     = {"customer": "👤", "operator": "🧑", "bot": "🤖"}.get(sent_by, "❓")
            print(f"\n  [{i:03d}] {icon} {label:<22} {ts}")
            for line in str(content).replace("\\n", "\n").split("\n")[:4]:
                print(f"         {line}")

        print(f"\n{'='*60}")
        print(f"  RESUMO")
        for k, v in sorted(counts.items()):
            icon = {"customer": "👤", "operator": "🧑", "bot": "🤖"}.get(k, "❓")
            print(f"  {icon} {k}: {v} mensagens")
        print(f"{'='*60}\n")

    else:
        print(f"\n  Campo 'messages' vazio ou tipo inesperado: {type(msgs_field)}")
        print(f"  Response completo: {json.dumps(data, indent=2)[:600]}")

except urllib.error.HTTPError as e:
    body = e.read()
    print(f"\n  HTTP {e.code}: {body[:400]}")
except Exception:
    import traceback; traceback.print_exc()
