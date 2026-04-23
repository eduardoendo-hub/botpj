"""Banco de dados SQLite para persistência — Bot SDR PJ."""

import aiosqlite
import os
import json
from datetime import datetime
from typing import Optional, List, Dict, Any

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "bot_pj.db")


async def get_db() -> aiosqlite.Connection:
    """Retorna conexão com o banco de dados."""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def init_db():
    """Inicializa as tabelas do banco de dados."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = await get_db()
    try:
        # Tabela de base de conhecimento
        await db.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Tabela de conversas (histórico)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_number TEXT NOT NULL,
                contact_name TEXT DEFAULT '',
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                channel TEXT DEFAULT 'tallos',
                external_id TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversations_phone
            ON conversations(phone_number, created_at DESC)
        """)

        try:
            await db.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_external_id
                ON conversations(external_id) WHERE external_id != ''
            """)
        except Exception:
            pass

        # Tabela de leads capturados
        await db.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_number TEXT UNIQUE NOT NULL,
                contact_name TEXT DEFAULT '',
                email TEXT DEFAULT '',
                company TEXT DEFAULT '',
                job_title TEXT DEFAULT '',
                training_interest TEXT DEFAULT '',
                tema_interesse TEXT DEFAULT '',
                tipo_interesse TEXT DEFAULT '',
                qtd_participantes TEXT DEFAULT '',
                formato TEXT DEFAULT '',
                cidade TEXT DEFAULT '',
                prazo TEXT DEFAULT '',
                urgencia TEXT DEFAULT '',
                objetivo_negocio TEXT DEFAULT '',
                lead_temperature TEXT DEFAULT '',
                trail TEXT DEFAULT '',
                score TEXT DEFAULT '',
                proximo_passo TEXT DEFAULT '',
                status_conversa TEXT DEFAULT '',
                interest TEXT DEFAULT '',
                stage TEXT DEFAULT 'novo',
                notes TEXT DEFAULT '',
                source_channel TEXT DEFAULT 'tallos_pj',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migração: colunas que podem não existir (inclusive as novas)
        for _col, _type_def in [
            ("company",           "TEXT DEFAULT ''"),
            ("job_title",         "TEXT DEFAULT ''"),
            ("training_interest", "TEXT DEFAULT ''"),
            ("tema_interesse",    "TEXT DEFAULT ''"),
            ("tipo_interesse",    "TEXT DEFAULT ''"),
            ("qtd_participantes", "TEXT DEFAULT ''"),
            ("formato",           "TEXT DEFAULT ''"),
            ("cidade",            "TEXT DEFAULT ''"),
            ("prazo",             "TEXT DEFAULT ''"),
            ("urgencia",          "TEXT DEFAULT ''"),
            ("objetivo_negocio",  "TEXT DEFAULT ''"),
            ("lead_temperature",  "TEXT DEFAULT ''"),
            ("trail",             "TEXT DEFAULT ''"),
            ("score",             "TEXT DEFAULT ''"),
            ("proximo_passo",     "TEXT DEFAULT ''"),
            ("status_conversa",   "TEXT DEFAULT ''"),
            # campos de formulário PJ
            ("identificador",     "TEXT DEFAULT ''"),
            ("qtd_colaboradores", "TEXT DEFAULT ''"),
            ("servico",           "TEXT DEFAULT ''"),
            ("raw_form_data",     "TEXT DEFAULT ''"),
            # integração RD CRM
            ("rd_crm_deal_id",    "TEXT DEFAULT ''"),
            # rastreamento de movimentação no CRM
            ("crm_etapa_cache",   "TEXT DEFAULT ''"),   # última etapa conhecida do CRM
            ("crm_moved_date",    "TEXT DEFAULT ''"),   # data (YYYY-MM-DD BRT) em que etapa mudou
            ("crm_insights",      "TEXT DEFAULT ''"),   # insights extraídos por LLM da conversa do CRM
        ]:
            try:
                await db.execute(f"ALTER TABLE leads ADD COLUMN {_col} {_type_def}")
            except Exception:
                pass

        # Tabela de configurações do sistema de prompt
        await db.execute("""
            CREATE TABLE IF NOT EXISTS system_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Prompt padrão para Bot SDR PJ (v2 — completo com trilhas e qualificação)
        _DEFAULT_PROMPT = (
            "Você é o SDR receptivo oficial de atendimento comercial, especializado em qualificar leads "
            "interessados em treinamentos corporativos, turmas abertas, turmas fechadas e locação de espaços.\n\n"
            "IDENTIFICAÇÃO OBRIGATÓRIA (1ª ou 2ª mensagem):\n"
            "Sempre colete nome e empresa nas primeiras trocas, antes de aprofundar qualquer qualificação. "
            "Nunca chame o lead de 'Lead' — use o nome real ou pergunte logo. "
            "Exemplo natural: 'Ótimo! Com quem eu estou falando e qual é o nome da empresa?'\n\n"
            "TRILHAS DE ATENDIMENTO:\n"
            "- Trilha A (turma aberta/individual): pessoa física ou poucas pessoas, quer grade existente\n"
            "- Trilha B (corporativo/fechado): empresa, equipe, turma exclusiva ou customizada\n"
            "- Trilha C (consultoria): lead não sabe o que quer — ajude a descobrir e transfira\n"
            "- Trilha D (locação de espaço): evento, workshop, reunião no local\n"
            "- Trilha E (transferência imediata): urgência, VIP, pedido explícito de humano\n\n"
            "CAMPOS A COLETAR (conforme a trilha):\n"
            "nome, empresa, cargo, telefone, email, tema de interesse, quantidade de participantes, "
            "formato (presencial/online/gravado), tipo (aberta/fechada), prazo, cidade.\n\n"
            "TEMPERATURA DO LEAD:\n"
            "- Quente: quer proposta, tem prazo curto, grupo definido, forte intenção de fechar\n"
            "- Morno: interesse real mas ainda exploratório\n"
            "- Frio: dúvida genérica, baixa intenção\n\n"
            "REGRAS DE COMPORTAMENTO:\n"
            "- Seja cordial, profissional, consultivo e humano.\n"
            "- Faça uma pergunta por vez, no máximo duas quando fizer sentido.\n"
            "- Colete nome e empresa cedo — de forma natural, não como interrogatório.\n"
            "- Se o lead não souber o curso exato, ajude a descobrir pela necessidade.\n"
            "- Nunca invente preços, datas ou condições.\n"
            "- Se o lead pedir atendimento humano, transfira imediatamente.\n"
            "- Nunca termine sem indicar o próximo passo.\n\n"
            "ENCERRAMENTO: somente após ter nome + empresa + interesse + contato (email ou telefone), "
            "envie mensagem contendo 'Anotei todos os seus dados' e 'em breve um consultor entrará em contato'."
        )
        await db.execute(
            "INSERT OR IGNORE INTO system_config (key, value) VALUES ('system_prompt', ?)",
            (_DEFAULT_PROMPT,)
        )

        # Tabela de configurações do bot
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_config (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                label       TEXT,
                description TEXT,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Valores padrão das configurações do bot
        defaults = [
            ("bot_enabled",                   "true",  "Bot habilitado",                    "Liga/desliga o bot independente de qualquer outra regra"),
            ("bot_schedule_enabled",           "true",  "Respeitar horário de atendimento",  "Se ativado, bot só responde fora do horário dos consultores"),
            ("bot_schedule_weekday_start",     "19",    "Início fora do horário (seg-sex)",  "Hora de início do bot em dias úteis (formato 24h)"),
            ("bot_schedule_weekday_end",       "8",     "Fim fora do horário (seg-sex)",     "Hora de término do bot em dias úteis (formato 24h)"),
            ("bot_schedule_weekend",           "true",  "Ativo no final de semana",          "Bot ativo o final de semana inteiro"),
            ("escalation_message",             "Vou chamar um dos nossos consultores especializados para continuar te ajudando! Em breve alguém da equipe entrará em contato. 😊", "Mensagem de escalonamento", "Mensagem enviada quando o bot encaminha para consultor"),
            # ── Notificações por email ───────────────────────────────────
            ("email_notifications_enabled",   "false", "Notificações por email habilitadas", "Envia email quando o bot qualifica um lead PJ"),
            ("gmail_sender",                  "",      "Conta Gmail remetente",              "Endereço Gmail usado para enviar as notificações"),
            ("gmail_app_password",            "",      "Senha de app do Gmail",              "Senha de aplicativo gerada no Google"),
            ("email_recipients",              "",      "Destinatários das notificações",     "E-mails separados por vírgula ou quebra de linha"),
            # ── Testes ─────────────────────────────────────────────────
            ("test_phone_numbers",            "",      "Números de teste",                   "Números separados por vírgula que SEMPRE recebem resposta do bot (ignora horário)"),
            # ── Encaminhamento para fila ────────────────────────────────
            ("escalation_flow_id",            "",      "ID do fluxo de escalonamento",       "ID do fluxo RD Conversas para onde o lead é encaminhado após qualificação (POST /v2/forward-to-customer)"),
            # ── Relatório diário WhatsApp ────────────────────────────────
            ("chatpro_token",      "f2da0b5d-2650-456f-b77f-49f8d8406be6", "ChatPro token",            "instance-token da instância sparks.chatpro.com.br"),
            ("chatpro_instance_id","chatpro-71f6d6f880",                   "ChatPro instance ID",       "ID da instância ChatPro"),
            ("chatpro_url",        "",                                      "ChatPro URL (legado)",      "Não utilizado — envio usa Sparks diretamente"),
            ("report_recipients",     "",     "Destinatários do relatório",  "Números WhatsApp da diretoria, um por linha (ex: 5511999998888)"),
            ("report_hour",          "18",   "Horário do relatório",         "Hora (BRT) em que o relatório diário é enviado automaticamente"),
            ("report_template_name", "",     "Nome do template WABA",        "Nome exato do template aprovado no Meta Business Manager para o relatório diário"),
            ("report_language_code", "pt_BR","Idioma do template",           "Código de idioma do template (ex: pt_BR, en_US)"),
        ]
        for key, val, label, desc in defaults:
            await db.execute(
                "INSERT OR IGNORE INTO bot_config (key, value, label, description) VALUES (?,?,?,?)",
                (key, val, label, desc)
            )

        # Tabela de sessões do bot por lead
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_sessions (
                phone_number        TEXT PRIMARY KEY,
                last_user_msg_at    TIMESTAMP,
                last_agent_msg_at   TIMESTAMP,
                last_bot_msg_at     TIMESTAMP,
                followup_count      INTEGER DEFAULT 0,
                last_followup_at    TIMESTAMP,
                agent_active        INTEGER DEFAULT 0,
                context_is_waiting  INTEGER DEFAULT 0,
                updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Tabela de logs brutos de webhook
        await db.execute("""
            CREATE TABLE IF NOT EXISTS webhook_logs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type   TEXT NOT NULL,
                instance_id  TEXT DEFAULT '',
                phone_number TEXT DEFAULT '',
                raw_payload  TEXT NOT NULL,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_webhook_logs_phone
            ON webhook_logs(phone_number, created_at DESC)
        """)

        # Sessões do painel admin
        await db.execute("""
            CREATE TABLE IF NOT EXISTS admin_sessions (
                session_id  TEXT PRIMARY KEY,
                username    TEXT NOT NULL,
                expires_at  TIMESTAMP NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Cache persistente de inteligência de empresa (pesquisa web via IA)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS company_intel_cache (
                company_name  TEXT PRIMARY KEY,
                data_json     TEXT NOT NULL,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.commit()
    finally:
        await db.close()


# ==================== Knowledge Base ====================

async def add_knowledge(category: str, title: str, content: str) -> int:
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO knowledge_base (category, title, content) VALUES (?, ?, ?)",
            (category, title, content)
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def update_knowledge(id: int, category: str, title: str, content: str):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE knowledge_base SET category=?, title=?, content=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (category, title, content, id)
        )
        await db.commit()
    finally:
        await db.close()


async def delete_knowledge(id: int):
    db = await get_db()
    try:
        await db.execute("DELETE FROM knowledge_base WHERE id=?", (id,))
        await db.commit()
    finally:
        await db.close()


async def get_all_knowledge(active_only: bool = False) -> List[Dict]:
    db = await get_db()
    try:
        query = "SELECT * FROM knowledge_base"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY category, title"
        cursor = await db.execute(query)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_knowledge_by_id(id: int) -> Optional[Dict]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM knowledge_base WHERE id=?", (id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_knowledge_text() -> str:
    """Retorna toda a base de conhecimento ativa como texto formatado."""
    items = await get_all_knowledge(active_only=True)
    if not items:
        return "Nenhuma informação na base de conhecimento."

    text_parts = []
    current_category = None
    for item in items:
        if item["category"] != current_category:
            current_category = item["category"]
            text_parts.append(f"\n## {current_category}")
        text_parts.append(f"\n### {item['title']}\n{item['content']}")

    return "\n".join(text_parts)


# Palavras de parada em português
_STOPWORDS = {
    "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas",
    "um", "uma", "uns", "umas", "o", "a", "os", "as", "e", "ou",
    "que", "com", "por", "para", "como", "se", "me", "meu", "minha",
    "seu", "sua", "ele", "ela", "eles", "elas", "eu", "você", "nós",
    "isso", "este", "esta", "esse", "essa", "qual", "quais", "tem",
    "ter", "ser", "foi", "são", "está", "estou", "tenho", "quero",
    "pode", "não", "sim", "mais", "muito", "bem", "já", "só", "há",
    "ao", "aos", "às", "pelo", "pela", "pelos", "pelas", "num", "numa",
    "vc", "pra", "pro", "né", "tá", "tô",
}

# Termos de domínio para treinamentos corporativos
_DOMAIN_KEYWORDS = {
    # Modalidades
    "ead", "online", "presencial", "gravado", "live", "ao vivo", "in company",
    # Formatos
    "turma", "fechada", "aberta", "customizado", "customizada", "inhouse",
    # Temas de treinamento
    "ti", "bi", "ia", "ux", "ui", "rh", "rpa", "iot", "sql", "aws",
    "scrum", "agil", "agile", "liderança", "gestao", "gestão", "excel",
    "power", "python", "java", "cloud", "devops", "segurança", "lgpd",
    "pmi", "pmp", "erp", "sap", "crm", "api",
    # Negócio
    "pj", "empresa", "corporativo", "in-company", "formação",
    "plataforma", "locação", "espaço", "sala", "auditório",
    "matricula", "matrícula", "turma", "curso", "treinamento",
    # Siglas comuns
    "cto", "cio", "ceo", "rh",
}

# Categorias de alta prioridade
_PRIORITY_CATEGORIES = {
    "faq", "geral", "apresentação", "sobre", "contato",
    "informações gerais", "diferenciais", "infraestrutura",
    "preços", "precos", "investimento", "valores", "pagamento",
    "premios", "prêmios", "reconhecimentos",
    "empresas treinadas", "cases", "clientes",
    "campanhas", "descontos", "promoções",
    "institucional",
}


async def get_relevant_knowledge_text(query: str = "", max_chars: int = 7000) -> str:
    """
    Retorna seções da base de conhecimento relevantes para a query do lead PJ.
    Mesma estratégia de truncamento inteligente do BOT MBA.
    """
    import re as _re

    items = await get_all_knowledge(active_only=True)
    if not items:
        return "Nenhuma informação na base de conhecimento."

    keywords: List[str] = []
    if query:
        clean_query = _re.sub(r"[^\w\s]", " ", query.lower())
        words = clean_query.split()
        keywords = [
            w for w in words
            if w in _DOMAIN_KEYWORDS
            or (len(w) >= 4 and w not in _STOPWORDS)
        ]

    def _score(item: Dict) -> int:
        if not keywords:
            return 0
        title_hits   = sum(1 for kw in keywords if kw in item["title"].lower())
        content_hits = sum(1 for kw in keywords if kw in item["content"].lower())
        return title_hits * 2 + content_hits

    def _is_priority(item: Dict) -> bool:
        return item["category"].lower() in _PRIORITY_CATEGORIES

    def _truncate(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        cut = text[:limit]
        last_nl = cut.rfind("\n")
        if last_nl > limit * 0.7:
            cut = cut[:last_nl]
        return cut + "\n[...conteúdo completo disponível com o consultor]"

    priority_items  = [i for i in items if _is_priority(i)]
    course_items    = [i for i in items if not _is_priority(i)]

    priority_scored = sorted(priority_items, key=_score, reverse=True)
    course_scored   = sorted(course_items,   key=_score, reverse=True)

    top_priority_score = _score(priority_scored[0]) if priority_scored else 0
    top_course_score   = _score(course_scored[0])   if course_scored   else 0

    course_specific = top_course_score > top_priority_score

    text_parts: List[str] = []
    total = 0

    def _add(item: Dict, char_limit: int):
        nonlocal total
        title   = item["title"]
        content = _truncate(item["content"], char_limit)
        block   = f"\n### {title}\n{content}"
        if total + len(block) > max_chars:
            remaining = max_chars - total - len(f"\n### {title}\n") - 50
            if remaining > 300:
                content = _truncate(item["content"], remaining)
                block   = f"\n### {title}\n{content}"
            else:
                return
        text_parts.append(block)
        total += len(block)

    if course_specific:
        matched_courses   = [i for i in course_scored   if _score(i) > 0]
        matched_priority  = [i for i in priority_scored if _score(i) > 0]
        unmatched_priority = [i for i in priority_scored if _score(i) == 0]

        _add(matched_courses[0], 5000)
        if len(matched_courses) > 1 and total < max_chars * 0.55:
            _add(matched_courses[1], 1500)
        for item in matched_priority:
            if total >= max_chars: break
            _add(item, 1500)
        for item in unmatched_priority:
            if total >= max_chars: break
            _add(item, 1000)
        for item in matched_courses[2:]:
            if total >= max_chars: break
            _add(item, 500)
    else:
        matched_priority   = [i for i in priority_scored if _score(i) > 0]
        unmatched_priority = [i for i in priority_scored if _score(i) == 0]
        matched_courses    = [i for i in course_scored   if _score(i) > 0]

        for item in matched_priority:
            if total >= max_chars: break
            item_limit = min(len(item["content"]) + 10, 3500)
            _add(item, item_limit)
        for item in unmatched_priority:
            if total >= max_chars: break
            _add(item, 1000)
        for item in matched_courses[:2]:
            if total >= max_chars: break
            _add(item, 600)

    if not text_parts:
        for item in priority_scored:
            _add(item, 3500)

    if not text_parts:
        return "Nenhuma informação disponível na base de conhecimento."

    header = (
        f"[{len(text_parts)} seção(ões) carregada(s)"
        + (f" | keywords: {', '.join(keywords)}" if keywords else "")
        + f" | {total:,} chars]\n"
    )
    return header + "\n".join(text_parts)


# ==================== Conversations ====================

async def is_recent_bot_message(phone_number: str, message: str, seconds: int = 30) -> bool:
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT id FROM conversations
               WHERE phone_number = ?
                 AND role = 'assistant'
                 AND message = ?
                 AND created_at >= datetime('now', ? || ' seconds')
               LIMIT 1""",
            (phone_number, message.strip(), f"-{seconds}")
        )
        row = await cursor.fetchone()
        return row is not None
    finally:
        await db.close()


async def save_message_external(
    phone_number: str,
    role: str,
    message: str,
    contact_name: str = "",
    channel: str = "tallos",
    external_id: str = "",
    created_at: str = "",
) -> bool:
    db = await get_db()
    try:
        if external_id:
            cursor = await db.execute(
                "SELECT id FROM conversations WHERE external_id = ?", (external_id,)
            )
            if await cursor.fetchone():
                return False

        if created_at:
            await db.execute(
                """INSERT OR IGNORE INTO conversations
                   (phone_number, contact_name, role, message, channel, external_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (phone_number, contact_name, role, message, channel, external_id, created_at)
            )
        else:
            await db.execute(
                """INSERT OR IGNORE INTO conversations
                   (phone_number, contact_name, role, message, channel, external_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (phone_number, contact_name, role, message, channel, external_id)
            )
        await db.commit()
        return True
    finally:
        await db.close()


async def save_message(phone_number: str, role: str, message: str, contact_name: str = "", channel: str = "tallos"):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO conversations (phone_number, contact_name, role, message, channel) VALUES (?, ?, ?, ?, ?)",
            (phone_number, contact_name, role, message, channel)
        )
        await db.commit()
    finally:
        await db.close()


async def get_conversation_history(phone_number: str, limit: int = 20) -> List[Dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT role, message, created_at, channel, contact_name FROM conversations
               WHERE phone_number = ?
               ORDER BY created_at DESC LIMIT ?""",
            (phone_number, limit)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in reversed(rows)]
    finally:
        await db.close()


async def get_all_conversations_summary() -> List[Dict]:
    db = await get_db()
    try:
        cursor = await db.execute("""
            SELECT phone_number, contact_name,
                   MAX(created_at) as last_message_at,
                   COUNT(*) as total_messages,
                   MAX(channel) as channel
            FROM conversations
            GROUP BY phone_number
            ORDER BY last_message_at DESC
        """)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


# ==================== Leads ====================

async def upsert_lead(phone_number: str, contact_name: str = "", **kwargs):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM leads WHERE phone_number=?", (phone_number,))
        existing = await cursor.fetchone()

        _updatable = (
            "email", "company", "job_title", "training_interest", "tema_interesse",
            "tipo_interesse", "qtd_participantes", "formato", "cidade", "prazo",
            "urgencia", "objetivo_negocio", "lead_temperature", "trail", "score",
            "proximo_passo", "status_conversa", "interest", "stage", "notes",
            "identificador", "qtd_colaboradores", "servico", "raw_form_data",
            "rd_crm_deal_id", "crm_etapa_cache", "crm_moved_date", "crm_insights",
        )

        # Hierarquia de source_channel — nunca regride para valor menos específico
        _channel_priority = {
            "tallos_pj":       4,
            "tallos_form_pj":  3,
            "tallos_chat":     2,
            "tallos_crm_sync": 2,  # importado via sync do RD CRM
            "tallos_monitor":  1,
        }

        if existing:
            updates = []
            values = []
            if contact_name:
                updates.append("contact_name=?")
                values.append(contact_name)
            for key in _updatable:
                if key in kwargs and kwargs[key] not in (None, ""):
                    updates.append(f"{key}=?")
                    values.append(kwargs[key])
            if "source_channel" in kwargs and kwargs["source_channel"]:
                existing_dict = dict(existing)
                current = existing_dict.get("source_channel", "tallos_monitor") or "tallos_monitor"
                incoming = kwargs["source_channel"]
                if _channel_priority.get(incoming, 0) > _channel_priority.get(current, 0):
                    updates.append("source_channel=?")
                    values.append(incoming)
            if updates:
                updates.append("updated_at=CURRENT_TIMESTAMP")
                values.append(phone_number)
                await db.execute(
                    f"UPDATE leads SET {', '.join(updates)} WHERE phone_number=?",
                    values
                )
        else:
            await db.execute(
                """INSERT INTO leads
                   (phone_number, contact_name, email, company, job_title,
                    training_interest, tema_interesse, tipo_interesse, qtd_participantes,
                    formato, cidade, prazo, urgencia, objetivo_negocio,
                    lead_temperature, trail, score, proximo_passo, status_conversa,
                    interest, stage, notes, source_channel,
                    identificador, qtd_colaboradores, servico, raw_form_data,
                    rd_crm_deal_id, crm_etapa_cache, crm_moved_date, crm_insights)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    phone_number, contact_name,
                    kwargs.get("email", ""),             kwargs.get("company", ""),
                    kwargs.get("job_title", ""),          kwargs.get("training_interest", ""),
                    kwargs.get("tema_interesse", ""),     kwargs.get("tipo_interesse", ""),
                    kwargs.get("qtd_participantes", ""),  kwargs.get("formato", ""),
                    kwargs.get("cidade", ""),             kwargs.get("prazo", ""),
                    kwargs.get("urgencia", ""),           kwargs.get("objetivo_negocio", ""),
                    kwargs.get("lead_temperature", ""),   kwargs.get("trail", ""),
                    kwargs.get("score", ""),              kwargs.get("proximo_passo", ""),
                    kwargs.get("status_conversa", ""),    kwargs.get("interest", ""),
                    kwargs.get("stage", "novo"),          kwargs.get("notes", ""),
                    kwargs.get("source_channel", "tallos_pj"),
                    kwargs.get("identificador", ""),      kwargs.get("qtd_colaboradores", ""),
                    kwargs.get("servico", ""),            kwargs.get("raw_form_data", ""),
                    kwargs.get("rd_crm_deal_id", ""),
                    kwargs.get("crm_etapa_cache", ""),   kwargs.get("crm_moved_date", ""),
                    kwargs.get("crm_insights", ""),
                )
            )
        await db.commit()
    finally:
        await db.close()


async def get_lead_by_phone(phone_number: str) -> Optional[Dict]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM leads WHERE phone_number=?", (phone_number,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def is_pj_lead(phone_number: str) -> bool:
    """
    Verifica se o contato é um lead PJ cadastrado via /webhook/tallospj.
    Leads PJ têm source_channel = 'tallos_pj'.
    """
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT source_channel FROM leads WHERE phone_number=?",
            (phone_number,)
        )
        row = await cursor.fetchone()
        if not row:
            return False
        return row["source_channel"] in ("tallos_pj", "tallos_form_pj")
    finally:
        await db.close()


async def get_all_leads() -> List[Dict]:
    """Retorna leads PJ reais (tallos_pj, tallos_form_pj) e leads importados do RD CRM (tallos_crm_sync)."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM leads WHERE source_channel IN ('tallos_pj', 'tallos_form_pj', 'tallos_crm_sync') ORDER BY updated_at DESC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


# ==================== System Config ====================

async def get_system_prompt() -> str:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT value FROM system_config WHERE key='system_prompt'")
        row = await cursor.fetchone()
        return row["value"] if row else ""
    finally:
        await db.close()


async def set_system_prompt(prompt: str):
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO system_config (key, value, updated_at) VALUES ('system_prompt', ?, CURRENT_TIMESTAMP)",
            (prompt,)
        )
        await db.commit()
    finally:
        await db.close()


# ==================== Bot Config ====================

async def get_bot_config() -> Dict[str, str]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT key, value FROM bot_config")
        rows = await cursor.fetchall()
        return {row["key"]: row["value"] for row in rows}
    finally:
        await db.close()


async def get_bot_config_full() -> List[Dict]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM bot_config ORDER BY key")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def set_bot_config(key: str, value: str):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE bot_config SET value=?, updated_at=CURRENT_TIMESTAMP WHERE key=?",
            (value, key)
        )
        await db.commit()
    finally:
        await db.close()


async def set_bot_config_bulk(updates: Dict[str, str]):
    db = await get_db()
    try:
        for key, value in updates.items():
            await db.execute(
                "UPDATE bot_config SET value=?, updated_at=CURRENT_TIMESTAMP WHERE key=?",
                (value, key)
            )
        await db.commit()
    finally:
        await db.close()


# ==================== Bot Sessions ====================

async def get_bot_session(phone_number: str) -> Optional[Dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM bot_sessions WHERE phone_number=?", (phone_number,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def upsert_bot_session(phone_number: str, **kwargs):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT phone_number FROM bot_sessions WHERE phone_number=?", (phone_number,)
        )
        exists = await cursor.fetchone()
        if exists:
            if kwargs:
                sets = ", ".join(f"{k}=?" for k in kwargs)
                sets += ", updated_at=CURRENT_TIMESTAMP"
                await db.execute(
                    f"UPDATE bot_sessions SET {sets} WHERE phone_number=?",
                    list(kwargs.values()) + [phone_number]
                )
        else:
            await db.execute(
                "INSERT INTO bot_sessions (phone_number, updated_at) VALUES (?, CURRENT_TIMESTAMP)",
                (phone_number,)
            )
            if kwargs:
                sets = ", ".join(f"{k}=?" for k in kwargs)
                sets += ", updated_at=CURRENT_TIMESTAMP"
                await db.execute(
                    f"UPDATE bot_sessions SET {sets} WHERE phone_number=?",
                    list(kwargs.values()) + [phone_number]
                )
        await db.commit()
    finally:
        await db.close()


# ==================== Webhook Logs ====================

async def log_webhook_event(event_type: str, raw_payload: dict,
                             instance_id: str = "", phone_number: str = ""):
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO webhook_logs (event_type, instance_id, phone_number, raw_payload)
               VALUES (?, ?, ?, ?)""",
            (event_type, instance_id, phone_number, json.dumps(raw_payload, ensure_ascii=False))
        )
        await db.commit()
    finally:
        await db.close()


async def get_webhook_logs(phone_number: str = "", limit: int = 100) -> List[Dict]:
    db = await get_db()
    try:
        if phone_number:
            cursor = await db.execute(
                "SELECT * FROM webhook_logs WHERE phone_number=? ORDER BY created_at DESC LIMIT ?",
                (phone_number, limit)
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM webhook_logs ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_full_conversation(phone_number: str) -> List[Dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT role, message, created_at FROM conversations WHERE phone_number=? ORDER BY created_at ASC",
            (phone_number,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


# ==================== Company Intel Cache ====================

async def get_company_intel_cached(company_name: str) -> Optional[Dict]:
    """Retorna dados de inteligência da empresa do cache persistente (ou None se não existir)."""
    key = company_name.strip().lower()
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT data_json FROM company_intel_cache WHERE company_name=?",
            (key,)
        )
        row = await cursor.fetchone()
        if row:
            try:
                return json.loads(row["data_json"])
            except Exception:
                return None
        return None
    finally:
        await db.close()


async def set_company_intel_cached(company_name: str, data: Dict) -> None:
    """Salva (ou atualiza) dados de inteligência da empresa no cache persistente."""
    key = company_name.strip().lower()
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO company_intel_cache (company_name, data_json, created_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(company_name) DO UPDATE SET
                 data_json  = excluded.data_json,
                 created_at = CURRENT_TIMESTAMP""",
            (key, json.dumps(data, ensure_ascii=False))
        )
        await db.commit()
    finally:
        await db.close()
