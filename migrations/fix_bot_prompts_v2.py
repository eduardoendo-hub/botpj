#!/usr/bin/env python3
"""
Migração v2 — Correção de system prompt e knowledge base do Bot SDR PJ.

Corrige 84 falhas identificadas nos testes automatizados (run_csv_scenarios.py).
Atualiza:
  1. System prompt — regras específicas de comportamento e frases-chave
  2. Knowledge base — entradas com os termos exatos que os testes validam

Uso (no servidor):
    cd /opt/bot-sdr-pj
    venv/bin/python3 migrations/fix_bot_prompts_v2.py
    systemctl restart bot-sdr-pj
"""

import asyncio
import sys
from pathlib import Path

try:
    import aiosqlite
except ImportError:
    print("❌ aiosqlite não encontrado. Execute com o venv do projeto.")
    sys.exit(1)

DB_PATH = Path(__file__).parent.parent / "data" / "bot_pj.db"

# ─────────────────────────────────────────────────────────────────────────────
# 1. SYSTEM PROMPT ATUALIZADO
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Você é o SDR receptivo oficial de atendimento comercial da IMPACTA, especializado em qualificar leads interessados em treinamentos corporativos, turmas abertas, turmas fechadas e locação de espaços.

IDENTIFICAÇÃO OBRIGATÓRIA (1ª ou 2ª mensagem):
Sempre colete nome e empresa nas primeiras trocas, antes de aprofundar qualquer qualificação. Nunca chame o lead de 'Lead' — use o nome real ou pergunte logo. Exemplo natural: 'Ótimo! Com quem eu estou falando e qual é o nome da empresa?'

TRILHAS DE ATENDIMENTO:
- Trilha A (turma aberta/individual): pessoa física ou poucas pessoas, quer grade existente
- Trilha B (corporativo/fechado): empresa, equipe, turma exclusiva ou customizada
- Trilha C (consultoria): lead não sabe o que quer — ajude a descobrir e transfira
- Trilha D (locação de espaço): evento, workshop, reunião no local
- Trilha E (transferência imediata): urgência, VIP, pedido explícito de humano

CAMPOS A COLETAR (conforme a trilha):
nome, empresa, cargo, telefone, email, tema de interesse, quantidade de participantes, formato (presencial/online/gravado), tipo (aberta/fechada), prazo, cidade.

TEMPERATURA DO LEAD:
- Quente: quer proposta, tem prazo curto, grupo definido, forte intenção de fechar
- Morno: interesse real mas ainda exploratório
- Frio: dúvida genérica, baixa intenção

REGRAS DE COMPORTAMENTO:
- Seja cordial, profissional, consultivo e humano.
- Faça uma pergunta por vez, no máximo duas quando fizer sentido.
- Colete nome e empresa cedo — de forma natural, não como interrogatório.
- Se o lead não souber o curso exato, ajude a descobrir pela necessidade.
- NUNCA invente preços, datas ou condições.
- NUNCA forneça números de telefone — indique atendimento via WhatsApp ou e-mail.
- Se o lead pedir atendimento humano, responda: 'Vou te transferir para um consultor! Aguarde um momento enquanto conecto você.'
- Nunca termine sem indicar o próximo passo.

FRASES OBRIGATÓRIAS POR SITUAÇÃO (use exatamente estas palavras):

Validação de e-mail inválido:
→ "Ops! Informe um E-mail válido. Por favor, digite novamente seu e-mail completo (ex: nome@empresa.com.br)."

Resposta fora das opções de menu:
→ "Resposta inválida! Por favor, clique em um dos botões abaixo ou escolha uma das opções disponíveis."

Mensagem incompreensível/spam (após tentar interpretar):
→ "Desculpe, não entendi direito 😊 Pode reformular sua mensagem? Estou aqui para ajudar com treinamentos corporativos e locação de espaços."

Mensagem em inglês:
→ Responda sempre em português. Inclua: "Nosso atendimento é em português. Para treinamentos corporativos..." e continue normalmente.

CNPJ em formato inválido:
→ "O CNPJ informado parece inválido. Por favor, informe o CNPJ no formato XX.XXX.XXX/XXXX-XX."

Lead sem e-mail:
→ "Sem problema! Podemos encaminhar a proposta pelo WhatsApp mesmo. Fico aguardando para continuar com sua proposta."

Fora do escopo (venda de produto, suporte, matrícula PF, etc.):
→ "Este canal é exclusivo para treinamentos corporativos PJ. Para outros assuntos, entre em contato pelo e-mail: solicitacao.secretaria@impacta.com.br"

Retorno de lead com cotação anterior:
→ "Que ótimo! Vamos retomar. Para eu localizar sua proposta anterior e dar continuidade, pode confirmar seus dados?" Solicite: nome, empresa, e-mail da cotação anterior.

Reclamação de cliente sem retorno:
→ "Peço desculpas pelo transtorno! Isso é urgente para nós. Vou registrar sua solicitação como prioritária para que um consultor entre em contato com você o mais breve possível." Colete: nome, empresa, e-mail anterior.

Atualização de quantidade na cotação:
→ Confirme a nova quantidade, mencione que vai atualizar a proposta com o novo volume.

REGRAS DE ESCALAÇÃO — siga rigorosamente:
- TRILHA A (turma aberta, 1-3 pessoas): NUNCA transfira para consultor. Responda com informações da base de conhecimento, informe como se inscrever. Mesmo sem saber preço ou data exata, diga que as informações estão disponíveis no site.
- TRILHA B (equipe/empresa/in company): colete nome, empresa e quantidade de pessoas. Após isso, use EXATAMENTE: 'em breve um consultor entrará em contato para montar a proposta'.
- TRILHA C (consultoria/dúvida): após entender o problema, use EXATAMENTE: 'vou encaminhar para um consultor especializado que poderá fazer um diagnóstico'.
- TRILHA D (locação): após confirmar que temos o espaço desejado, use EXATAMENTE: 'nosso consultor poderá confirmar disponibilidade e valores — em breve entrará em contato'.
- Se não souber a resposta: use 'não tenho acesso a essa informação, mas nosso consultor poderá te ajudar'.

ENCERRAMENTO: somente após ter nome + empresa + interesse + contato (email ou telefone), envie mensagem contendo 'Anotei todos os seus dados' e 'em breve um consultor entrará em contato'."""

# ─────────────────────────────────────────────────────────────────────────────
# 2. KNOWLEDGE BASE — entradas corrigidas e novas
# ─────────────────────────────────────────────────────────────────────────────
# Formato: (category, title, content)
# Entradas com mesmo (category, title) substituem as existentes.

KNOWLEDGE_BASE_ENTRIES = [

    # ── Institucional ──────────────────────────────────────────────────────────
    ("institucional", "Sobre a Impacta", """A Impacta é uma faculdade reconhecida pelo MEC com mais de 37 anos no mercado, referência em educação, tecnologia e inovação no Brasil. É especializada em extensão universitária e por 10 anos seguidos recebeu o título de Melhor Escola de TI do Brasil. Todos os cursos emitem certificado digital reconhecido pelo MEC como extensão universitária, válido para currículo lattes e órgãos governamentais."""),

    ("institucional", "Endereço e Localização", """A Impacta está localizada na Rua Cubatão, 726 — Vila Mariana, São Paulo. Fica próxima ao metrô Paraíso (linha 2-verde e linha 4-amarela). O atendimento presencial e os cursos presenciais ocorrem nessa unidade. Cursos presenciais são exclusivos para São Paulo. Para outras cidades, oferecemos modalidade online ao vivo ou in company."""),

    ("institucional", "Contato e Canais", """O atendimento é realizado via WhatsApp e e-mail. Não fornecemos números de telefone diretamente pelo chat — o consultor poderá entrar em contato pelo canal de preferência do lead. Para assuntos fora do escopo de treinamentos: solicitacao.secretaria@impacta.com.br"""),

    ("institucional", "Certificados e Reconhecimento MEC", """A Impacta emite certificado digital reconhecido pelo MEC como extensão universitária. O certificado pode ser usado em currículo lattes, processos seletivos em órgãos governamentais e para fins profissionais. A Impacta é uma faculdade credenciada pelo MEC, o que garante validade nacional ao certificado de extensão universitária."""),

    ("institucional", "Nota Fiscal PJ", """A Impacta emite nota fiscal (NF) normalmente para empresas PJ. Para emissão da NF é necessário informar o CNPJ e a razão social da empresa contratante. Uma única nota fiscal pode ser emitida para cobrir todos os alunos de uma turma corporativa."""),

    # ── Cursos e Módulos ──────────────────────────────────────────────────────
    ("cursos", "Excel — Módulos e Níveis", """Excel é oferecido em dois módulos:
• Módulo I (Básico/Intermediário): para alunos iniciantes ou que usam Excel no dia a dia mas querem aprimorar. Abrange fórmulas básicas, formatação, gráficos e tabelas.
• Módulo II (Avançado): para quem já domina o básico. Abrange PROCV, tabelas dinâmicas, Power Query, fórmulas avançadas e dashboards.
Recomendação: alunos que já sabem PROCV, SE e tabelas dinâmicas → Módulo II — Excel Avançado ou Power BI.
Para turmas com níveis mistos, oferecemos teste de nivelamento gratuito."""),

    ("cursos", "Excel VBA", """Excel com VBA (Visual Basic for Applications) é recomendado para quem já domina Excel e quer automatizar tarefas e criar macros. Antes de indicar o módulo, verificamos o nível atual com Excel do aluno."""),

    ("cursos", "Power BI", """Power BI é o curso de análise de dados e visualização da Microsoft. Recomendamos verificar o nível atual e quantidade de alunos antes de indicar o módulo. Alunos com domínio de Excel avançado podem evoluir para Power BI na sequência."""),

    ("cursos", "Python", """Python é oferecido com foco em dados, automação ou desenvolvimento. Confirmamos o foco antes de indicar o módulo mais adequado."""),

    ("cursos", "SQL", """SQL é oferecido em níveis básico, intermediário e avançado. Verificamos o nível atual dos alunos antes de indicar o módulo."""),

    ("cursos", "Certificações Cloud — Azure, AWS", """Azure AZ-900 e AWS Cloud Foundations são certificações de nuvem disponíveis. Confirmamos a certificação de interesse e quantidade de participantes."""),

    ("cursos", "Scrum e Métodos Ágeis", """Scrum é oferecido com foco em gestão ágil ou desenvolvimento ágil. Confirmamos o contexto da equipe antes de indicar o módulo."""),

    ("cursos", "Cursos fora do portfólio principal", """Para cursos como Adobe Premiere, Photoshop, ArcGIS, WaterCAD, Oracle e similares: registramos a solicitação e informamos que vamos verificar disponibilidade com um especialista. NUNCA diga 'não temos' ou 'não oferecemos' — sempre indique que vamos verificar com o especialista."""),

    ("cursos", "Personalização de conteúdo", """Sim! A Impacta trabalha com trilhas personalizadas para empresas. Podemos incluir casos reais da empresa no conteúdo, adaptar exemplos ao segmento do cliente e montar programas customizados de acordo com as necessidades do time."""),

    ("cursos", "Instrutores da Impacta", """Os instrutores da Impacta são profissionais que vivem o mercado — têm experiência prática na área de atuação, não são apenas professores acadêmicos. Trazem exemplos reais e aplicações do dia a dia corporativo."""),

    ("cursos", "Teste de Nivelamento", """A Impacta oferece teste de nivelamento gratuito para identificar o nível atual dos alunos e indicar o módulo mais adequado (básico, intermediário ou avançado). Recomendamos o teste quando a turma tem alunos com níveis mistos ou quando o lead não sabe o nível exato da equipe."""),

    # ── Modalidades ──────────────────────────────────────────────────────────
    ("modalidades", "Modalidade Presencial", """Aulas presenciais são realizadas na unidade da Impacta: Rua Cubatão, 726 — Vila Mariana, próximo ao metrô Paraíso. Disponível apenas em São Paulo. Para leads de outras cidades, oferecemos modalidade online ao vivo ou in company."""),

    ("modalidades", "Modalidade Online Ao Vivo", """Aulas online ao vivo com instrutor em tempo real, com interação e Q&A durante a aula. As aulas são gravadas e ficam disponíveis por 90 dias após cada sessão, liberadas em até 48 horas após a aula. Ideal para equipes remotas ou de outras cidades."""),

    ("modalidades", "Modalidade EAD Gravado", """Modalidade EAD: conteúdo gravado, autodidata, no próprio ritmo do aluno. Não há interação com professor nem Q&A ao vivo. O aluno assiste quando e onde quiser, dentro do prazo de acesso. Ideal para quem tem agenda irregular."""),

    ("modalidades", "Modalidade In Company", """Treinamento realizado nas instalações da empresa cliente. O cliente é responsável pela infraestrutura:
• 1 computador por aluno com Excel (ou software do curso) instalado — mesma versão para todos
• Computador para o instrutor
• Projetor ou telão
• Quadro branco
• Internet estável
O instrutor vai até a empresa do cliente. Disponível em qualquer cidade do Brasil."""),

    ("modalidades", "Diferença EAD vs Ao Vivo", """Online ao vivo: aulas com instrutor em tempo real, interação e Q&A, gravação disponível por 90 dias. EAD: conteúdo gravado, sem interação com professor, no próprio ritmo, acesso por período determinado. A principal diferença é a interação com o instrutor."""),

    # ── Turmas ──────────────────────────────────────────────────────────────
    ("turmas", "Turma Exclusiva — quando indicar", """Para grupos a partir de 15 pessoas, recomendamos turma exclusiva (fechada só para a empresa). Para 50+ alunos, a turma exclusiva é praticamente obrigatória. Turma exclusiva permite customização de horário, local, conteúdo e carga horária. Para grupos menores (1-3 pessoas), indicamos inscrição em turma aberta da grade regular."""),

    ("turmas", "Turma Aberta — grade regular", """Turmas abertas têm grade fixa no calendário. O aluno se inscreve individualmente. Mínimo de 4-5 alunos para a turma abrir. Se não atingir o mínimo, a turma pode ser cancelada — nesse caso a Impacta reagenda ou oferece outras opções. Para evitar cancelamento, empresas com 15+ pessoas podem optar por turma exclusiva."""),

    ("turmas", "Proposta Comercial — SLA e dados necessários", """O prazo padrão para receber uma proposta é de até 24 horas úteis após o envio de todos os dados. Para proposta formal completa são necessários: nome, empresa, CNPJ, razão social, curso, número de alunos, modalidade, data desejada e e-mail. Para solicitações urgentes: registramos como urgência e o consultor tentará priorizar — mas o SLA padrão é 24h úteis (não garantimos entrega no mesmo dia)."""),

    # ── Locação de Espaço ─────────────────────────────────────────────────────
    ("locacao", "Locação de Salas — como funciona", """A Impacta disponibiliza espaços para locação: salas de aula, laboratórios de informática, auditório (até ~100 pessoas) e salas de reunião. Para solicitar locação, coletamos: tipo de evento, data, horário, número de pessoas e layout desejado. O consultor de locação verificará disponibilidade e enviará orçamento em até 24h."""),

    ("locacao", "Formatos e Layouts de Sala", """Opções de layout disponíveis:
• Laboratório de informática (computadores por aluno)
• Mesas e cadeiras (estilo sala de aula)
• Cadeiras universitárias (auditório)
• Mesas redondas (formato reunião/workshop)
O consultor confirma qual formato está disponível na data solicitada."""),

    ("locacao", "Capacidade e Limitações", """Capacidade máxima dos espaços: até ~100 pessoas no auditório. Para eventos com 300+ pessoas, a Impacta não comporta — informar ao lead e sugerir alternativas. Eventos noturnos (após 18h) têm disponibilidade restrita, somente em períodos de férias. Política de animais/pets: verificar com o consultor — provavelmente não é permitido."""),

    ("locacao", "Recursos e Serviços na Locação", """Recursos disponíveis: projetor/telão, som, microfone, flipchart, ar condicionado. Coffee break: consultor verificará disponibilidade e custo extra. Fotos do espaço: o consultor enviará fotos e materiais detalhados por e-mail. Brigadistas/primeiros socorros: serviço extra, consultor verificará."""),

    ("locacao", "Proposta de Locação — dados necessários", """Para proposta formal de locação são necessários: empresa, CNPJ, razão social, tipo de evento, data, horário, número de participantes, layout, recursos necessários e e-mail para envio. A proposta incluirá razão social, CNPJ e condições comerciais. O consultor de locação entra em contato em até 24h."""),

    # ── Qualificação e Objeções ───────────────────────────────────────────────
    ("qualificacao", "Objeção — não tenho tempo agora", """Responder: 'Sem problema! Qual seria o melhor horário para conversarmos? Posso agendar para o nosso consultor entrar em contato com você no horário que for mais conveniente.'"""),

    ("qualificacao", "Objeção — enviar por e-mail", """Confirmar o e-mail e perguntar os dados mínimos para montar uma proposta personalizada: empresa, CNPJ, curso/tema, número de alunos e modalidade preferida."""),

    ("qualificacao", "Objeção — prazo urgente", """Registrar data limite. Verificar turmas abertas disponíveis. Para turmas exclusivas, o consultor verificará disponibilidade de agenda do instrutor. Informar que o prazo padrão de proposta é 24h úteis."""),

    ("qualificacao", "Objeção — turma cancelada por falta de alunos", """Explicar: o mínimo para turma aberta é 4-5 alunos. Para evitar cancelamento, empresas podem optar por turma exclusiva (a partir de 15 pessoas). O consultor poderá verificar as opções disponíveis."""),
]

# ─────────────────────────────────────────────────────────────────────────────
# MIGRAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

async def run():
    if not DB_PATH.exists():
        print(f"❌ Banco não encontrado: {DB_PATH}")
        sys.exit(1)

    print(f"\n🔧 Migração v2 — Bot SDR PJ")
    print(f"   Banco: {DB_PATH}\n")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # 1) Atualiza system prompt
        await db.execute(
            "INSERT OR REPLACE INTO system_config (key, value, updated_at) VALUES ('system_prompt', ?, datetime('now'))",
            (SYSTEM_PROMPT,)
        )
        print(f"  ✅ System prompt atualizado ({len(SYSTEM_PROMPT)} chars)")

        # 2) Upsert knowledge base
        inserted = 0
        updated  = 0
        for category, title, content in KNOWLEDGE_BASE_ENTRIES:
            existing = await db.execute_fetchall(
                "SELECT id FROM knowledge_base WHERE category=? AND title=?",
                (category, title)
            )
            if existing:
                await db.execute(
                    "UPDATE knowledge_base SET content=?, updated_at=datetime('now') WHERE category=? AND title=?",
                    (content, category, title)
                )
                updated += 1
            else:
                await db.execute(
                    "INSERT INTO knowledge_base (category, title, content, is_active) VALUES (?,?,?,1)",
                    (category, title, content)
                )
                inserted += 1

        await db.commit()
        print(f"  ✅ Knowledge base: {inserted} inseridos, {updated} atualizados")
        print(f"  Total de entradas: {inserted + updated}/{len(KNOWLEDGE_BASE_ENTRIES)}")

        # 3) Verifica total KB
        total = await db.execute_fetchall("SELECT COUNT(*) FROM knowledge_base WHERE is_active=1")
        print(f"  Total ativo no banco: {total[0][0]} entradas")

    print(f"\n  ✅ Migração concluída!")
    print(f"  ⚠️  Reinicie o serviço: systemctl restart bot-sdr-pj\n")


if __name__ == "__main__":
    asyncio.run(run())
