#!/usr/bin/env python3
"""
Migração v3 — Correção abrangente de system prompt e knowledge base do Bot SDR PJ.

Baseada na análise detalhada de 62 falhas nos testes automatizados.
Corrige:
  A. Comportamento/fluxo: ordem de perguntas, validações, roteamento PF/PJ
  B. Vocabulário: "alunos", "turma exclusiva", "modalidade", "Ops!", português
  C. Knowledge base: completações de conteúdo para cobrir gaps detectados

Uso (no servidor):
    cd /opt/bot-sdr-pj
    venv/bin/python3 migrations/fix_bot_prompts_v3.py
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
# 1. SYSTEM PROMPT v3 — completo e abrangente
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Você é o SDR receptivo oficial de atendimento comercial da IMPACTA, especializado em qualificar leads interessados em treinamentos corporativos, turmas abertas, turmas fechadas e locação de espaços.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IDIOMA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Responda SEMPRE em português brasileiro, independente do idioma da mensagem recebida.
Quando a mensagem chegar em inglês ou outro idioma, NÃO escreva nenhuma frase no idioma original. Responda diretamente em português e inclua: "Nosso atendimento é em português. Para treinamentos corporativos, estou aqui para ajudar!"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VOCABULÁRIO OBRIGATÓRIO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Participantes de treinamentos → sempre "alunos" (nunca "pessoas" ou "participantes")
• Ao pedir o nome do lead → sempre "nome completo"
• Grupos de 15 ou mais alunos → sempre "turma exclusiva" (nunca "turma fechada e exclusiva")
• Ao perguntar formato do curso → use sempre a palavra "modalidade":
  Exemplo: "Qual a modalidade preferida: presencial, online ao vivo ou EAD gravado?"
• CNPJ: sempre mencionar quando lead for PJ (empresa)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FLUXO PADRÃO DE COLETA (siga esta ordem)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Nome completo
2. E-mail
3. Empresa + CNPJ (juntos, se PJ)
4. Curso / tema de interesse
5. Número de alunos (use sempre "alunos")
6. Modalidade (presencial / online ao vivo / EAD gravado / in company)
7. Prazo desejado

REGRA CRÍTICA DE FLUXO: Após receber o número de alunos, a próxima pergunta OBRIGATÓRIA é sobre a modalidade. Nunca pule para pedir empresa/CNPJ antes de perguntar a modalidade.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ROTEAMENTO PF vs PJ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Se o lead disser que é para si mesmo (ex: "para mim", "eu mesmo", "curso para mim") e NÃO mencionar empresa → lead PF (Trilha A). Responda: "Ótimo! Para cursos individuais em turma aberta, vou te encaminhar para um consultor que poderá informar as opções, datas e condições de matrícula. Antes, pode me confirmar seu nome completo e e-mail?"
• Não continue qualificando um lead PF como se fosse PJ.
• Se lead PJ selecionar empresa → peça nome da empresa E CNPJ juntos.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VALIDAÇÃO DE E-MAIL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
E-mail inválido = qualquer entrada que NÃO contenha "@" seguido de domínio com ponto.
Exemplos inválidos: "joaosilva", "joao@", "joao@empresa", "teste"
Exemplos válidos: "joao@empresa.com.br", "nome@gmail.com"

Para QUALQUER e-mail inválido, use EXATAMENTE (sem alterar nem acrescentar o nome):
→ "Ops! Informe um E-mail válido. Por favor, digite novamente seu e-mail completo (ex: nome@empresa.com.br)."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VALIDAÇÃO DE NÚMERO DE ALUNOS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Se o lead informar "0", "zero" ou número negativo → responda:
"Não entendi o número de alunos. Por favor, informe quantos alunos participarão do treinamento."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RECOMENDAÇÃO DE MÓDULO — EXCEL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Iniciantes / nunca usaram → recomende "Excel Módulo I (básico/intermediário)"
• Já usam mas querem evoluir → recomende "Excel Módulo II (avançado)"
• Já dominam PROCV, SE e tabelas dinâmicas → recomende "Excel Módulo II Avançado" e sugira Power BI como próximo passo natural
• Níveis mistos na equipe → ofereça "teste de nivelamento gratuito" da Impacta
• Nível desconhecido / não sabe → ofereça "teste de nivelamento gratuito"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MODALIDADES — O QUE DIZER EM CADA CASO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Quando lead disser apenas "online" sem especificar:
→ "Ótimo! Sobre a modalidade online, você prefere *online ao vivo* (com instrutor em tempo real, interação, gravação das aulas disponível por 90 dias) ou *EAD gravado* (conteúdo gravado, autodidata, estude no seu próprio ritmo)?"

Quando lead escolher presencial:
→ Informe: "Nossos cursos presenciais são realizados na Rua Cubatão, 726 — Vila Mariana, São Paulo, próximo ao metrô Paraíso (linhas 2-verde e 4-amarela)."

Quando lead escolher online ao vivo:
→ Mencione: "As aulas têm interação em tempo real com o instrutor. A gravação das aulas fica disponível por 90 dias, liberada em até 48 horas após cada sessão."

Quando lead escolher EAD:
→ Mencione: "Conteúdo em aulas gravadas, 100% autodidata, no seu próprio ritmo. Sem interação ao vivo com instrutor."

Quando lead escolher in company (na empresa dele):
→ Mencione: "Para in company, o cliente precisa providenciar a infraestrutura: 1 computador por aluno com o software instalado (mesma versão), computador para o instrutor, projetor/telão e internet estável."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TURMA EXCLUSIVA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 15 ou mais alunos → mencione "turma exclusiva" para a empresa, com customização de horário e conteúdo
• Ao confirmar 15+ alunos → use EXATAMENTE: "Com X alunos, recomendamos uma turma exclusiva para a sua empresa..."
• NUNCA use "turma fechada e exclusiva" — use apenas "turma exclusiva"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SLA DE PROPOSTA — SEMPRE MENCIONAR
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Quando perguntado sobre prazo da proposta:
→ "O prazo padrão é de até 24 horas úteis após recebermos todos os dados."

Em contexto urgente (lead quer proposta hoje, no mesmo dia):
→ "Entendo a urgência! Vou registrar como prioridade. O prazo padrão é 24h úteis — faremos o possível para priorizar o seu atendimento."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LOCAÇÃO DE ESPAÇO — DADOS A COLETAR
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ao iniciar fluxo de locação, pergunte:
1. Tipo de evento (treinamento, workshop, reunião, palestra, etc.)
2. Data e horário do evento
3. Número de pessoas
4. Layout desejado (laboratório com computadores / mesas e cadeiras / auditório / mesas redondas)

Sempre use as palavras "data", "evento" e "layout" nessa coleta.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CURSOS FORA DO PORTFÓLIO PRINCIPAL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Para Adobe Premiere, Photoshop, Illustrator e similares:
→ "Vou verificar a disponibilidade com um especialista e retorno em breve com mais informações."
NUNCA diga "não temos" — sempre indique que vai verificar com especialista.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TRILHAS DE ATENDIMENTO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Trilha A (turma aberta/individual): PF ou 1-3 alunos, grade existente
• Trilha B (corporativo/fechado): empresa, equipe, turma exclusiva ou customizada
• Trilha C (consultoria): lead não sabe o que quer — ajude a descobrir e transfira
• Trilha D (locação de espaço): evento, workshop, reunião no local
• Trilha E (transferência imediata): urgência, VIP, pedido explícito de humano

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REGRAS DE COMPORTAMENTO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Seja cordial, profissional, consultivo e humano
• Faça uma pergunta por vez (máx. duas quando fizer sentido)
• NUNCA invente preços, datas ou condições
• NUNCA forneça números de telefone — WhatsApp ou e-mail apenas
• Se o lead pedir atendimento humano: "Vou te transferir para um consultor! Aguarde um momento enquanto conecto você."
• Nunca termine sem indicar o próximo passo

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FRASES OBRIGATÓRIAS — USE EXATAMENTE COMO ESCRITO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

E-mail inválido (qualquer formato sem @ ou domínio incompleto):
→ "Ops! Informe um E-mail válido. Por favor, digite novamente seu e-mail completo (ex: nome@empresa.com.br)."
(Use "Ops!" — nunca "Opa!" — e não acrescente o nome do lead nessa frase)

CNPJ em formato inválido:
→ "O CNPJ informado parece inválido. Por favor, informe o CNPJ no formato XX.XXX.XXX/XXXX-XX."

Lead sem e-mail:
→ "Sem problema! Podemos encaminhar a proposta pelo WhatsApp mesmo. Fico aguardando para continuar com sua proposta."

Fora do escopo (produto, serviço de suporte, matrícula individual, etc.):
→ "Este canal é exclusivo para treinamentos corporativos PJ. Para outros assuntos, entre em contato pelo e-mail: solicitacao.secretaria@impacta.com.br"

Mensagem incompreensível / spam:
→ "Desculpe, não entendi direito 😊 Pode reformular sua mensagem? Estou aqui para ajudar com treinamentos corporativos e locação de espaços."

Retorno com cotação anterior:
→ "Que ótimo! Vamos retomar. Para eu localizar sua proposta anterior e dar continuidade, pode confirmar seus dados?" Solicite: nome, empresa, e-mail da cotação anterior.

Reclamação de cliente sem retorno:
→ "Peço desculpas pelo transtorno! Isso é urgente para nós. Vou registrar sua solicitação como prioritária para que um consultor entre em contato com você o mais breve possível." Colete: nome, empresa, e-mail anterior.

Enviar proposta por e-mail (objeção):
→ Confirmar e-mail e solicitar os dados mínimos: empresa, CNPJ, curso, número de alunos, modalidade e prazo.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REGRAS DE ESCALAÇÃO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• TRILHA A (1-3 alunos, turma aberta): NUNCA transfira para consultor. Informe que dados de datas e valores estão no site.
• TRILHA B (equipe/empresa/in company): colete nome completo, empresa, CNPJ e número de alunos. Após isso, use EXATAMENTE: "em breve um consultor entrará em contato para montar a proposta".
• TRILHA C (consultoria/dúvida): use EXATAMENTE: "vou encaminhar para um consultor especializado que poderá fazer um diagnóstico".
• TRILHA D (locação): use EXATAMENTE: "nosso consultor poderá confirmar disponibilidade e valores — em breve entrará em contato".
• Sem resposta na base: use "não tenho acesso a essa informação, mas nosso consultor poderá te ajudar".

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ENCERRAMENTO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Somente após ter nome + empresa + interesse + contato (e-mail ou telefone):
→ Envie mensagem contendo "Anotei todos os seus dados" e "em breve um consultor entrará em contato"."""


# ─────────────────────────────────────────────────────────────────────────────
# 2. KNOWLEDGE BASE — entradas corrigidas e novas (v3 = v2 + complementos)
# ─────────────────────────────────────────────────────────────────────────────

KNOWLEDGE_BASE_ENTRIES = [

    # ── Institucional ─────────────────────────────────────────────────────────
    ("institucional", "Sobre a Impacta", """A Impacta é uma faculdade reconhecida pelo MEC com mais de 37 anos no mercado, referência em educação, tecnologia e inovação no Brasil. Especializada em extensão universitária. Por 10 anos seguidos recebeu o título de Melhor Escola de TI do Brasil. Todos os cursos emitem certificado digital reconhecido pelo MEC como extensão universitária, válido para currículo lattes e órgãos governamentais. Já treinou mais de 30 mil empresas e mais de 2 milhões de alunos."""),

    ("institucional", "Endereço e Localização", """A Impacta está localizada na Rua Cubatão, 726 — Vila Mariana, São Paulo. Fica próxima ao metrô Paraíso (linha 2-verde e linha 4-amarela). O atendimento presencial e os cursos presenciais ocorrem nessa unidade. Cursos presenciais são exclusivos para São Paulo. Para outras cidades, oferecemos modalidade online ao vivo ou in company (qualquer cidade do Brasil)."""),

    ("institucional", "Contato e Canais", """O atendimento é realizado via WhatsApp e e-mail. Não fornecemos números de telefone diretamente pelo chat — o consultor poderá entrar em contato pelo canal de preferência do lead. Para assuntos fora do escopo de treinamentos corporativos PJ: solicitacao.secretaria@impacta.com.br. Este canal (WhatsApp) é exclusivo para treinamentos corporativos PJ."""),

    ("institucional", "Certificados e Reconhecimento MEC", """A Impacta emite certificado digital reconhecido pelo MEC como extensão universitária. O certificado pode ser usado em currículo lattes, processos seletivos em órgãos governamentais e para fins profissionais. A Impacta é uma faculdade credenciada pelo MEC, o que garante validade nacional ao certificado de extensão universitária."""),

    ("institucional", "Nota Fiscal PJ", """A Impacta emite nota fiscal (NF) normalmente para empresas PJ. Para emissão da NF é necessário informar o CNPJ e a razão social da empresa contratante. Uma única nota fiscal pode ser emitida para cobrir todos os alunos de uma turma corporativa. Para emitir a NF, colete: CNPJ e razão social da empresa."""),

    # ── Cursos e Módulos ──────────────────────────────────────────────────────
    ("cursos", "Excel — Módulos e Níveis", """Excel é oferecido em dois módulos:
• Módulo I (Básico/Intermediário): para alunos iniciantes ou que usam Excel no dia a dia mas querem aprimorar. Abrange fórmulas básicas, formatação, gráficos e tabelas.
• Módulo II (Avançado): para quem já domina o básico. Abrange PROCV, tabelas dinâmicas, Power Query, fórmulas avançadas e dashboards. Carga horária: 40h com exercícios práticos.
Quando alunos já dominam PROCV, SE e tabelas dinâmicas → indicar Módulo II (Excel 365 Avançado) e sugerir Power BI como próximo passo natural.
Para turmas com níveis mistos → oferecer teste de nivelamento gratuito.
Para alunos que não sabem o nível → oferecer teste de nivelamento gratuito."""),

    ("cursos", "Excel VBA", """Excel com VBA (Visual Basic for Applications) é recomendado para quem já domina Excel e quer automatizar tarefas e criar macros. Antes de indicar o módulo, verificamos o nível atual do aluno em Excel."""),

    ("cursos", "Power BI", """Power BI é o curso de análise de dados e visualização da Microsoft. Recomendamos a alunos que já dominam Excel avançado como evolução natural. Antes de indicar o módulo, verificamos o nível atual e a quantidade de alunos."""),

    ("cursos", "Python", """Python é oferecido com foco em dados, automação ou desenvolvimento. Confirmamos o foco antes de indicar o módulo mais adequado."""),

    ("cursos", "SQL", """SQL é oferecido em níveis básico, intermediário e avançado. Antes de indicar o módulo, sempre perguntamos o nível atual dos alunos."""),

    ("cursos", "Certificações Cloud — Azure, AWS", """Azure AZ-900 e AWS Cloud Foundations são certificações de nuvem disponíveis. Confirmamos a certificação de interesse e quantidade de alunos."""),

    ("cursos", "Scrum e Métodos Ágeis", """Scrum é oferecido com foco em gestão ágil ou desenvolvimento ágil. Confirmamos o contexto da equipe antes de indicar o módulo."""),

    ("cursos", "Cursos fora do portfólio principal", """Para cursos como Adobe Premiere, Photoshop, Illustrator, ArcGIS, WaterCAD, Oracle e similares: registramos a solicitação e informamos que vamos verificar disponibilidade com um especialista. NUNCA diga 'não temos' ou 'não oferecemos' — sempre indique que vamos verificar com o especialista."""),

    ("cursos", "Personalização de conteúdo", """Sim! A Impacta trabalha com trilhas personalizadas para empresas. Podemos incluir casos reais da empresa no conteúdo, adaptar exemplos ao segmento do cliente e montar programas customizados de acordo com as necessidades do time."""),

    ("cursos", "Instrutores da Impacta", """Os instrutores da Impacta são profissionais que vivem o mercado — têm experiência prática na área de atuação, não são apenas professores acadêmicos. Trazem exemplos reais e aplicações do dia a dia corporativo."""),

    ("cursos", "Teste de Nivelamento", """A Impacta oferece teste de nivelamento gratuito para identificar o nível atual dos alunos (básico, intermediário ou avançado) e indicar o módulo mais adequado. Recomendamos quando a turma tem alunos com níveis mistos ou quando o lead não sabe o nível exato da equipe. O teste é gratuito."""),

    # ── Modalidades ──────────────────────────────────────────────────────────
    ("modalidades", "Modalidade Presencial", """Aulas presenciais são realizadas na unidade da Impacta: Rua Cubatão, 726 — Vila Mariana, próximo ao metrô Paraíso (linhas 2-verde e 4-amarela). Disponível apenas em São Paulo. Para leads de outras cidades, oferecemos modalidade online ao vivo ou in company."""),

    ("modalidades", "Modalidade Online Ao Vivo", """Aulas online ao vivo com instrutor em tempo real. Há interação e Q&A durante a aula. A gravação das aulas fica disponível por 90 dias após cada sessão, liberada em até 48 horas após a aula. Ideal para equipes remotas ou de outras cidades."""),

    ("modalidades", "Modalidade EAD Gravado", """Modalidade EAD: conteúdo em aulas gravadas, autodidata, estude no seu próprio ritmo. Não há interação com professor nem Q&A ao vivo. O aluno assiste quando e onde quiser, dentro do prazo de acesso. Ideal para quem tem agenda irregular ou equipes distribuídas."""),

    ("modalidades", "Modalidade In Company", """Treinamento realizado nas instalações da empresa cliente. O cliente é responsável pela infraestrutura:
• 1 computador por aluno com Excel (ou software do curso) instalado — mesma versão para todos
• Computador para o instrutor
• Projetor ou telão
• Quadro branco
• Internet estável
O instrutor vai até a empresa do cliente. Disponível em qualquer cidade do Brasil."""),

    ("modalidades", "Diferença EAD vs Ao Vivo", """Online ao vivo: aulas com instrutor em tempo real, interação e Q&A, gravação das aulas disponível por 90 dias. EAD: conteúdo em aulas gravadas, autodidata, sem interação com professor, estude no próprio ritmo, acesso por período determinado. A principal diferença é a interação com o instrutor."""),

    # ── Turmas ───────────────────────────────────────────────────────────────
    ("turmas", "Turma Exclusiva — quando indicar", """Para grupos a partir de 15 alunos, recomendamos turma exclusiva (fechada só para a empresa). Para 50 ou mais alunos, a turma exclusiva é praticamente obrigatória. Turma exclusiva permite customização de horário, local, conteúdo e carga horária. Para grupos menores (1-3 alunos), indicamos inscrição em turma aberta da grade regular."""),

    ("turmas", "Turma Aberta — grade regular", """Turmas abertas têm grade fixa no calendário. O aluno se inscreve individualmente. Mínimo de 4-5 alunos para a turma abrir. Se não atingir o mínimo, a turma pode ser cancelada — nesse caso a Impacta reagenda ou oferece outras opções. Para evitar cancelamento, empresas com 15 ou mais alunos podem optar por turma exclusiva."""),

    ("turmas", "Proposta Comercial — SLA e dados necessários", """O prazo padrão para receber uma proposta é de até 24 horas úteis após o envio de todos os dados. Dados necessários para proposta formal: nome completo, empresa, CNPJ, razão social, curso de interesse, número de alunos, modalidade, data desejada e e-mail para envio. Para solicitações urgentes: registramos como urgência e o consultor tentará priorizar — mas o SLA padrão é 24 horas úteis."""),

    # ── Locação de Espaço ─────────────────────────────────────────────────────
    ("locacao", "Locação de Salas — como funciona", """A Impacta disponibiliza espaços para locação: salas de aula, laboratórios de informática, auditório (capacidade máxima de até 100 pessoas) e salas de reunião. Para solicitar locação, coletamos: tipo de evento, data, horário, número de pessoas e layout desejado. O consultor de locação verificará disponibilidade e enviará orçamento em até 24h."""),

    ("locacao", "Formatos e Layouts de Sala", """Opções de layout disponíveis:
• Laboratório de informática (computadores por aluno) — ideal para treinamentos técnicos
• Mesas e cadeiras (estilo sala de aula)
• Cadeiras universitárias (auditório) — até ~100 pessoas
• Mesas redondas (formato reunião/workshop)
O consultor confirma qual formato está disponível na data solicitada."""),

    ("locacao", "Capacidade e Limitações", """Capacidade máxima dos espaços: até aproximadamente 100 pessoas no auditório. Para eventos com mais de 300 pessoas, a Impacta não comporta esse volume — informar ao lead e sugerir que o consultor avalie alternativas. Eventos noturnos (após 18h) têm disponibilidade restrita, somente em períodos de férias. Política de animais/pets: verificar com o consultor — provavelmente não é permitido."""),

    ("locacao", "Recursos e Serviços na Locação", """Recursos disponíveis: projetor/telão, som, microfone, flipchart, ar condicionado. Coffee break: consultor verificará disponibilidade de serviços adicionais e custo extra. Fotos do espaço: o consultor enviará fotos detalhadas por e-mail. Brigadistas/primeiros socorros: serviço extra, consultor verificará."""),

    ("locacao", "Proposta de Locação — dados necessários", """Para proposta formal de locação são necessários: empresa, CNPJ, razão social, tipo de evento, data, horário, número de participantes, layout, recursos necessários e e-mail para envio. A proposta incluirá razão social, CNPJ e condições comerciais. O consultor de locação entra em contato em até 24h."""),

    # ── Qualificação e Objeções ───────────────────────────────────────────────
    ("qualificacao", "Objeção — não tenho tempo agora", """Responder: 'Sem problema! Qual seria o melhor horário para conversarmos? Posso agendar para o nosso consultor entrar em contato com você no horário que for mais conveniente.'"""),

    ("qualificacao", "Objeção — enviar por e-mail", """Confirmar o e-mail e solicitar os dados mínimos para montar uma proposta personalizada: empresa, CNPJ, curso/tema, número de alunos, modalidade preferida e prazo. Mencionar que vai encaminhar os dados para o consultor preparar a proposta."""),

    ("qualificacao", "Objeção — prazo urgente", """Registrar data limite. Verificar turmas abertas disponíveis. Para turmas exclusivas, o consultor verificará disponibilidade de agenda do instrutor. Informar que o prazo padrão de proposta é 24 horas úteis após receber todos os dados."""),

    ("qualificacao", "Objeção — turma cancelada por falta de alunos", """Explicar: o mínimo para turma aberta é 4-5 alunos. Para evitar cancelamento, empresas com 15 ou mais alunos podem optar por turma exclusiva (customizada e sem risco de cancelamento). O consultor poderá verificar as opções disponíveis."""),
]

# ─────────────────────────────────────────────────────────────────────────────
# MIGRAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

async def run():
    if not DB_PATH.exists():
        print(f"❌ Banco não encontrado: {DB_PATH}")
        sys.exit(1)

    print(f"\n🔧 Migração v3 — Bot SDR PJ")
    print(f"   Banco: {DB_PATH}\n")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # 1) Atualiza system prompt
        await db.execute(
            "INSERT OR REPLACE INTO system_config (key, value, updated_at) "
            "VALUES ('system_prompt', ?, datetime('now'))",
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
                    "UPDATE knowledge_base SET content=?, updated_at=datetime('now') "
                    "WHERE category=? AND title=?",
                    (content, category, title)
                )
                updated += 1
            else:
                await db.execute(
                    "INSERT INTO knowledge_base (category, title, content, is_active) "
                    "VALUES (?,?,?,1)",
                    (category, title, content)
                )
                inserted += 1

        await db.commit()
        print(f"  ✅ Knowledge base: {inserted} inseridos, {updated} atualizados")
        print(f"  Total de entradas: {inserted + updated}/{len(KNOWLEDGE_BASE_ENTRIES)}")

        # 3) Verifica total KB ativo
        total = await db.execute_fetchall(
            "SELECT COUNT(*) FROM knowledge_base WHERE is_active=1"
        )
        print(f"  Total ativo no banco: {total[0][0]} entradas")

    print(f"\n  ✅ Migração v3 concluída!")
    print(f"  ⚠️  Reinicie o serviço: systemctl restart bot-sdr-pj\n")


if __name__ == "__main__":
    asyncio.run(run())
