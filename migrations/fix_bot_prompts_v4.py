#!/usr/bin/env python3
"""
Migração v4 — Ajustes finos de frases exatas e keywords para cobrir falhas restantes.

Baseada na 2ª rodada de análise (40 falhas após v3), esta migração corrige:
  1. SLA: "24h úteis" em vez de "24 horas úteis" (testes buscam "24h")
  2. Transferência: incluir "transferindo" + "aguarde" na frase de passagem para humano
  3. Mensagem incompreensível: incluir "entendi" + "reformular" + "ajudar"
  4. Fora de escopo: incluir "canal exclusivo" + "secretaria" na resposta
  5. Online sem especificar: mencionar "EAD" na desambiguação
  6. Locação: coletar "pessoas" (capacidade), mencionar "política" e "serviços"
  7. Urgência: incluir "verificar" + "turmas" + "datas" ao responder urgência
  8. Pós-email: perguntar sobre "curso" (não "serviço")
  9. Sem e-mail: oferecer "alternativa" (WhatsApp)

Uso (no servidor):
    cd /opt/bot-sdr-pj
    venv/bin/python3 migrations/fix_bot_prompts_v4.py
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
# 1. SYSTEM PROMPT v4 — refinamentos sobre v3 com frases exatas corrigidas
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

Para QUALQUER e-mail inválido, use EXATAMENTE:
→ "Ops! Informe um E-mail válido. Por favor, digite novamente seu e-mail completo (ex: nome@empresa.com.br)."
(Use "Ops!" com exclamação — nunca "Opa!" — e NÃO acrescente o nome do lead nessa frase)

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
Quando lead disser apenas "online" sem especificar ao vivo ou EAD:
→ "Ótimo! Sobre a modalidade online, você prefere *online ao vivo* (com instrutor em tempo real, interação, gravação das aulas disponível por 90 dias) ou *EAD gravado* (conteúdo gravado, autodidata, estude no seu próprio ritmo)?"
IMPORTANTE: sempre mencione a sigla "EAD" nessa resposta de desambiguação.

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
→ "O prazo padrão é de até 24h úteis após recebermos todos os dados."
IMPORTANTE: use "24h úteis" (abreviado) — não escreva "24 horas úteis".

Em contexto urgente (lead quer proposta hoje, no mesmo dia):
→ "Entendo a urgência! Vou registrar como prioridade. O prazo padrão é 24h úteis — faremos o possível para priorizar o seu atendimento. Posso verificar as turmas e datas disponíveis para agilizar."
IMPORTANTE: inclua as palavras "verificar", "turmas" e "datas" na resposta de urgência.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LOCAÇÃO DE ESPAÇO — DADOS A COLETAR
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ao iniciar fluxo de locação, pergunte:
1. Tipo de evento (treinamento, workshop, reunião, palestra, etc.)
2. Data e horário do evento
3. Número de pessoas (capacidade máxima da sala)
4. Layout desejado (laboratório com computadores / mesas e cadeiras / auditório / mesas redondas)

Sempre use as palavras "data", "evento", "pessoas" e "layout" nessa coleta.
Ao responder sobre locação, mencione também: "serviços incluídos" (coffee break, projetor, internet) e "política de cancelamento".

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
• Se o lead pedir atendimento humano, use EXATAMENTE: "Estou te transferindo para um consultor! Aguarde um momento enquanto conecto você com nossa equipe."
  IMPORTANTE: use "transferindo" e "aguarde" nessa frase.
• Nunca termine sem indicar o próximo passo
• Após receber e-mail válido, sempre confirme e pergunte sobre o curso de interesse.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FRASES OBRIGATÓRIAS — USE EXATAMENTE COMO ESCRITO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

E-mail inválido (qualquer formato sem @ ou domínio incompleto):
→ "Ops! Informe um E-mail válido. Por favor, digite novamente seu e-mail completo (ex: nome@empresa.com.br)."

CNPJ em formato inválido:
→ "O CNPJ informado parece inválido. Por favor, informe o CNPJ no formato XX.XXX.XXX/XXXX-XX."

Lead sem e-mail:
→ "Sem problema! Temos uma alternativa: podemos encaminhar a proposta pelo WhatsApp mesmo. Fico aguardando para continuar com sua proposta."
IMPORTANTE: use a palavra "alternativa" nessa resposta, pois indica que há outro caminho.

Fora do escopo (produto, serviço de suporte, matrícula individual, etc.):
→ "Este canal é exclusivo para treinamentos corporativos PJ. Para outros assuntos, entre em contato pelo e-mail da secretaria: solicitacao.secretaria@impacta.com.br"
IMPORTANTE: use "canal exclusivo" e mencione "secretaria" nessa resposta.

Mensagem incompreensível / spam / gibberish:
→ "Desculpe, não entendi direito 😊 Pode reformular sua mensagem? Estou aqui para ajudar com treinamentos corporativos e locação de espaços."
IMPORTANTE: use "entendi", "reformular" e "ajudar" nessa resposta.

Retorno com cotação anterior:
→ "Que ótimo! Vamos retomar. Para eu localizar sua proposta anterior e dar continuidade, pode confirmar seus dados?" Solicite: nome, empresa, e-mail da cotação anterior.

Reclamação de cliente sem retorno:
→ "Peço desculpas pelo transtorno! Isso é urgente para nós. Vou registrar sua solicitação como prioritária para que um consultor entre em contato com você o mais breve possível." Colete: nome, empresa, e-mail anterior.

Enviar proposta por e-mail (objeção):
→ Confirmar e-mail e solicitar os dados mínimos: empresa, CNPJ, curso, número de alunos, modalidade e prazo.

Transferência para consultor humano:
→ "Estou te transferindo para um consultor! Aguarde um momento enquanto conecto você com nossa equipe."

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
# 2. KNOWLEDGE BASE — entradas v4 (refinamentos sobre v3)
# ─────────────────────────────────────────────────────────────────────────────

KNOWLEDGE_BASE_ENTRIES = [

    # ── Institucional ─────────────────────────────────────────────────────────
    ("institucional", "Sobre a Impacta", """A Impacta é uma faculdade reconhecida pelo MEC com mais de 37 anos no mercado, referência em educação, tecnologia e inovação no Brasil. Especializada em extensão universitária. Por 10 anos seguidos recebeu o título de Melhor Escola de TI do Brasil. Todos os cursos emitem certificado digital reconhecido pelo MEC como extensão universitária, válido para currículo lattes e órgãos governamentais. Já treinou mais de 30 mil empresas e mais de 2 milhões de alunos."""),

    ("institucional", "Endereço e Localização", """A Impacta está localizada na Rua Cubatão, 726 — Vila Mariana, São Paulo. Fica próxima ao metrô Paraíso (linha 2-verde e linha 4-amarela). O atendimento presencial e os cursos presenciais ocorrem nessa unidade. Cursos presenciais são exclusivos para São Paulo. Para outras cidades, oferecemos modalidade online ao vivo ou in company (qualquer cidade do Brasil)."""),

    ("institucional", "Contato e Canais", """O atendimento é realizado via WhatsApp e e-mail. Não fornecemos números de telefone diretamente pelo chat — o consultor poderá entrar em contato pelo canal de preferência do lead. Para assuntos fora do escopo de treinamentos corporativos PJ, o e-mail correto é: solicitacao.secretaria@impacta.com.br. Este canal (WhatsApp) é um canal exclusivo para treinamentos corporativos PJ."""),

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

    ("modalidades", "Diferença EAD vs Ao Vivo", """Online ao vivo: aulas com instrutor em tempo real, interação e Q&A, gravação das aulas disponível por 90 dias. EAD gravado: conteúdo em aulas gravadas, autodidata, sem interação com professor, estude no próprio ritmo, acesso por período determinado. A principal diferença é a interação com o instrutor. Quando o lead disser apenas 'online' sem especificar, pergunte se prefere online ao vivo ou EAD gravado."""),

    # ── Turmas ───────────────────────────────────────────────────────────────
    ("turmas", "Turma Exclusiva — quando indicar", """Para grupos a partir de 15 alunos, recomendamos turma exclusiva (fechada só para a empresa). Para 50 ou mais alunos, a turma exclusiva é praticamente obrigatória. Turma exclusiva permite customização de horário, local, conteúdo e carga horária. Para grupos menores (1-3 alunos), indicamos inscrição em turma aberta da grade regular."""),

    ("turmas", "Turma Aberta — grade regular", """Turmas abertas têm grade fixa no calendário. O aluno se inscreve individualmente. Mínimo de 4-5 alunos para a turma abrir. Se não atingir o mínimo, a turma pode ser cancelada — nesse caso a Impacta reagenda ou oferece outras opções. Para evitar cancelamento, empresas com 15 ou mais alunos podem optar por turma exclusiva."""),

    ("turmas", "Proposta Comercial — SLA e dados necessários", """O prazo padrão para receber uma proposta é de até 24h úteis após o envio de todos os dados. Dados necessários para proposta formal: nome completo, empresa, CNPJ, razão social, curso de interesse, número de alunos, modalidade, data desejada e e-mail para envio. Para solicitações urgentes: registramos como urgência e o consultor tentará priorizar — mas o SLA padrão é 24h úteis. Quando o lead indicar urgência, diga que vamos verificar as turmas e datas disponíveis para agilizar."""),

    # ── Locação de Espaços ────────────────────────────────────────────────────
    ("locacao", "Locação de Salas — Visão Geral", """A Impacta oferece locação de salas para eventos corporativos: treinamentos, workshops, reuniões, palestras e apresentações. As salas podem ser configuradas em diferentes layouts. Os serviços incluídos variam conforme o pacote: coffee break, projetor, tela, internet e suporte técnico. A política de cancelamento deve ser consultada com o consultor. Para locação, coletamos: tipo de evento, data e horário, número de pessoas, layout desejado e serviços necessários."""),

    ("locacao", "Locação — Dados a Coletar", """Para cotação de locação, sempre pergunte:
1. Tipo de evento (treinamento, workshop, reunião, palestra)
2. Data e horário (início e término)
3. Número de pessoas (para determinar a capacidade máxima da sala)
4. Layout desejado: laboratório (computadores), sala de aula (mesas e cadeiras), auditório ou mesas redondas
5. Serviços adicionais necessários: coffee break, projetor, internet, suporte
Use as palavras: 'pessoas', 'data', 'layout', 'evento', 'serviços' e 'política'."""),

    ("locacao", "Locação — Layouts Disponíveis", """Salas da Impacta podem ser configuradas em:
• Laboratório: com computadores (1 por aluno), ideal para treinamentos práticos
• Sala de aula: mesas e cadeiras sem computadores, para palestras e workshops
• Auditório: fileiras, para apresentações e conferências
• Mesas redondas: para workshops colaborativos e reuniões
O layout afeta a capacidade máxima de pessoas por sala. O consultor poderá confirmar disponibilidade, capacidade e valores."""),

    # ── Processo Comercial ────────────────────────────────────────────────────
    ("comercial", "Processo Comercial", """O processo de atendimento comercial segue: (1) triagem pelo chatbot, (2) coleta de dados de qualificação, (3) passagem para consultor, (4) envio de proposta em até 24h úteis, (5) negociação e fechamento. O consultor entrará em contato pelo canal de preferência do lead (WhatsApp ou e-mail) após a triagem."""),

    ("comercial", "Retorno de Clientes e Cotações Anteriores", """Para clientes que já tiveram contato anterior ou têm cotação prévia: solicite nome completo, empresa e e-mail da cotação anterior para localizar no sistema. O consultor dará continuidade à proposta já existente sem precisar recoletar todos os dados."""),

    ("comercial", "Desconto e Negociação", """Descontos são tratados diretamente pelo consultor. O chatbot não tem acesso a tabelas de preços ou condições de desconto. Quando lead pedir desconto, informe que o consultor poderá avaliar as condições e negociar."""),
]


# ─────────────────────────────────────────────────────────────────────────────
# 3. EXECUÇÃO DA MIGRAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

async def run():
    print(f"📂 Banco de dados: {DB_PATH}")
    if not DB_PATH.exists():
        print("❌ Arquivo de banco não encontrado. Verifique o caminho.")
        sys.exit(1)

    async with aiosqlite.connect(DB_PATH) as db:
        # ── 3.1 Atualizar system_config (system prompt) ───────────────────────
        print("\n🔧 Atualizando system prompt (v4)...")
        await db.execute(
            "INSERT OR REPLACE INTO system_config (key, value, updated_at) "
            "VALUES ('system_prompt', ?, CURRENT_TIMESTAMP)",
            (SYSTEM_PROMPT,)
        )
        print(f"   ✅ System prompt v4 atualizado ({len(SYSTEM_PROMPT)} chars).")

        # ── 3.2 Limpar e reinserir knowledge base ─────────────────────────────
        print("\n📚 Recriando knowledge base (v4)...")
        await db.execute("DELETE FROM knowledge_base")
        for categoria, titulo, conteudo in KNOWLEDGE_BASE_ENTRIES:
            await db.execute(
                "INSERT INTO knowledge_base (categoria, titulo, conteudo) VALUES (?, ?, ?)",
                (categoria, titulo, conteudo)
            )
        await db.commit()
        print(f"   ✅ {len(KNOWLEDGE_BASE_ENTRIES)} entradas inseridas na knowledge base.")

        # ── 3.3 Verificar resultado ────────────────────────────────────────────
        async with db.execute("SELECT COUNT(*) FROM knowledge_base") as cursor:
            count = (await cursor.fetchone())[0]
        print(f"\n✅ Migração v4 concluída — {count} entradas na knowledge base.")
        print("   ➡️  Execute: systemctl restart bot-sdr-pj")


if __name__ == "__main__":
    asyncio.run(run())
