#!/usr/bin/env python3
"""
Script de testes automatizados — Bot SDR PJ
Executa cenários nas 4 trilhas e gera relatório HTML.

Uso no servidor (sempre com o venv do projeto):
    cd /opt/bot-sdr-pj
    venv/bin/python3 tests/run_scenarios.py

Rodar apenas um cenário específico:
    venv/bin/python3 tests/run_scenarios.py --cenario B1

Apontando para outro servidor:
    venv/bin/python3 tests/run_scenarios.py --base-url https://dominio.com/pj
"""

import asyncio
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    import httpx
except ImportError:
    print("❌ httpx não encontrado. Execute com o venv do projeto:")
    print("   venv/bin/python3 tests/run_scenarios.py")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# CENÁRIOS DE TESTE
# ─────────────────────────────────────────────────────────────────────────────

SCENARIOS = [

    # ══════════════════════════════════════════════════════════════════════════
    # TRILHA A — Turma Aberta / Individual
    # ══════════════════════════════════════════════════════════════════════════

    {
        "id": "A1",
        "trilha": "A",
        "nome": "Pessoa física — curso de liderança",
        "descricao": "Lead individual quer fazer um curso de liderança para si mesmo",
        "lead_name": "João Almeida",
        "phone": "5511900000001",
        "turns": [
            "Olá! Quero fazer um curso de liderança.",
            "Sou coordenador em uma empresa de logística, quero me desenvolver pessoalmente.",
            "Quero fazer eu mesmo, não preciso para minha equipe.",
            "Tem cursos presenciais? Moro em São Paulo.",
            "Quais são os valores e datas das próximas turmas?",
        ],
        "esperado": {
            "trilha_detectada": "A",
            "deve_escalar": False,
            "keywords_resposta": ["turma aberta", "liderança", "inscrição", "data", "individual"],
        },
    },

    {
        "id": "A2",
        "trilha": "A",
        "nome": "RH — inscrever 2 pessoas em turma existente",
        "descricao": "RH de empresa pequena quer inscrever dois funcionários em curso da grade",
        "lead_name": "Ana Ferreira",
        "phone": "5511900000002",
        "turns": [
            "Boa tarde! Trabalho no RH de uma empresa e quero inscrever dois funcionários num curso.",
            "Somos uma empresa de contabilidade com 12 funcionários.",
            "Eles precisam de um curso de Excel avançado ou Power BI. Vocês têm isso?",
            "Seriam 2 pessoas, queríamos turma aberta mesmo. Tem opção online?",
            "Como funciona o pagamento para empresa?",
        ],
        "esperado": {
            "trilha_detectada": "A",
            "deve_escalar": False,
            "keywords_resposta": ["turma aberta", "inscrição", "online", "pagamento"],
        },
    },

    {
        "id": "A3",
        "trilha": "A",
        "nome": "Lead pergunta sobre grade de cursos",
        "descricao": "Lead quer conhecer o catálogo completo antes de decidir",
        "lead_name": "Marcos Oliveira",
        "phone": "5511900000003",
        "turns": [
            "Oi, quais cursos vocês oferecem?",
            "Sou gerente de projetos e quero me atualizar. Pode me mandar um catálogo?",
            "Tem algum curso de gestão de projetos ou PMP?",
            "E de comunicação e apresentação?",
            "Qual a duração e o valor médio dos cursos?",
        ],
        "esperado": {
            "trilha_detectada": "A",
            "deve_escalar": False,
            "keywords_resposta": ["catálogo", "curso", "gestão", "duração"],
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # TRILHA B — Corporativo / In Company / Fechado
    # ══════════════════════════════════════════════════════════════════════════

    {
        "id": "B1",
        "trilha": "B",
        "nome": "RH — treinamento exclusivo de liderança para 25 pessoas",
        "descricao": "RH de empresa grande quer turma exclusiva, customizada, in company",
        "lead_name": "Renata Costa",
        "phone": "5511900000004",
        "turns": [
            "Olá! Sou da área de RH da empresa Grupo Alfa e preciso de uma proposta de treinamento.",
            "Precisamos de um programa de liderança para 25 gestores, turma exclusiva para nossa empresa.",
            "Queremos que seja presencial, nas nossas instalações aqui em São Paulo.",
            "O tema principal é liderança situacional e gestão de conflitos.",
            "Qual o prazo para receber uma proposta? Precisamos fechar até o final do mês.",
        ],
        "esperado": {
            "trilha_detectada": "B",
            "deve_escalar": True,
            "keywords_resposta": ["proposta", "in company", "exclusivo", "consultor"],
        },
    },

    {
        "id": "B2",
        "trilha": "B",
        "nome": "Gerente de fábrica — NR segurança in company",
        "descricao": "Lead quer treinamento obrigatório de NR para toda a equipe operacional",
        "lead_name": "Carlos Mendes",
        "phone": "5511900000005",
        "turns": [
            "Boa tarde, preciso de treinamento de NR para minha equipe de produção.",
            "Sou gerente de uma fábrica de autopeças, temos 80 operadores.",
            "Preciso de NR-10 e NR-35, para todos eles. Tem que ser in company aqui na fábrica.",
            "Precisamos emitir certificado para todos. Vocês emitem?",
            "Qual a disponibilidade para o próximo mês? E como funciona o orçamento?",
        ],
        "esperado": {
            "trilha_detectada": "B",
            "deve_escalar": True,
            "keywords_resposta": ["proposta", "in company", "certificado", "consultor"],
        },
    },

    {
        "id": "B3",
        "trilha": "B",
        "nome": "Empresa grande — programa customizado para 50 líderes",
        "descricao": "Diretora de T&D quer montar um programa completo de desenvolvimento",
        "lead_name": "Patricia Lima",
        "phone": "5511900000006",
        "turns": [
            "Olá! Sou diretora de T&D da Construtora Nova Era e estou montando um PDL para 2025.",
            "Preciso de um programa completo de desenvolvimento de líderes, cerca de 50 pessoas.",
            "São gerentes e coordenadores. Queremos módulos de liderança, comunicação e gestão de pessoas.",
            "Tem como customizar o conteúdo para o contexto da construção civil?",
            "Qual seria o processo para desenvolver esse programa conosco?",
        ],
        "esperado": {
            "trilha_detectada": "B",
            "deve_escalar": True,
            "keywords_resposta": ["customizado", "proposta", "programa", "consultor"],
        },
    },

    {
        "id": "B4",
        "trilha": "B",
        "nome": "Startup — team building para 15 pessoas",
        "descricao": "Fundador de startup quer dinâmica de team building para equipe pequena",
        "lead_name": "Rafael Souza",
        "phone": "5511900000007",
        "turns": [
            "Oi! Sou fundador de uma startup de tecnologia e quero fazer um team building pra minha equipe.",
            "Somos 15 pessoas, equipe jovem, muito do time é remoto mas vamos reunir todo mundo.",
            "Quero algo de 1 dia, focado em integração e comunicação. Tem?",
            "Pode ser aqui no nosso escritório ou num espaço que vocês indiquem.",
            "Qual o valor aproximado para esse tipo de ação?",
        ],
        "esperado": {
            "trilha_detectada": "B",
            "deve_escalar": True,
            "keywords_resposta": ["proposta", "team building", "consultor"],
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # TRILHA C — Consultoria / Lead não sabe o que quer
    # ══════════════════════════════════════════════════════════════════════════

    {
        "id": "C1",
        "trilha": "C",
        "nome": "Lead vago — problema na equipe sem saber o quê",
        "descricao": "Lead percebe problema mas não consegue articular a necessidade de treinamento",
        "lead_name": "Fernanda Rocha",
        "phone": "5511900000008",
        "turns": [
            "Olá, vim pelo site. Preciso de ajuda com minha equipe.",
            "A gente tem tido muitos problemas de comunicação interna e as entregas estão atrasando.",
            "Não sei bem o que seria necessário. Talvez um treinamento? Ou uma consultoria?",
            "Somos uma empresa de 45 pessoas, área de marketing digital.",
            "Não sei por onde começar.",
        ],
        "esperado": {
            "trilha_detectada": "C",
            "deve_escalar": True,
            "keywords_resposta": ["consultoria", "diagnóstico", "entender", "necessidade", "consultor"],
        },
    },

    {
        "id": "C2",
        "trilha": "C",
        "nome": "Lead indicado — sem clareza sobre produtos",
        "descricao": "Lead veio por indicação, não conhece os produtos, precisa de orientação",
        "lead_name": "Bruno Tavares",
        "phone": "5511900000009",
        "turns": [
            "Oi! Meu colega me indicou a empresa de vocês, mas não sei bem o que vocês fazem.",
            "Trabalho como gerente de operações num hospital particular, 200 funcionários.",
            "A diretoria pediu para eu buscar algo para desenvolver os supervisores.",
            "Não sei se seria curso, palestra, workshop... Qual a diferença?",
            "Como vocês poderiam me ajudar a entender o que faz mais sentido pra gente?",
        ],
        "esperado": {
            "trilha_detectada": "C",
            "deve_escalar": True,
            "keywords_resposta": ["consultoria", "diagnóstico", "consultor", "entender"],
        },
    },

    {
        "id": "C3",
        "trilha": "C",
        "nome": "Lead com problema de produtividade",
        "descricao": "Empresa com baixa produtividade, líder não sabe se é treinamento ou processo",
        "lead_name": "Luciana Faria",
        "phone": "5511900000010",
        "turns": [
            "Bom dia! Preciso resolver um problema sério de produtividade na minha equipe.",
            "Somos um escritório de advocacia com 30 advogados. Os sócios estão preocupados.",
            "Não sei se é falta de treinamento, gestão ruim ou ferramentas inadequadas.",
            "Os sócios querem uma solução rápida mas também sustentável.",
            "Vocês conseguem fazer um diagnóstico antes de recomendar algo?",
        ],
        "esperado": {
            "trilha_detectada": "C",
            "deve_escalar": True,
            "keywords_resposta": ["diagnóstico", "consultoria", "consultor", "análise"],
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # TRILHA D — Locação de Espaço
    # ══════════════════════════════════════════════════════════════════════════

    {
        "id": "D1",
        "trilha": "D",
        "nome": "Empresa quer sala para treinamento próprio",
        "descricao": "RH quer alugar sala para aplicar treinamento que já tem internamente",
        "lead_name": "Silvia Nunes",
        "phone": "5511900000011",
        "turns": [
            "Olá! Gostaria de informações sobre locação de salas.",
            "Precisamos de uma sala para um treinamento que vamos ministrar internamente.",
            "Seriam 20 pessoas, 1 dia inteiro, com coffee break.",
            "Precisamos de projetor, flipchart e ar condicionado.",
            "Qual a disponibilidade para a segunda quinzena de maio e quais os valores?",
        ],
        "esperado": {
            "trilha_detectada": "D",
            "deve_escalar": True,
            "keywords_resposta": ["locação", "sala", "espaço", "diária", "consultor"],
        },
    },

    {
        "id": "D2",
        "trilha": "D",
        "nome": "ONG quer auditório para congresso",
        "descricao": "ONG precisa de auditório grande para evento de 1 dia com palestrantes",
        "lead_name": "Eduardo Pires",
        "phone": "5511900000012",
        "turns": [
            "Boa tarde! Sou de uma ONG e preciso de um auditório para um evento.",
            "Vamos fazer um congresso de sustentabilidade, esperamos 150 participantes.",
            "Seria um dia inteiro com 4 palestrantes e uma sessão de perguntas.",
            "Precisamos de palco, microfones, projetor grande e área para coffee break.",
            "Têm esse tipo de espaço disponível? Quais são os valores?",
        ],
        "esperado": {
            "trilha_detectada": "D",
            "deve_escalar": True,
            "keywords_resposta": ["auditório", "locação", "espaço", "consultor"],
        },
    },

    {
        "id": "D3",
        "trilha": "D",
        "nome": "Escola técnica quer laboratório de informática",
        "descricao": "Escola precisa de laboratório com computadores para curso externo",
        "lead_name": "Roberto Campos",
        "phone": "5511900000013",
        "turns": [
            "Olá! Preciso saber se vocês têm laboratório de informática para locação.",
            "Somos uma escola técnica e vamos aplicar um curso de programação para turma externa.",
            "Precisamos de 20 computadores com boa configuração, internet e ar condicionado.",
            "Seria 3 dias consecutivos, com turno integral.",
            "Qual a capacidade do laboratório e quais são as condições de locação?",
        ],
        "esperado": {
            "trilha_detectada": "D",
            "deve_escalar": True,
            "keywords_resposta": ["laboratório", "locação", "informática", "consultor"],
        },
    },

    {
        "id": "D4",
        "trilha": "D",
        "nome": "Empresa quer estúdio para gravação de conteúdo",
        "descricao": "Empresa quer gravar treinamentos EAD em estúdio profissional",
        "lead_name": "Camila Vaz",
        "phone": "5511900000014",
        "turns": [
            "Oi! Vocês têm estúdio para gravação de vídeos?",
            "Queremos gravar uma série de treinamentos para nosso EAD interno.",
            "Seriam 10 vídeos de 10 a 15 minutos cada, com 2 dias de gravação.",
            "Precisamos de fundo verde ou cenário montado, iluminação e equipamento de áudio.",
            "Vocês fornecem operador de câmera ou só o espaço?",
        ],
        "esperado": {
            "trilha_detectada": "D",
            "deve_escalar": True,
            "keywords_resposta": ["estúdio", "gravação", "locação", "consultor"],
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # CASOS ESPECIAIS / EDGE CASES
    # ══════════════════════════════════════════════════════════════════════════

    {
        "id": "E1",
        "trilha": "EDGE",
        "nome": "Mensagem curta e vaga no início",
        "descricao": "Lead inicia com mensagem mínima — bot deve perguntar e conduzir",
        "lead_name": "Teste Lead",
        "phone": "5511900000015",
        "turns": [
            "oi",
            "quero treinamento",
            "pra minha empresa",
            "são 30 pessoas",
            "liderança e comunicação",
        ],
        "esperado": {
            "trilha_detectada": "B",
            "deve_escalar": True,
            "keywords_resposta": ["empresa", "equipe", "consultor"],
        },
    },

    {
        "id": "E2",
        "trilha": "EDGE",
        "nome": "Lead fora do escopo — pede algo que não é oferecido",
        "descricao": "Lead pede coaching individual de carreira — bot deve tratar com elegância",
        "lead_name": "Gustavo Lima",
        "phone": "5511900000016",
        "turns": [
            "Olá! Gostaria de fazer um coaching individual de carreira com um coach executivo.",
            "Sou diretor e quero sessões 1:1 de desenvolvimento pessoal.",
            "Não é para equipe, é só para mim mesmo.",
            "Tenho orçamento de R$ 5.000 para 3 meses.",
        ],
        "esperado": {
            "trilha_detectada": "C",
            "deve_escalar": True,
            "keywords_resposta": ["consultor", "entender", "possibilidade"],
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────────────

class BotTester:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.results: list[dict] = []

    async def clear(self, phone: str) -> None:
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                await client.post(f"{self.base_url}/test/clear", params={"phone": phone})
                await client.post(f"{self.base_url}/test/reactivate", params={"phone": phone})
            except Exception as e:
                print(f"         ⚠️  Aviso ao limpar histórico: {e}")

    async def send(self, phone: str, name: str, message: str) -> dict:
        """Envia mensagem com retry automático (até 3 tentativas)."""
        for tentativa in range(1, 4):
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    res = await client.post(
                        f"{self.base_url}/test/send",
                        json={"message": message, "phone": phone, "name": name},
                    )
                    res.raise_for_status()
                    return res.json()
            except httpx.TimeoutException:
                if tentativa < 3:
                    print(f"         ⏳ Timeout — tentativa {tentativa}/3, aguardando 5s...")
                    await asyncio.sleep(5)
                else:
                    raise
            except httpx.HTTPStatusError as e:
                raise RuntimeError(f"HTTP {e.response.status_code}: {e.response.text[:200]}")

    def _check_keywords(self, response: str, keywords: list[str]) -> list[str]:
        found = []
        resp_lower = response.lower()
        for kw in keywords:
            if kw.lower() in resp_lower:
                found.append(kw)
        return found

    async def run_scenario(self, scenario: dict) -> dict:
        sid      = scenario["id"]
        phone    = scenario["phone"]
        name     = scenario["lead_name"]
        turns    = scenario["turns"]
        esperado = scenario["esperado"]

        print(f"\n{'='*60}")
        print(f"  [{sid}] {scenario['nome']}")
        print(f"  Trilha esperada: {scenario['trilha']} | Lead: {name}")
        print(f"{'='*60}")

        await self.clear(phone)
        await asyncio.sleep(0.5)

        conversation = []
        escalated    = False
        last_response = ""
        error        = None

        for i, msg in enumerate(turns, 1):
            print(f"\n  [{i}/{len(turns)}] Lead: {msg[:80]}{'...' if len(msg)>80 else ''}")
            try:
                result = await self.send(phone, name, msg)
                resp   = result.get("response", "")
                esc    = result.get("escalated", False)
                status = result.get("status", "ok")

                print(f"         Bot:  {resp[:120]}{'...' if len(resp)>120 else ''}")
                if esc:
                    print(f"         🚨 ESCALADO")

                conversation.append({
                    "turn": i,
                    "user": msg,
                    "bot":  resp,
                    "escalated": esc,
                    "status": status,
                })

                last_response = resp
                if esc:
                    escalated = True

                await asyncio.sleep(2.5)  # pausa entre mensagens (IA pode demorar)

            except Exception as e:
                error = str(e)
                print(f"         ❌ ERRO: {e}")
                break

        # ── Avaliação ──────────────────────────────────────────────────────────
        keywords_encontradas = self._check_keywords(
            " ".join(t["bot"] for t in conversation),
            esperado.get("keywords_resposta", []),
        )
        kw_pct = (
            len(keywords_encontradas) / len(esperado["keywords_resposta"]) * 100
            if esperado.get("keywords_resposta") else 100
        )

        escalation_ok = (escalated == esperado.get("deve_escalar", False))

        # Score: 50% cobertura de keywords + 50% escalação correta
        score = int(kw_pct * 0.5 + (100 if escalation_ok else 0) * 0.5)

        if score >= 70:
            status_label = "✅ APROVADO"
        elif score >= 40:
            status_label = "⚠️  PARCIAL"
        else:
            status_label = "❌ REPROVADO"

        print(f"\n  Resultado: {status_label}  |  Score: {score}/100")
        print(f"  Escalação: {'✅' if escalation_ok else '❌'} (esperado={esperado.get('deve_escalar')} obtido={escalated})")
        print(f"  Keywords:  {len(keywords_encontradas)}/{len(esperado.get('keywords_resposta',[]))} → {', '.join(keywords_encontradas) or '—'}")

        record = {
            "id":            sid,
            "trilha":        scenario["trilha"],
            "nome":          scenario["nome"],
            "descricao":     scenario["descricao"],
            "lead_name":     name,
            "phone":         phone,
            "conversation":  conversation,
            "escalated":     escalated,
            "esperado":      esperado,
            "keywords_encontradas": keywords_encontradas,
            "keywords_pct":  round(kw_pct),
            "escalation_ok": escalation_ok,
            "score":         score,
            "status_label":  status_label,
            "error":         error,
        }
        self.results.append(record)
        return record

    async def check_connection(self) -> bool:
        """Verifica se o bot está acessível antes de rodar os testes."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.get(f"{self.base_url}/test/status")
                data = res.json()
                return data.get("ok", False)
        except Exception as e:
            print(f"❌ Não foi possível conectar ao bot em {self.base_url}")
            print(f"   Erro: {e}")
            print(f"   Verifique se o serviço está rodando: systemctl status bot-sdr-pj")
            return False

    async def run_all(self) -> list[dict]:
        print(f"\n🤖 Bot SDR PJ — Testes Automatizados")
        print(f"   Base URL: {self.base_url}")
        print(f"   Cenários: {len(SCENARIOS)}")
        print(f"   Início: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")

        print("   Verificando conexão com o bot...")
        ok = await self.check_connection()
        if not ok:
            print("   ❌ Bot inacessível — abortando testes.")
            sys.exit(1)
        print("   ✅ Bot online\n")

        for scenario in SCENARIOS:
            await self.run_scenario(scenario)

        return self.results


# ─────────────────────────────────────────────────────────────────────────────
# RELATÓRIO HTML
# ─────────────────────────────────────────────────────────────────────────────

def gerar_relatorio_html(results: list[dict], base_url: str) -> str:
    agora    = datetime.now().strftime("%d/%m/%Y às %H:%M")
    total    = len(results)
    aprovado = sum(1 for r in results if r["score"] >= 70)
    parcial  = sum(1 for r in results if 40 <= r["score"] < 70)
    reprov   = sum(1 for r in results if r["score"] < 40)
    score_md = int(sum(r["score"] for r in results) / total) if total else 0

    by_trilha: dict[str, list] = {}
    for r in results:
        by_trilha.setdefault(r["trilha"], []).append(r)

    trilha_cor = {
        "A":    ("#3b82f6", "#dbeafe", "Turma Aberta / Individual"),
        "B":    ("#8b5cf6", "#ede9fe", "Corporativo / In Company"),
        "C":    ("#f59e0b", "#fef3c7", "Consultoria / Descoberta"),
        "D":    ("#10b981", "#d1fae5", "Locação de Espaço"),
        "EDGE": ("#6b7280", "#f3f4f6", "Casos Especiais"),
    }

    def score_badge(score):
        if score >= 70:
            return f'<span style="background:#dcfce7;color:#16a34a;padding:3px 10px;border-radius:999px;font-weight:700;font-size:12px">✅ {score}/100</span>'
        elif score >= 40:
            return f'<span style="background:#fef9c3;color:#ca8a04;padding:3px 10px;border-radius:999px;font-weight:700;font-size:12px">⚠️ {score}/100</span>'
        return f'<span style="background:#fee2e2;color:#dc2626;padding:3px 10px;border-radius:999px;font-weight:700;font-size:12px">❌ {score}/100</span>'

    def conv_html(conv):
        parts = []
        for t in conv:
            esc_flag = ' <span style="color:#f59e0b;font-size:11px">🚨 escalado</span>' if t.get("escalated") else ""
            parts.append(f'''
              <div style="margin-bottom:10px">
                <div style="text-align:right;margin-bottom:4px">
                  <span style="background:#dcf8c6;padding:6px 12px;border-radius:12px 12px 4px 12px;font-size:13px;display:inline-block;max-width:80%">{t["user"]}</span>
                </div>
                <div style="text-align:left">
                  <span style="background:#fff;border:1px solid #e5e7eb;padding:6px 12px;border-radius:4px 12px 12px 12px;font-size:13px;display:inline-block;max-width:80%;white-space:pre-wrap">{t["bot"]}</span>{esc_flag}
                </div>
              </div>''')
        return "".join(parts)

    cards = ""
    for trilha_id, regs in sorted(by_trilha.items()):
        cor, bg, desc = trilha_cor.get(trilha_id, ("#6b7280", "#f3f4f6", trilha_id))
        trilha_score  = int(sum(r["score"] for r in regs) / len(regs))
        cards += f'''
        <div style="margin-bottom:32px">
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
            <div style="background:{cor};color:#fff;padding:4px 14px;border-radius:999px;font-weight:700;font-size:13px">Trilha {trilha_id}</div>
            <div style="font-size:15px;font-weight:600;color:#1e293b">{desc}</div>
            <div style="margin-left:auto">{score_badge(trilha_score)}</div>
          </div>'''

        for r in regs:
            border_c = "#16a34a" if r["score"]>=70 else "#ca8a04" if r["score"]>=40 else "#dc2626"
            cards += f'''
          <div style="border:1px solid #e5e7eb;border-left:4px solid {border_c};border-radius:8px;margin-bottom:16px;overflow:hidden">
            <div style="background:#f8fafc;padding:14px 20px;display:flex;align-items:center;gap:12px;border-bottom:1px solid #e5e7eb;cursor:pointer"
                 onclick="toggle('{r['id']}')">
              <span style="font-weight:700;color:#1e293b">[{r['id']}] {r['nome']}</span>
              <span style="font-size:12px;color:#64748b">— {r['lead_name']}</span>
              <div style="margin-left:auto;display:flex;gap:8px;align-items:center">
                <span style="font-size:11px;color:#64748b">Escalação: {'✅' if r['escalation_ok'] else '❌'}</span>
                <span style="font-size:11px;color:#64748b">Keywords: {r['keywords_pct']}%</span>
                {score_badge(r['score'])}
                <span style="color:#94a3b8;font-size:14px" id="arr-{r['id']}">▼</span>
              </div>
            </div>
            <div id="body-{r['id']}" style="display:none;padding:16px 20px">
              <p style="font-size:13px;color:#64748b;margin-bottom:12px">{r['descricao']}</p>
              <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px">
                <div style="background:#f1f5f9;padding:8px 14px;border-radius:8px;font-size:12px">
                  <strong>Esperado escalar:</strong> {r['esperado'].get('deve_escalar','?')}
                </div>
                <div style="background:#f1f5f9;padding:8px 14px;border-radius:8px;font-size:12px">
                  <strong>Escalou:</strong> {r['escalated']}
                </div>
                <div style="background:#f1f5f9;padding:8px 14px;border-radius:8px;font-size:12px">
                  <strong>Keywords encontradas:</strong> {', '.join(r['keywords_encontradas']) or '—'}
                </div>
                {'<div style="background:#fee2e2;padding:8px 14px;border-radius:8px;font-size:12px;color:#dc2626"><strong>Erro:</strong> ' + r['error'] + '</div>' if r['error'] else ''}
              </div>
              <div style="background:#f8fafc;border-radius:8px;padding:16px;max-height:400px;overflow-y:auto">
                {conv_html(r['conversation'])}
              </div>
            </div>
          </div>'''

        cards += "</div>"

    html = f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Relatório de Testes — Bot SDR PJ</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
          background:#f0f4f8; color:#1e293b; }}
  .header {{ background:linear-gradient(135deg,#1e1b4b,#312e81); color:#fff;
             padding:32px 40px; }}
  .header h1 {{ font-size:26px; font-weight:800; margin-bottom:4px; }}
  .header p {{ font-size:14px; color:#a5b4fc; }}
  .kpis {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(160px,1fr));
           gap:16px; padding:24px 40px; }}
  .kpi {{ background:#fff; border:1px solid #e2e8f0; border-radius:12px;
          padding:18px; text-align:center; }}
  .kpi .val {{ font-size:32px; font-weight:800; line-height:1; margin-bottom:4px; }}
  .kpi .lbl {{ font-size:12px; color:#64748b; }}
  .content {{ padding:0 40px 40px; }}
  @media(max-width:600px) {{ .kpis,.content {{ padding-left:16px;padding-right:16px; }} }}
</style>
</head>
<body>
<div class="header">
  <h1>🤖 Relatório de Testes — Bot SDR PJ</h1>
  <p>Gerado em {agora} · URL testada: {base_url} · {total} cenários executados</p>
</div>
<div class="kpis">
  <div class="kpi">
    <div class="val" style="color:#6366f1">{total}</div>
    <div class="lbl">Cenários testados</div>
  </div>
  <div class="kpi">
    <div class="val" style="color:#16a34a">{aprovado}</div>
    <div class="lbl">✅ Aprovados (≥70)</div>
  </div>
  <div class="kpi">
    <div class="val" style="color:#ca8a04">{parcial}</div>
    <div class="lbl">⚠️ Parciais (40–69)</div>
  </div>
  <div class="kpi">
    <div class="val" style="color:#dc2626">{reprov}</div>
    <div class="lbl">❌ Reprovados (&lt;40)</div>
  </div>
  <div class="kpi">
    <div class="val" style="color:#0ea5e9">{score_md}</div>
    <div class="lbl">Score médio geral</div>
  </div>
</div>
<div class="content">
  {cards}
</div>
<script>
function toggle(id) {{
  const body = document.getElementById('body-' + id);
  const arr  = document.getElementById('arr-' + id);
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : 'block';
  arr.textContent = open ? '▼' : '▲';
}}
</script>
</body>
</html>'''
    return html


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Testes automatizados Bot SDR PJ")
    parser.add_argument("--base-url", default="http://localhost:8001",
                        help="URL base do bot (ex: http://localhost:8001 ou https://dominio.com/pj)")
    parser.add_argument("--output", default="tests/relatorio_testes.html",
                        help="Caminho do relatório HTML de saída")
    parser.add_argument("--json", default="tests/relatorio_testes.json",
                        help="Caminho do relatório JSON de saída")
    parser.add_argument("--cenario", default=None,
                        help="Rodar apenas um cenário específico (ex: A1, B2)")
    args = parser.parse_args()

    tester = BotTester(args.base_url)

    # Filtra cenário específico se solicitado
    global SCENARIOS
    if args.cenario:
        SCENARIOS = [s for s in SCENARIOS if s["id"] == args.cenario.upper()]
        if not SCENARIOS:
            print(f"❌ Cenário '{args.cenario}' não encontrado.")
            sys.exit(1)

    results = await tester.run_all()

    # ── Salva JSON ──
    json_path = Path(args.json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n💾 JSON salvo: {json_path}")

    # ── Salva HTML ──
    html      = gerar_relatorio_html(results, args.base_url)
    html_path = Path(args.output)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"📊 Relatório HTML: {html_path}")

    # ── Resumo final ──
    total    = len(results)
    aprovado = sum(1 for r in results if r["score"] >= 70)
    score_md = int(sum(r["score"] for r in results) / total) if total else 0

    print(f"\n{'='*60}")
    print(f"  RESULTADO FINAL")
    print(f"  Aprovados:   {aprovado}/{total}")
    print(f"  Score médio: {score_md}/100")
    print(f"{'='*60}\n")

    return 0 if aprovado == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
