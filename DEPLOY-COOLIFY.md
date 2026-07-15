# Deploy do Bot SDR PJ (+ Radar) no Coolify

Migração do servidor antigo (`204.168.224.108`, systemd+nginx+venv, servido sob `/pj/`)
para o **Coolify dos LPs** (Hetzner `159.69.240.1`, Traefik + Let's Encrypt automático).
Auto-deploy nativo via GitHub App a cada push na `main`, build pelo `Dockerfile`.

- **Domínios:** `botpj.technowhub.ai` (admin/bot) e `radar.technowhub.ai` (radar)
- **Porta interna:** `8001`
- **Banco:** SQLite em `/app/data/bot_pj.db` (volume persistente)

> **Mudança importante:** no Coolify o app roda na **raiz do subdomínio** (sem o prefixo `/pj`).
> Isso é controlado por `APP_URL_PREFIX` (padrão vazio). O radar e o admin passam a ser:
> - `https://botpj.technowhub.ai/admin` (antes `/pj/admin`)
> - `https://radar.technowhub.ai/radar/login` (a raiz `radar.technowhub.ai` já redireciona pra lá)

---

## 1. DNS (GoDaddy)

```
A   botpj   159.69.240.1   (TTL 60)
A   radar   159.69.240.1   (TTL 60)
```

## 2. Coolify — criar a aplicação

1. **+ New Resource** → **Repository** (GitHub App) → `eduardoendo-hub/botpj`, branch `main`.
2. **Build Pack:** `Dockerfile`.
3. **Port:** `8001`.
4. **Domains:** adicione os **dois** domínios no mesmo recurso, separados por vírgula:
   `https://botpj.technowhub.ai,https://radar.technowhub.ai`

## 3. Volume persistente

**Storages** → Add Persistent Volume:

- **Name:** `botpj-data`
- **Mount Path:** `/app/data`

## 4. Environment Variables

Veja `.env.example`. Principais:

| Variável | Observação |
|---|---|
| `ANTHROPIC_API_KEY` | Claude |
| `TALLOS_API_TOKEN` / `TALLOS_JWK_KEY` / `TALLOS_ACCOUNT_ID` | RD Conversas (histórico criptografado) |
| `RD_CRM_TOKEN` | RD Station CRM |
| `APP_SECRET_KEY` | `token_hex(32)` |
| `ADMIN_PASSWORD_HASH` / `CONSULTANT_PASSWORD_HASH` | gerar com `python3 hash_password.py` |
| `APP_URL_PREFIX` | **deixe vazio** (raiz do subdomínio) |

## 5. Deploy + volume + banco

1. **Deploy** e aguarde o health check (`/health`).
2. Migrar o banco antigo `/opt/bot-sdr-pj/data/bot_pj.db` para o volume `botpj-data`
   (mesmo procedimento do botmba — ver `DEPLOY-COOLIFY.md` do botmba, seção 6, com o app parado).

## 6. Webhooks

- **RD Conversas (geral):** continua no **botmba** (`https://botmba.technowhub.ai/webhook/tallos`).
  O botmba reencaminha para o botpj via `PJ_WEBHOOK_FORWARD_URL` — **substitui o nginx mirror**.
- **RD Conversas (cadastro lead PJ):** `https://botpj.technowhub.ai/webhook/tallospj`

## 7. Avisar quem usa o Radar

A URL de login muda de `http://204.168.224.108/pj/radar/login` para
`https://radar.technowhub.ai/radar/login`. Os usuários e senhas seguem no banco migrado.

## 8. Desligar o antigo

Após validar: `systemctl stop bot-sdr-pj && systemctl disable bot-sdr-pj` no servidor antigo,
e remover os blocos `/pj/` e o `mirror` do nginx.
