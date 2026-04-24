#!/usr/bin/env python3
"""
Testes automatizados — cenários do CSV (Bot SDR PJ)
Executa os 91 casos de validação mapeados em testes_automatizados_bot_pj.csv
e gera relatório HTML com resultado de cada asserção.

Uso (sempre com o venv do projeto):
    cd /opt/bot-sdr-pj
    venv/bin/python3 tests/run_csv_scenarios.py
    venv/bin/python3 tests/run_csv_scenarios.py --base-url http://localhost:8001
    venv/bin/python3 tests/run_csv_scenarios.py --id T01-04        # só esse cenário
    venv/bin/python3 tests/run_csv_scenarios.py --bloco "Fluxo Inicial"
    venv/bin/python3 tests/run_csv_scenarios.py --tipo validacao

Phones de teste usados: 5511920000001 a 5511920000099
Para apagar esses dados depois: cleanup_test_data.py
"""

import asyncio
import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    import httpx
except ImportError:
    print("❌ httpx não encontrado. Execute com o venv do projeto:")
    print("   venv/bin/python3 tests/run_csv_scenarios.py")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# TELEFONES DE TESTE — prefixo único para facilitar o cleanup posterior
# ─────────────────────────────────────────────────────────────────────────────
PHONE_PREFIX = "551192000"  # cleanup: WHERE phone_number LIKE '551192%'
LEAD_NAME    = "Teste CSV Bot PJ"

# ─────────────────────────────────────────────────────────────────────────────
# SEQUÊNCIAS DE SETUP — mensagens para levar o bot ao estado correto
# Cada valor é uma lista de mensagens de "aquecimento" enviadas antes do
# input real do teste.  O bot pode não responder exatamente como esperado
# a cada step, mas a sequência coloca-o no estado mais próximo possível.
# ─────────────────────────────────────────────────────────────────────────────
STATE_SETUPS: dict[str, list[str]] = {
    "inicio":                       [],
    "aguardando_nome":              ["Oi"],
    "aguardando_email":             ["Oi", "João Silva"],
    "aguardando_empresa_ou_menu":   ["Oi", "João Silva", "joao.teste@empresa.com"],
    "menu_pf_pj":                   ["Oi", "João Silva", "joao.teste@empresa.com"],
    "menu_principal":               ["Oi", "João Silva", "joao.teste@empresa.com"],
    "fluxo_pj":                     ["Oi", "João Silva", "joao.teste@empresa.com", "Para minha Empresa"],
    "fluxo_pj_ou_pergunta":         ["Oi", "João Silva", "joao.teste@empresa.com", "Para minha Empresa"],
    "fluxo_pf":                     ["Oi", "João Silva", "joao.teste@empresa.com", "Para mim"],
    "aguardando_curso":             ["Oi", "João Silva", "joao.teste@empresa.com", "Para minha Empresa"],
    "aguardando_n_alunos":          ["Oi", "João Silva", "joao.teste@empresa.com", "Para minha Empresa", "Excel avançado"],
    "aguardando_nivel":             ["Oi", "João Silva", "joao.teste@empresa.com", "Para minha Empresa", "Excel avançado", "5 pessoas"],
    "aguardando_modalidade":        ["Oi", "João Silva", "joao.teste@empresa.com", "Para minha Empresa", "Excel avançado", "5 pessoas", "Já sabem básico"],
    "aguardando_data":              ["Oi", "João Silva", "joao.teste@empresa.com", "Para minha Empresa", "Excel avançado", "5 pessoas", "Já sabem básico", "Online ao vivo"],
    "aguardando_cnpj":              ["Oi", "João Silva", "joao.teste@empresa.com", "Para minha Empresa", "Empresa Teste LTDA"],
    "fluxo_locacao":                ["Oi", "João Silva", "joao.teste@empresa.com", "Preciso alugar uma sala para evento"],
    "fluxo_locacao_capacidade":     ["Oi", "João Silva", "joao.teste@empresa.com", "Preciso alugar uma sala para evento", "Workshop corporativo"],
    # Estados difíceis de reproduzir programaticamente — roda sem setup específico
    "qualquer":                     ["Oi", "João Silva", "joao.teste@empresa.com"],
    "fora_horario":                 [],
    "pos_atendimento":              ["Oi", "João Silva", "joao.teste@empresa.com"],
    "inativo":                      [],
    "detectou_contato_anterior":    [],
}

# ─────────────────────────────────────────────────────────────────────────────
# CENÁRIOS EMBUTIDOS (espelho do CSV — não precisa do arquivo em disco)
# ─────────────────────────────────────────────────────────────────────────────
CSV_SCENARIOS = [
    # ── Fluxo Inicial ────────────────────────────────────────────────────────
    {"id":"T01-01","bloco":"Fluxo Inicial","tipo":"happy_path","descricao":"Lead inicia com saudação simples","estado":"inicio","input":"Oi","deve_conter":["bem-vindo","IMPACTA","nome"],"nao_deve_conter":["preço","R$"],"proximo_estado":"aguardando_nome"},
    {"id":"T01-02","bloco":"Fluxo Inicial","tipo":"happy_path","descricao":"Lead cumprimenta de forma diferente","estado":"inicio","input":"Bom dia","deve_conter":["bem-vindo","nome"],"nao_deve_conter":[],"proximo_estado":"aguardando_nome"},
    {"id":"T01-03","bloco":"Fluxo Inicial","tipo":"happy_path","descricao":"Lead fornece nome válido","estado":"aguardando_nome","input":"João Silva","deve_conter":["e-mail"],"nao_deve_conter":[],"proximo_estado":"aguardando_email"},
    {"id":"T01-04","bloco":"Fluxo Inicial","tipo":"validacao","descricao":"E-mail inválido sem @","estado":"aguardando_email","input":"joaosilva","deve_conter":["Ops","E-mail válido"],"nao_deve_conter":[],"proximo_estado":"aguardando_email"},
    {"id":"T01-05","bloco":"Fluxo Inicial","tipo":"validacao","descricao":"E-mail inválido com @ incompleto","estado":"aguardando_email","input":"joao@","deve_conter":["Ops","E-mail válido"],"nao_deve_conter":[],"proximo_estado":"aguardando_email"},
    {"id":"T01-06","bloco":"Fluxo Inicial","tipo":"happy_path","descricao":"E-mail válido aceito","estado":"aguardando_email","input":"joao.silva@empresa.com.br","deve_conter":["empresa","curso"],"nao_deve_conter":[],"proximo_estado":"aguardando_empresa_ou_menu"},
    {"id":"T01-07","bloco":"Fluxo Inicial","tipo":"validacao","descricao":"Resposta fora do menu","estado":"menu_principal","input":"quero falar com humano","deve_conter":["inválida","botões","opção"],"nao_deve_conter":[],"proximo_estado":"menu_principal"},
    {"id":"T01-08","bloco":"Fluxo Inicial","tipo":"validacao","descricao":"Número no campo de nome","estado":"aguardando_nome","input":"5511999998888","deve_conter":["nome","completo"],"nao_deve_conter":[],"proximo_estado":"aguardando_nome"},
    # ── Roteamento PF/PJ ─────────────────────────────────────────────────────
    {"id":"T02-01","bloco":"Roteamento PF/PJ","tipo":"happy_path","descricao":"Seleciona curso para empresa","estado":"menu_pf_pj","input":"Para minha Empresa","deve_conter":["empresa","CNPJ"],"nao_deve_conter":[],"proximo_estado":"fluxo_pj"},
    {"id":"T02-02","bloco":"Roteamento PF/PJ","tipo":"happy_path","descricao":"Seleciona curso para si mesmo","estado":"menu_pf_pj","input":"Para mim","deve_conter":["transferindo","consultor"],"nao_deve_conter":[],"proximo_estado":"fluxo_pf"},
    {"id":"T02-03","bloco":"Roteamento PF/PJ","tipo":"edge_case","descricao":"PF mas empresa vai pagar","estado":"fluxo_pf","input":"minha empresa vai pagar pode emitir NF","deve_conter":["empresa","CNPJ","PJ"],"nao_deve_conter":[],"proximo_estado":"fluxo_pj_ou_pergunta"},
    {"id":"T02-04","bloco":"Roteamento PF/PJ","tipo":"edge_case","descricao":"MEI pode contratar como PJ","estado":"fluxo_pj","input":"Sou MEI posso contratar como PJ","deve_conter":["CNPJ","sim","empresa"],"nao_deve_conter":["não pode","inválido"],"proximo_estado":""},
    # ── Curso Solicitado ──────────────────────────────────────────────────────
    {"id":"T03-01","bloco":"Curso Solicitado","tipo":"happy_path","descricao":"Excel avançado","estado":"aguardando_curso","input":"Excel avançado","deve_conter":["Excel","Módulo","alunos"],"nao_deve_conter":[],"proximo_estado":"aguardando_n_alunos"},
    {"id":"T03-02","bloco":"Curso Solicitado","tipo":"happy_path","descricao":"Power BI","estado":"aguardando_curso","input":"Power BI","deve_conter":["Power BI","alunos"],"nao_deve_conter":[],"proximo_estado":"aguardando_n_alunos"},
    {"id":"T03-03","bloco":"Curso Solicitado","tipo":"happy_path","descricao":"Combo Excel + Power BI","estado":"aguardando_curso","input":"Excel e Power BI para minha equipe","deve_conter":["Excel","Power BI","alunos"],"nao_deve_conter":[],"proximo_estado":"aguardando_n_alunos"},
    {"id":"T03-04","bloco":"Curso Solicitado","tipo":"happy_path","descricao":"Python","estado":"aguardando_curso","input":"Python","deve_conter":["Python","alunos"],"nao_deve_conter":[],"proximo_estado":"aguardando_n_alunos"},
    {"id":"T03-05","bloco":"Curso Solicitado","tipo":"happy_path","descricao":"Azure AZ-900","estado":"aguardando_curso","input":"Azure AZ-900","deve_conter":["Azure","alunos"],"nao_deve_conter":[],"proximo_estado":"aguardando_n_alunos"},
    {"id":"T03-06","bloco":"Curso Solicitado","tipo":"happy_path","descricao":"SQL análise de dados","estado":"aguardando_curso","input":"SQL para análise de dados","deve_conter":["SQL","nível","alunos"],"nao_deve_conter":[],"proximo_estado":"aguardando_nivel"},
    {"id":"T03-07","bloco":"Curso Solicitado","tipo":"edge_case","descricao":"Adobe Premiere (fora portfólio principal)","estado":"aguardando_curso","input":"Adobe Premiere","deve_conter":["especialista","verificar","disponibilidade"],"nao_deve_conter":["não temos","não oferecemos"],"proximo_estado":""},
    {"id":"T03-08","bloco":"Curso Solicitado","tipo":"edge_case","descricao":"Lead não sabe qual curso","estado":"aguardando_curso","input":"Preciso de algo na área de dados","deve_conter":["necessidade","equipe","alinhamento"],"nao_deve_conter":[],"proximo_estado":"aguardando_contexto"},
    {"id":"T03-09","bloco":"Curso Solicitado","tipo":"fora_escopo","descricao":"Completamente fora do escopo","estado":"aguardando_curso","input":"Quero comprar um notebook","deve_conter":["treinamentos","canal","exclusivo"],"nao_deve_conter":["preço","produto"],"proximo_estado":""},
    {"id":"T03-10","bloco":"Curso Solicitado","tipo":"happy_path","descricao":"Scrum","estado":"aguardando_curso","input":"Scrum para minha equipe de TI","deve_conter":["Scrum","alunos"],"nao_deve_conter":[],"proximo_estado":"aguardando_n_alunos"},
    {"id":"T03-11","bloco":"Curso Solicitado","tipo":"happy_path","descricao":"Excel VBA","estado":"aguardando_curso","input":"Excel com VBA","deve_conter":["VBA","Excel","alunos"],"nao_deve_conter":[],"proximo_estado":"aguardando_nivel"},
    {"id":"T03-12","bloco":"Curso Solicitado","tipo":"happy_path","descricao":"AWS Cloud Foundations","estado":"aguardando_curso","input":"AWS Cloud Foundations","deve_conter":["AWS","alunos"],"nao_deve_conter":[],"proximo_estado":"aguardando_n_alunos"},
    # ── Número de Alunos ─────────────────────────────────────────────────────
    {"id":"T04-01","bloco":"Número de Alunos","tipo":"happy_path","descricao":"1 aluno","estado":"aguardando_n_alunos","input":"1 pessoa","deve_conter":["modalidade"],"nao_deve_conter":[],"proximo_estado":"aguardando_modalidade"},
    {"id":"T04-02","bloco":"Número de Alunos","tipo":"happy_path","descricao":"2 alunos","estado":"aguardando_n_alunos","input":"2 pessoas","deve_conter":["modalidade"],"nao_deve_conter":[],"proximo_estado":"aguardando_modalidade"},
    {"id":"T04-03","bloco":"Número de Alunos","tipo":"happy_path","descricao":"15 alunos — turma exclusiva","estado":"aguardando_n_alunos","input":"15 pessoas","deve_conter":["turma exclusiva"],"nao_deve_conter":[],"proximo_estado":"aguardando_modalidade"},
    {"id":"T04-04","bloco":"Número de Alunos","tipo":"happy_path","descricao":"50 alunos","estado":"aguardando_n_alunos","input":"50 alunos","deve_conter":["turma exclusiva","modalidade"],"nao_deve_conter":[],"proximo_estado":"aguardando_modalidade"},
    {"id":"T04-05","bloco":"Número de Alunos","tipo":"edge_case","descricao":"Lead não sabe ainda","estado":"aguardando_n_alunos","input":"Talvez 10, talvez 20","deve_conter":["modalidade"],"nao_deve_conter":["inválido","número"],"proximo_estado":""},
    {"id":"T04-06","bloco":"Número de Alunos","tipo":"edge_case","descricao":"Número por extenso","estado":"aguardando_n_alunos","input":"uns quinze","deve_conter":["modalidade"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T04-07","bloco":"Número de Alunos","tipo":"validacao","descricao":"Zero alunos","estado":"aguardando_n_alunos","input":"0","deve_conter":["alunos","número"],"nao_deve_conter":[],"proximo_estado":"aguardando_n_alunos"},
    # ── Nível de Conhecimento ─────────────────────────────────────────────────
    {"id":"T05-01","bloco":"Nível de Conhecimento","tipo":"happy_path","descricao":"Alunos do zero","estado":"aguardando_nivel","input":"Do zero, nunca usaram","deve_conter":["básico","módulo"],"nao_deve_conter":[],"proximo_estado":"aguardando_modalidade"},
    {"id":"T05-02","bloco":"Nível de Conhecimento","tipo":"happy_path","descricao":"Já usam mas querem evoluir","estado":"aguardando_nivel","input":"Já usam mas querem evoluir","deve_conter":["avançado","módulo"],"nao_deve_conter":[],"proximo_estado":"aguardando_modalidade"},
    {"id":"T05-03","bloco":"Nível de Conhecimento","tipo":"edge_case","descricao":"Níveis mistos na equipe","estado":"aguardando_nivel","input":"Alguns sabem básico, outros avançados","deve_conter":["nivelamento","gratuito","teste"],"nao_deve_conter":[],"proximo_estado":"aguardando_modalidade"},
    {"id":"T05-04","bloco":"Nível de Conhecimento","tipo":"edge_case","descricao":"Lead não sabe o nível","estado":"aguardando_nivel","input":"Não sei exatamente","deve_conter":["nivelamento","gratuito"],"nao_deve_conter":[],"proximo_estado":"aguardando_modalidade"},
    {"id":"T05-05","bloco":"Nível de Conhecimento","tipo":"happy_path","descricao":"Sabem PROCV e tabelas dinâmicas","estado":"aguardando_nivel","input":"Já sabem PROCV, SE e tabelas dinâmicas","deve_conter":["avançado","Power BI"],"nao_deve_conter":[],"proximo_estado":"aguardando_modalidade"},
    # ── Modalidade ────────────────────────────────────────────────────────────
    {"id":"T06-01","bloco":"Modalidade","tipo":"happy_path","descricao":"Presencial","estado":"aguardando_modalidade","input":"Presencial","deve_conter":["Vila Mariana","Cubatão","Paraíso"],"nao_deve_conter":[],"proximo_estado":"aguardando_data"},
    {"id":"T06-02","bloco":"Modalidade","tipo":"happy_path","descricao":"Online sem especificar","estado":"aguardando_modalidade","input":"Online","deve_conter":["ao vivo","EAD","instrutor"],"nao_deve_conter":[],"proximo_estado":"aguardando_tipo_online"},
    {"id":"T06-03","bloco":"Modalidade","tipo":"happy_path","descricao":"Ao vivo com instrutor","estado":"aguardando_modalidade","input":"Ao vivo com instrutor","deve_conter":["interação","tempo real","gravação","90 dias"],"nao_deve_conter":[],"proximo_estado":"aguardando_data"},
    {"id":"T06-04","bloco":"Modalidade","tipo":"happy_path","descricao":"EAD gravado","estado":"aguardando_modalidade","input":"EAD","deve_conter":["gravadas","ritmo","autodidata"],"nao_deve_conter":[],"proximo_estado":"aguardando_data"},
    {"id":"T06-05","bloco":"Modalidade","tipo":"happy_path","descricao":"In Company","estado":"aguardando_modalidade","input":"Na nossa empresa","deve_conter":["infraestrutura","computadores","Excel instalado","projetor"],"nao_deve_conter":[],"proximo_estado":"aguardando_endereco"},
    {"id":"T06-06","bloco":"Modalidade","tipo":"duvida_frequente","descricao":"Dúvida sobre gravação das aulas","estado":"qualquer","input":"As aulas ao vivo ficam gravadas","deve_conter":["90 dias","gravadas","48 horas"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T06-07","bloco":"Modalidade","tipo":"edge_case","descricao":"Presencial fora de SP","estado":"aguardando_modalidade","input":"Quero presencial mas estou em Belo Horizonte","deve_conter":["ao vivo","in company","online"],"nao_deve_conter":["presencial em BH"],"proximo_estado":""},
    {"id":"T06-08","bloco":"Modalidade","tipo":"edge_case","descricao":"Só pode sábado","estado":"aguardando_modalidade","input":"Só posso fazer aos sábados","deve_conter":["grade","verificar","sábado"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T06-09","bloco":"Modalidade","tipo":"duvida_frequente","descricao":"Diferença EAD vs ao vivo","estado":"qualquer","input":"Qual a diferença entre EAD e ao vivo","deve_conter":["EAD","ao vivo","instrutor","interação"],"nao_deve_conter":[],"proximo_estado":""},
    # ── Aluguel de Sala ────────────────────────────────────────────────────────
    {"id":"T07-01","bloco":"Aluguel de Sala","tipo":"happy_path","descricao":"Solicitação de locação","estado":"menu_principal","input":"Preciso alugar uma sala para treinamento","deve_conter":["data","evento","pessoas"],"nao_deve_conter":[],"proximo_estado":"fluxo_locacao"},
    {"id":"T07-02","bloco":"Aluguel de Sala","tipo":"happy_path","descricao":"Capacidade dentro do limite","estado":"fluxo_locacao_capacidade","input":"Para 50 pessoas","deve_conter":["layout","formato","data"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T07-03","bloco":"Aluguel de Sala","tipo":"limitacao","descricao":"Capacidade acima do limite","estado":"fluxo_locacao_capacidade","input":"Preciso para 300 pessoas","deve_conter":["100","capacidade","máxima"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T07-04","bloco":"Aluguel de Sala","tipo":"happy_path","descricao":"Laboratório com computadores","estado":"fluxo_locacao","input":"Preciso de sala com computadores","deve_conter":["laboratório","computadores","quantos"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T07-05","bloco":"Aluguel de Sala","tipo":"limitacao","descricao":"Evento noturno","estado":"fluxo_locacao","input":"Evento à noite a partir das 19h","deve_conter":["horário","disponibilidade","férias"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T07-06","bloco":"Aluguel de Sala","tipo":"edge_case","descricao":"Evento com pets","estado":"fluxo_locacao","input":"Será um evento pet, vamos levar cachorros","deve_conter":["verificar","política","permitido"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T07-07","bloco":"Aluguel de Sala","tipo":"happy_path","descricao":"Coffee break","estado":"fluxo_locacao","input":"Precisa ter coffee break incluso","deve_conter":["consultor","verificar","serviços"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T07-08","bloco":"Aluguel de Sala","tipo":"happy_path","descricao":"Fotos do espaço","estado":"fluxo_locacao","input":"Podem me enviar fotos da sala","deve_conter":["consultor","enviar","fotos"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T07-09","bloco":"Aluguel de Sala","tipo":"happy_path","descricao":"RFQ formal com dados CNPJ","estado":"fluxo_locacao","input":"O orçamento deve conter Razão Social, CNPJ, condições","deve_conter":["proposta","consultor","e-mail"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T07-10","bloco":"Aluguel de Sala","tipo":"duvida_frequente","descricao":"Formatos de sala disponíveis","estado":"fluxo_locacao","input":"Qual o formato de sala disponível","deve_conter":["laboratório","mesas","cadeiras universitárias","mesas redondas"],"nao_deve_conter":[],"proximo_estado":""},
    # ── Objeções ──────────────────────────────────────────────────────────────
    {"id":"T08-01","bloco":"Objeções","tipo":"objecao","descricao":"Não tem tempo agora","estado":"qualquer","input":"Não tenho tempo agora","deve_conter":["horário","melhor","conversar"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T08-02","bloco":"Objeções","tipo":"objecao","descricao":"Quer receber por e-mail","estado":"qualquer","input":"Pode me mandar tudo por e-mail","deve_conter":["e-mail","dados","proposta"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T08-03","bloco":"Objeções","tipo":"objecao","descricao":"Urgência de prazo","estado":"qualquer","input":"Precisamos iniciar até o final do mês","deve_conter":["verificar","turmas","datas"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T08-04","bloco":"Objeções","tipo":"objecao","descricao":"Turma cancelada por falta de alunos","estado":"qualquer","input":"Já aconteceu de a turma ser cancelada","deve_conter":["mínimo","alunos","turma exclusiva"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T08-05","bloco":"Objeções","tipo":"duvida_frequente","descricao":"Reconhecimento do certificado","estado":"qualquer","input":"O certificado é reconhecido","deve_conter":["MEC","extensão universitária"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T08-06","bloco":"Objeções","tipo":"duvida_frequente","descricao":"Pergunta sobre NF","estado":"qualquer","input":"Podem emitir nota fiscal para minha empresa","deve_conter":["NF","nota fiscal","empresa","CNPJ"],"nao_deve_conter":[],"proximo_estado":""},
    # ── Sobre a Impacta ───────────────────────────────────────────────────────
    {"id":"T09-01","bloco":"Sobre a Impacta","tipo":"institucional","descricao":"Tempo de mercado","estado":"qualquer","input":"Há quanto tempo a Impacta existe","deve_conter":["37 anos","mercado"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T09-02","bloco":"Sobre a Impacta","tipo":"institucional","descricao":"Endereço","estado":"qualquer","input":"Qual o endereço de vocês","deve_conter":["Cubatão","Vila Mariana","Paraíso"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T09-03","bloco":"Sobre a Impacta","tipo":"institucional","descricao":"Telefone","estado":"qualquer","input":"Qual o telefone de vocês","deve_conter":["WhatsApp","e-mail","contato"],"nao_deve_conter":["(11)","0800"],"proximo_estado":""},
    {"id":"T09-04","bloco":"Sobre a Impacta","tipo":"institucional","descricao":"Reconhecimento MEC","estado":"qualquer","input":"A Impacta é reconhecida pelo MEC","deve_conter":["MEC","faculdade","extensão universitária"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T09-05","bloco":"Sobre a Impacta","tipo":"duvida_frequente","descricao":"Teste de nivelamento","estado":"qualquer","input":"Como funciona o teste de nivelamento","deve_conter":["gratuito","nível","módulo"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T09-06","bloco":"Sobre a Impacta","tipo":"duvida_frequente","descricao":"Personalização de conteúdo","estado":"qualquer","input":"Vocês personalizam o conteúdo para a nossa empresa","deve_conter":["personalizadas","trilhas","casos reais"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T09-07","bloco":"Sobre a Impacta","tipo":"duvida_frequente","descricao":"Instrutor profissional","estado":"qualquer","input":"O instrutor tem experiência de mercado ou é só professor","deve_conter":["mercado","prática","instrutor"],"nao_deve_conter":[],"proximo_estado":""},
    # ── Processo Comercial ────────────────────────────────────────────────────
    {"id":"T10-01","bloco":"Processo Comercial","tipo":"processo","descricao":"Prazo da proposta","estado":"qualquer","input":"Quando recebo a proposta","deve_conter":["24h","horas","úteis"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T10-02","bloco":"Processo Comercial","tipo":"edge_case","descricao":"Proposta urgente hoje","estado":"qualquer","input":"Preciso da proposta ainda hoje","deve_conter":["24h","priorizar","urgência"],"nao_deve_conter":["garantido","confirmado hoje"],"proximo_estado":""},
    {"id":"T10-03","bloco":"Processo Comercial","tipo":"processo","descricao":"NF única para todos","estado":"qualquer","input":"Precisamos de uma única nota fiscal para os 15 alunos","deve_conter":["NF","empresa","CNPJ"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T10-04","bloco":"Processo Comercial","tipo":"processo","descricao":"Quais dados para proposta","estado":"qualquer","input":"Quais dados vocês precisam para montar a proposta","deve_conter":["empresa","CNPJ","curso","alunos","modalidade"],"nao_deve_conter":[],"proximo_estado":""},
    # ── Clientes que Retornam ─────────────────────────────────────────────────
    {"id":"T11-01","bloco":"Clientes que Retornam","tipo":"retorno","descricao":"Lead com cotação anterior","estado":"inicio","input":"Já fiz cotação com vocês antes quero retomar","deve_conter":["retomar","proposta","dados"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T11-02","bloco":"Clientes que Retornam","tipo":"reclamacao","descricao":"Cliente sem retorno","estado":"inicio","input":"Somos clientes enviamos e-mail há uma semana sem resposta","deve_conter":["desculpa","urgente","consultor"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T11-03","bloco":"Clientes que Retornam","tipo":"retorno","descricao":"Aumentar quantidade de alunos","estado":"qualquer","input":"Na cotação anterior eram 10, agora são 20","deve_conter":["20","proposta","atualizar"],"nao_deve_conter":[],"proximo_estado":""},
    # ── Casos Especiais ───────────────────────────────────────────────────────
    {"id":"T12-01","bloco":"Casos Especiais","tipo":"fora_escopo","descricao":"Venda de produto","estado":"qualquer","input":"Vendo notebooks seminovos alguém aí compra","deve_conter":["treinamentos","canal","exclusivo","secretaria"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T12-02","bloco":"Casos Especiais","tipo":"fora_horario","descricao":"Fora do horário comercial","estado":"inicio","input":"Boa noite preciso de uma cotação","deve_conter":["disponível","próximo dia útil","retornaremos"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T12-03","bloco":"Casos Especiais","tipo":"retorno","descricao":"Retorna após ausência (responde SIM)","estado":"detectou_contato_anterior","input":"SIM","deve_conter":["atendimento","novo"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T12-04","bloco":"Casos Especiais","tipo":"escalonamento","descricao":"Quer atendimento humano imediato","estado":"qualquer","input":"Quero falar com uma pessoa agora","deve_conter":["transferir","consultor","aguarde"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T12-05","bloco":"Casos Especiais","tipo":"validacao","descricao":"Mensagem ininteligível","estado":"qualquer","input":"asdfghjkl qwerty 123456","deve_conter":["entendi","reformular","ajudar"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T12-06","bloco":"Casos Especiais","tipo":"edge_case","descricao":"Lead em inglês","estado":"qualquer","input":"I need a quote for corporate training","deve_conter":["português","treinamento"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T12-07","bloco":"Casos Especiais","tipo":"validacao","descricao":"CNPJ formato inválido","estado":"aguardando_cnpj","input":"CNPJ: 123","deve_conter":["CNPJ","válido","formato"],"nao_deve_conter":[],"proximo_estado":"aguardando_cnpj"},
    {"id":"T12-08","bloco":"Casos Especiais","tipo":"edge_case","descricao":"Lead sem e-mail","estado":"aguardando_email","input":"Não tenho e-mail","deve_conter":["WhatsApp","proposta","alternativa"],"nao_deve_conter":[],"proximo_estado":""},
    # ── Frases do Sistema ──────────────────────────────────────────────────────
    {"id":"T13-01","bloco":"Frases do Sistema","tipo":"validacao_texto","descricao":"Boas-vindas","estado":"inicio","input":"Olá","deve_conter":["bem-vindo","IMPACTA"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T13-02","bloco":"Frases do Sistema","tipo":"validacao_texto","descricao":"Erro e-mail","estado":"aguardando_email","input":"email_invalido","deve_conter":["Ops","E-mail válido"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T13-03","bloco":"Frases do Sistema","tipo":"validacao_texto","descricao":"Resposta inválida","estado":"menu_principal","input":"resposta_aleatoria_xyz","deve_conter":["Resposta inválida","botões"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T13-04","bloco":"Frases do Sistema","tipo":"validacao_texto","descricao":"Sem atendente disponível","estado":"fora_horario","input":"quero falar com consultor","deve_conter":["não estamos operando","Atendimentos Humanizados"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T13-05","bloco":"Frases do Sistema","tipo":"validacao_texto","descricao":"Pesquisa de satisfação","estado":"pos_atendimento","input":"ok","deve_conter":["satisfação","1 a 10"],"nao_deve_conter":[],"proximo_estado":""},
    {"id":"T13-06","bloco":"Frases do Sistema","tipo":"validacao_texto","descricao":"Reengajamento após inatividade","estado":"inativo","input":"Oi tudo bem","deve_conter":["não conseguimos avançar","disponível"],"nao_deve_conter":[],"proximo_estado":""},
]


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────────────

class CsvBotTester:
    def __init__(self, base_url: str, delay: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.delay    = delay
        self.results: list[dict] = []

    def _phone_for(self, index: int) -> str:
        """Gera um telefone único de teste baseado no índice do cenário."""
        return f"{PHONE_PREFIX}{index:04d}"

    async def _clear(self, phone: str) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                await client.post(f"{self.base_url}/test/clear", params={"phone": phone})
                await client.post(f"{self.base_url}/test/reactivate", params={"phone": phone})
            except Exception as e:
                print(f"         ⚠️  Aviso ao limpar: {e}")

    async def _send(self, phone: str, message: str) -> dict:
        """Envia uma mensagem e retorna a resposta do bot (com retry)."""
        for attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    res = await client.post(
                        f"{self.base_url}/test/send",
                        json={"message": message, "phone": phone, "name": LEAD_NAME},
                    )
                    res.raise_for_status()
                    return res.json()
            except httpx.TimeoutException:
                if attempt < 3:
                    print(f"         ⏳ Timeout (tentativa {attempt}/3)...")
                    await asyncio.sleep(5)
                else:
                    raise
            except httpx.HTTPStatusError as e:
                raise RuntimeError(f"HTTP {e.response.status_code}: {e.response.text[:200]}")

    async def _setup_state(self, phone: str, estado: str) -> None:
        """Envia mensagens de setup para colocar o bot no estado desejado."""
        msgs = STATE_SETUPS.get(estado, STATE_SETUPS["qualquer"])
        if not msgs:
            return
        for msg in msgs:
            try:
                await self._send(phone, msg)
                await asyncio.sleep(self.delay)
            except Exception as e:
                print(f"         ⚠️  Setup falhou em '{msg}': {e}")

    def _check_keywords(self, response: str, deve_conter: list[str]) -> list[str]:
        found = []
        resp_lower = response.lower()
        for kw in deve_conter:
            if kw.lower() in resp_lower:
                found.append(kw)
        return found

    def _check_forbidden(self, response: str, nao_deve_conter: list[str]) -> list[str]:
        found = []
        resp_lower = response.lower()
        for kw in nao_deve_conter:
            if kw.lower() in resp_lower:
                found.append(kw)
        return found

    async def run_scenario(self, scenario: dict, index: int) -> dict:
        sid   = scenario["id"]
        phone = self._phone_for(index)

        print(f"\n{'─'*56}")
        print(f"  [{sid}] {scenario['descricao']}")
        print(f"  Estado: {scenario['estado']} | Tipo: {scenario['tipo']} | Phone: {phone}")
        print(f"{'─'*56}")

        # 1) Limpa estado anterior
        await self._clear(phone)
        await asyncio.sleep(0.5)

        # 2) Setup do estado requerido
        estado = scenario["estado"]
        if estado not in ("inicio", "fora_horario", "inativo", "detectou_contato_anterior", "pos_atendimento"):
            setup_msgs = STATE_SETUPS.get(estado, STATE_SETUPS["qualquer"])
            if setup_msgs:
                print(f"  ↑ Setup: {len(setup_msgs)} mensagem(ns) para atingir estado '{estado}'")
                await self._setup_state(phone, estado)

        # 3) Envia o input real do teste
        test_input = scenario["input"]
        print(f"  ► Input: {test_input}")

        bot_response = ""
        error = None
        try:
            result       = await self._send(phone, test_input)
            bot_response = result.get("response", "")
            print(f"  ◄ Bot:   {bot_response[:160]}{'...' if len(bot_response)>160 else ''}")
        except Exception as e:
            error = str(e)
            print(f"  ❌ ERRO: {e}")

        # 4) Avaliação
        kw_found    = self._check_keywords(bot_response, scenario["deve_conter"])
        kw_missing  = [k for k in scenario["deve_conter"] if k not in kw_found]
        forbidden   = self._check_forbidden(bot_response, scenario["nao_deve_conter"])

        kw_pct = (len(kw_found) / len(scenario["deve_conter"]) * 100) if scenario["deve_conter"] else 100
        ok_kw  = kw_pct == 100
        ok_forb = len(forbidden) == 0
        passed  = ok_kw and ok_forb and not error

        status_label = "✅ OK" if passed else ("⚠️  PARCIAL" if kw_pct >= 50 and ok_forb else "❌ FALHOU")
        print(f"  {status_label}  |  Keywords: {len(kw_found)}/{len(scenario['deve_conter'])}  |  Proibidas encontradas: {len(forbidden)}")
        if kw_missing:
            print(f"  Faltando: {', '.join(kw_missing)}")
        if forbidden:
            print(f"  Proibidas: {', '.join(forbidden)}")

        record = {
            "id":           sid,
            "bloco":        scenario["bloco"],
            "tipo":         scenario["tipo"],
            "descricao":    scenario["descricao"],
            "estado":       scenario["estado"],
            "phone":        phone,
            "input":        test_input,
            "response":     bot_response,
            "deve_conter":  scenario["deve_conter"],
            "kw_found":     kw_found,
            "kw_missing":   kw_missing,
            "kw_pct":       round(kw_pct),
            "forbidden":    forbidden,
            "passed":       passed,
            "status_label": status_label,
            "error":        error,
        }
        self.results.append(record)
        return record

    async def check_connection(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                res  = await client.get(f"{self.base_url}/test/status")
                data = res.json()
                return data.get("ok", False)
        except Exception as e:
            print(f"❌ Não foi possível conectar em {self.base_url}: {e}")
            return False

    async def run_all(self, scenarios: list[dict]) -> list[dict]:
        print(f"\n🤖 Bot SDR PJ — Testes CSV Automatizados")
        print(f"   Base URL:  {self.base_url}")
        print(f"   Cenários:  {len(scenarios)}")
        print(f"   Início:    {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")

        print("   Verificando conexão...")
        if not await self.check_connection():
            print("   ❌ Bot offline — abortando.")
            sys.exit(1)
        print("   ✅ Bot online\n")

        for i, sc in enumerate(scenarios, start=1):
            await self.run_scenario(sc, i)
            await asyncio.sleep(1.0)  # pausa entre cenários

        return self.results


# ─────────────────────────────────────────────────────────────────────────────
# RELATÓRIO HTML
# ─────────────────────────────────────────────────────────────────────────────

def gerar_relatorio_html(results: list[dict], base_url: str) -> str:
    agora   = datetime.now().strftime("%d/%m/%Y às %H:%M")
    total   = len(results)
    passed  = sum(1 for r in results if r["passed"])
    failed  = sum(1 for r in results if not r["passed"])
    pct_ok  = int(passed / total * 100) if total else 0

    by_bloco: dict[str, list] = {}
    for r in results:
        by_bloco.setdefault(r["bloco"], []).append(r)

    bloco_cor = {
        "Fluxo Inicial":         ("#3b82f6", "#dbeafe"),
        "Roteamento PF/PJ":      ("#8b5cf6", "#ede9fe"),
        "Curso Solicitado":      ("#f59e0b", "#fef3c7"),
        "Número de Alunos":      ("#10b981", "#d1fae5"),
        "Nível de Conhecimento": ("#06b6d4", "#cffafe"),
        "Modalidade":            ("#6366f1", "#e0e7ff"),
        "Aluguel de Sala":       ("#84cc16", "#ecfccb"),
        "Objeções":              ("#f43f5e", "#ffe4e6"),
        "Sobre a Impacta":       ("#a855f7", "#f3e8ff"),
        "Processo Comercial":    ("#0ea5e9", "#e0f2fe"),
        "Clientes que Retornam": ("#d97706", "#fef3c7"),
        "Casos Especiais":       ("#6b7280", "#f3f4f6"),
        "Frases do Sistema":     ("#0f766e", "#ccfbf1"),
    }

    def badge(passed):
        if passed:
            return '<span style="background:#dcfce7;color:#16a34a;padding:2px 10px;border-radius:999px;font-size:11px;font-weight:700">✅ OK</span>'
        return '<span style="background:#fee2e2;color:#dc2626;padding:2px 10px;border-radius:999px;font-size:11px;font-weight:700">❌ FALHOU</span>'

    cards = ""
    for bloco, regs in sorted(by_bloco.items()):
        cor, bg = bloco_cor.get(bloco, ("#6b7280", "#f3f4f6"))
        bloco_ok = sum(1 for r in regs if r["passed"])
        cards += f'''
        <div style="margin-bottom:28px">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
            <div style="background:{cor};color:#fff;padding:4px 14px;border-radius:999px;font-weight:700;font-size:12px">{bloco}</div>
            <div style="font-size:13px;color:#64748b">{bloco_ok}/{len(regs)} OK</div>
          </div>
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead>
              <tr style="background:#f8fafc;border-bottom:2px solid #e2e8f0">
                <th style="padding:8px 12px;text-align:left;color:#64748b;font-weight:600">ID</th>
                <th style="padding:8px 12px;text-align:left;color:#64748b;font-weight:600">Descrição</th>
                <th style="padding:8px 12px;text-align:left;color:#64748b;font-weight:600">Tipo</th>
                <th style="padding:8px 12px;text-align:left;color:#64748b;font-weight:600">Estado</th>
                <th style="padding:8px 12px;text-align:left;color:#64748b;font-weight:600">Input</th>
                <th style="padding:8px 12px;text-align:left;color:#64748b;font-weight:600">Keywords</th>
                <th style="padding:8px 12px;text-align:center;color:#64748b;font-weight:600">Resultado</th>
              </tr>
            </thead>
            <tbody>'''
        for i, r in enumerate(regs):
            bg_row = "#fff" if i % 2 == 0 else "#f8fafc"
            kw_html = ""
            if r["kw_found"]:
                kw_html += " ".join(f'<span style="background:#dcfce7;color:#15803d;padding:1px 6px;border-radius:4px;font-size:11px">{k}</span>' for k in r["kw_found"])
            if r["kw_missing"]:
                kw_html += " ".join(f'<span style="background:#fee2e2;color:#dc2626;padding:1px 6px;border-radius:4px;font-size:11px">✗{k}</span>' for k in r["kw_missing"])
            if r["forbidden"]:
                kw_html += " ".join(f'<span style="background:#fef3c7;color:#92400e;padding:1px 6px;border-radius:4px;font-size:11px">⚠{k}</span>' for k in r["forbidden"])
            resp_tooltip = r["response"].replace('"', '&quot;').replace('<', '&lt;')[:300]
            cards += f'''
              <tr style="background:{bg_row};border-bottom:1px solid #f1f5f9" title="Resposta: {resp_tooltip}">
                <td style="padding:8px 12px;font-family:monospace;font-weight:700;color:#4f46e5">{r['id']}</td>
                <td style="padding:8px 12px;color:#1e293b">{r['descricao']}</td>
                <td style="padding:8px 12px;color:#64748b">{r['tipo']}</td>
                <td style="padding:8px 12px;color:#64748b;font-size:11px;font-family:monospace">{r['estado']}</td>
                <td style="padding:8px 12px;color:#374151;font-style:italic;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{r['input']}</td>
                <td style="padding:8px 12px">{kw_html}</td>
                <td style="padding:8px 12px;text-align:center">{badge(r['passed'])}</td>
              </tr>'''
            if r["error"]:
                cards += f'''
              <tr style="background:#fff7ed"><td colspan="7" style="padding:6px 12px;font-size:11px;color:#c2410c">❌ Erro: {r['error']}</td></tr>'''
        cards += "</tbody></table></div>"

    html = f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Relatório Testes CSV — Bot SDR PJ</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f0f4f8;color:#1e293b}}
  .header{{background:linear-gradient(135deg,#1e1b4b,#312e81);color:#fff;padding:28px 40px}}
  .header h1{{font-size:22px;font-weight:800;margin-bottom:4px}}
  .header p{{font-size:13px;color:#a5b4fc}}
  .kpis{{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:14px;padding:20px 40px}}
  .kpi{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:16px;text-align:center}}
  .kpi .val{{font-size:28px;font-weight:800;line-height:1;margin-bottom:2px}}
  .kpi .lbl{{font-size:11px;color:#64748b}}
  .content{{padding:0 40px 40px}}
  @media(max-width:600px){{.kpis,.content{{padding-left:12px;padding-right:12px}}}}
</style>
</head>
<body>
<div class="header">
  <h1>📋 Relatório de Testes CSV — Bot SDR PJ</h1>
  <p>Gerado em {agora} · URL: {base_url} · {total} cenários do CSV</p>
</div>
<div class="kpis">
  <div class="kpi"><div class="val" style="color:#6366f1">{total}</div><div class="lbl">Total</div></div>
  <div class="kpi"><div class="val" style="color:#16a34a">{passed}</div><div class="lbl">✅ Passou</div></div>
  <div class="kpi"><div class="val" style="color:#dc2626">{failed}</div><div class="lbl">❌ Falhou</div></div>
  <div class="kpi"><div class="val" style="color:#0ea5e9">{pct_ok}%</div><div class="lbl">Taxa de sucesso</div></div>
</div>
<div class="content">
  {cards}
</div>
</body>
</html>'''
    return html


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Testes CSV — Bot SDR PJ")
    parser.add_argument("--base-url", default="http://localhost:8001",
                        help="URL base do bot")
    parser.add_argument("--output",   default="tests/relatorio_csv_testes.html",
                        help="Relatório HTML de saída")
    parser.add_argument("--json",     default="tests/relatorio_csv_testes.json",
                        help="Relatório JSON de saída")
    parser.add_argument("--id",       default=None,
                        help="Rodar só este ID (ex: T01-04)")
    parser.add_argument("--bloco",    default=None,
                        help='Filtrar por bloco (ex: "Fluxo Inicial")')
    parser.add_argument("--tipo",     default=None,
                        help="Filtrar por tipo (ex: validacao, happy_path, edge_case)")
    parser.add_argument("--delay",    type=float, default=2.0,
                        help="Segundos de pausa entre mensagens (padrão: 2.0)")
    args = parser.parse_args()

    scenarios = CSV_SCENARIOS[:]

    if args.id:
        scenarios = [s for s in scenarios if s["id"].upper() == args.id.upper()]
        if not scenarios:
            print(f"❌ ID '{args.id}' não encontrado.")
            sys.exit(1)
    if args.bloco:
        scenarios = [s for s in scenarios if args.bloco.lower() in s["bloco"].lower()]
    if args.tipo:
        scenarios = [s for s in scenarios if s["tipo"].lower() == args.tipo.lower()]

    if not scenarios:
        print("❌ Nenhum cenário selecionado com os filtros fornecidos.")
        sys.exit(1)

    tester  = CsvBotTester(base_url=args.base_url, delay=args.delay)
    results = await tester.run_all(scenarios)

    # Salva JSON
    json_path = Path(args.json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n💾 JSON: {json_path}")

    # Salva HTML
    html      = gerar_relatorio_html(results, args.base_url)
    html_path = Path(args.output)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"📊 HTML: {html_path}")

    total  = len(results)
    passed = sum(1 for r in results if r["passed"])
    print(f"\n{'='*50}")
    print(f"  RESULTADO FINAL")
    print(f"  Passou:     {passed}/{total}")
    print(f"  Taxa:       {int(passed/total*100) if total else 0}%")
    print(f"{'='*50}\n")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
