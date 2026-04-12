#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
#  deploy.sh — Bot SDR PJ
#  Execute no servidor como root: bash deploy.sh
# ──────────────────────────────────────────────────────────────────────────────

set -e  # Para na primeira falha

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║        Deploy — Bot SDR PJ (porta 8001)      ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── 1. Criar usuário e diretórios ─────────────────────────────────────────────
echo "→ Criando usuário e diretórios..."
id botsdrpj &>/dev/null || useradd -m -s /bin/bash botsdrpj
mkdir -p /opt/bot-sdr-pj /var/log/bot-sdr-pj /opt/bot-sdr-pj/data
chown -R botsdrpj:botsdrpj /opt/bot-sdr-pj /var/log/bot-sdr-pj

# ── 2. Copiar código ──────────────────────────────────────────────────────────
echo "→ Copiando arquivos do projeto..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

rsync -av --exclude='.env' --exclude='data/' --exclude='venv/' --exclude='__pycache__' \
    "$PROJECT_DIR/" /opt/bot-sdr-pj/

chown -R botsdrpj:botsdrpj /opt/bot-sdr-pj

# ── 3. Ambiente virtual e dependências ───────────────────────────────────────
echo "→ Instalando dependências Python..."
cd /opt/bot-sdr-pj
sudo -u botsdrpj python3 -m venv venv
sudo -u botsdrpj venv/bin/pip install --upgrade pip -q
sudo -u botsdrpj venv/bin/pip install -r requirements.txt -q
sudo -u botsdrpj venv/bin/pip install gunicorn -q

# ── 4. Configurar .env ────────────────────────────────────────────────────────
if [ ! -f /opt/bot-sdr-pj/.env ]; then
    echo ""
    echo "⚠️  Arquivo .env não encontrado!"
    echo "   Criando a partir do .env.example..."
    cp /opt/bot-sdr-pj/.env.example /opt/bot-sdr-pj/.env
    chown botsdrpj:botsdrpj /opt/bot-sdr-pj/.env
    chmod 600 /opt/bot-sdr-pj/.env
    echo ""
    echo "   ⚡ AÇÃO NECESSÁRIA: Edite o arquivo antes de continuar:"
    echo "      nano /opt/bot-sdr-pj/.env"
    echo ""
    read -p "   Pressione ENTER após preencher o .env para continuar..." _dummy
fi

# ── 5. Instalar serviço systemd ───────────────────────────────────────────────
echo "→ Instalando serviço systemd..."
cp /opt/bot-sdr-pj/deploy/bot-sdr-pj.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable bot-sdr-pj

# ── 6. Iniciar serviço ────────────────────────────────────────────────────────
echo "→ Iniciando Bot SDR PJ..."
systemctl restart bot-sdr-pj
sleep 3
systemctl status bot-sdr-pj --no-pager

# ── 7. Configurar Nginx ───────────────────────────────────────────────────────
echo ""
echo "──────────────────────────────────────────────"
echo " Configuração do Nginx"
echo "──────────────────────────────────────────────"

NGINX_CONF=""
for f in /etc/nginx/sites-enabled/botmba /etc/nginx/sites-enabled/default /etc/nginx/sites-available/botmba; do
    if [ -f "$f" ]; then
        NGINX_CONF="$f"
        break
    fi
done

if [ -n "$NGINX_CONF" ]; then
    echo "→ Arquivo nginx encontrado: $NGINX_CONF"

    if grep -q "tallospj" "$NGINX_CONF"; then
        echo "   (rotas PJ já configuradas, pulando)"
    else
        echo "→ Adicionando rotas do Bot SDR PJ ao nginx..."

        # Insere os blocos location antes do "location /" existente
        ADDON=$(cat /opt/bot-sdr-pj/deploy/nginx-addon.conf | grep -v '^#' | grep -v '^$' | head -30)

        # Usa sed para inserir antes do "location /" no arquivo
        sed -i "/location \//i\\
\\
    # == Bot SDR PJ ==\\
    location /pj/ {\\
        proxy_pass http:\/\/127.0.0.1:8001\/;\\
        proxy_http_version 1.1;\\
        proxy_set_header Host \$host;\\
        proxy_set_header X-Real-IP \$remote_addr;\\
        proxy_read_timeout 120s;\\
    }\\
\\
    location /webhook\\/tallospj {\\
        proxy_pass http:\/\/127.0.0.1:8001\\/webhook\\/tallospj;\\
        proxy_http_version 1.1;\\
        proxy_set_header Host \$host;\\
        proxy_set_header X-Real-IP \$remote_addr;\\
        proxy_read_timeout 30s;\\
    }\\
" "$NGINX_CONF"

        nginx -t && systemctl reload nginx
        echo "   ✅ Nginx atualizado com sucesso!"
    fi
else
    echo "⚠️  Arquivo de configuração do nginx não encontrado automaticamente."
    echo "   Adicione manualmente o conteúdo de:"
    echo "   /opt/bot-sdr-pj/deploy/nginx-addon.conf"
    echo "   ao seu arquivo nginx existente, antes do 'location /'."
fi

# ── 8. Verificação final ──────────────────────────────────────────────────────
echo ""
echo "──────────────────────────────────────────────"
echo " Verificação Final"
echo "──────────────────────────────────────────────"
echo ""

sleep 2
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/health 2>/dev/null || echo "000")

if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ Bot SDR PJ está rodando! (HTTP $HTTP_CODE)"
else
    echo "⚠️  Bot respondeu com HTTP $HTTP_CODE — verifique os logs:"
    echo "   journalctl -u bot-sdr-pj -n 30"
fi

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║           Deploy concluído! 🚀               ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo " Painel Admin PJ  →  http://204.168.224.108/pj/admin"
echo " Webhook PJ       →  http://204.168.224.108/webhook/tallospj"
echo " Logs             →  journalctl -u bot-sdr-pj -f"
echo ""
