# Bot SDR PJ — Guia de Deploy

## Pré-requisitos

- Python 3.11+
- Servidor Ubuntu 22.04 (mesmo servidor do BOT MBA)
- Acesso SSH root ou sudo
- Domínio/subdomínio apontando para o servidor (opcional, para SSL)

---

## 1. Clonar / Copiar o Projeto

```bash
# Copiar os arquivos do projeto para o servidor
scp -r BOT-SDR-PJ/ root@204.168.224.108:/opt/bot-sdr-pj/

# Ou via git
git clone <seu-repositorio> /opt/bot-sdr-pj
cd /opt/bot-sdr-pj
```

---

## 2. Configurar Ambiente Virtual e Dependências

```bash
cd /opt/bot-sdr-pj
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## 3. Configurar Variáveis de Ambiente

```bash
cp .env.example .env
nano .env
```

Preencher obrigatoriamente:
- `ANTHROPIC_API_KEY` — chave da API Anthropic
- `TALLOS_API_TOKEN` — token da API RD Conversas/Tallos
- `TALLOS_ACCOUNT_ID` — ID da conta no Tallos
- `ADMIN_PASSWORD` — senha do painel administrativo
- `APP_SECRET_KEY` — chave secreta para sessões (gerar com `python3 -c "import secrets; print(secrets.token_hex(32))"`)

---

## 4. Criar Banco de Dados

O banco SQLite é criado automaticamente na primeira execução em `data/bot_pj.db`.

```bash
mkdir -p data
```

---

## 5. Testar Localmente

```bash
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

Acesse: http://localhost:8001/admin

---

## 6. Configurar como Serviço Systemd

```bash
# Criar arquivo de serviço
cat > /etc/systemd/system/bot-sdr-pj.service << 'EOF'
[Unit]
Description=Bot SDR PJ — Treinamentos Corporativos
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/bot-sdr-pj
Environment="PATH=/opt/bot-sdr-pj/venv/bin"
ExecStart=/opt/bot-sdr-pj/venv/bin/gunicorn main:app \
    -w 2 \
    -k uvicorn.workers.UvicornWorker \
    --bind 127.0.0.1:8001 \
    --timeout 120 \
    --access-logfile /var/log/bot-sdr-pj/access.log \
    --error-logfile /var/log/bot-sdr-pj/error.log
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Criar diretório de logs
mkdir -p /var/log/bot-sdr-pj
chown www-data:www-data /var/log/bot-sdr-pj

# Ajustar permissões do projeto
chown -R www-data:www-data /opt/bot-sdr-pj

# Ativar e iniciar o serviço
systemctl daemon-reload
systemctl enable bot-sdr-pj
systemctl start bot-sdr-pj
systemctl status bot-sdr-pj
```

---

## 7. Configurar NGINX

```bash
# Copiar configuração
cp deploy/nginx.conf /etc/nginx/sites-available/bot-sdr-pj

# Editar com seu domínio real
nano /etc/nginx/sites-available/bot-sdr-pj

# Ativar o site
ln -s /etc/nginx/sites-available/bot-sdr-pj /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

---

## 8. Configurar SSL com Let's Encrypt (opcional)

```bash
certbot --nginx -d bot-pj.seudominio.com.br
```

---

## 9. Integração com BOT MBA (Webhook Compartilhado)

O RD Conversas suporta apenas **um único webhook URL**. A arquitetura funciona assim:

```
RD Conversas
    └─→ http://204.168.224.108/webhook/tallos  (BOT MBA — porta 8000)
            └─→ Detecta leads PJ (source_channel = tallos_pj)
            └─→ Encaminha para → http://127.0.0.1:8001/webhook/tallos  (BOT SDR PJ)
```

**No BOT MBA**, é necessário adicionar o forward interno para o Bot SDR PJ quando detectar leads PJ.

O endpoint **`/webhook/tallospj`** é chamado diretamente pelas automações do RD Conversas quando um novo lead PJ é registrado (formulários, tags PJ, etc.).

---

## 10. Verificar Funcionamento

```bash
# Status do serviço
systemctl status bot-sdr-pj

# Logs em tempo real
journalctl -u bot-sdr-pj -f

# Testar webhook
curl -X POST http://localhost:8001/webhook/tallospj/status
```

Acesse o painel: **http://seu-servidor:8001/admin**

---

## Comandos Úteis

```bash
# Reiniciar bot
systemctl restart bot-sdr-pj

# Ver logs
tail -f /var/log/bot-sdr-pj/error.log

# Atualizar código e reiniciar
cd /opt/bot-sdr-pj && git pull && systemctl restart bot-sdr-pj
```
