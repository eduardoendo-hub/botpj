"""Copiloto do Gestor — resumo estratégico diário da área de Treinamentos (PF + PJ).

Todo dia de manhã, olha as conversas do dia anterior de TODA a área de Treinamentos
(canais Treinamentos + Site — inclui PF/individual e PJ/corporativo), CRUZA com o funil
do CRM do RD (etapa + insights já cacheados nos leads) e monta um email para o gestor com:
  • números com significado, SEPARADOS por PF e PJ (volume, quentes, novos, turma fechada);
  • leitura do FUNIL (etapas, negociações, perdidos + motivo);
  • leitura estratégica via IA por segmento (oportunidades, quem ligar hoje);
  • temas em alta, objeções, risco de perda e recomendações.

Reusa scheduler + email (Gmail) + Claude já existentes. NÃO inclui Faculdade/Cobrança.
"""

import re
import json
import logging
import asyncio
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Tuple, Optional

from app.core.database import DB_PATH
from app.services.turma_fechada import _channel_of_sync, _CORP_CHANNELS, is_turma_fechada

logger = logging.getLogger(__name__)

BRT = timezone(timedelta(hours=-3))

# Limites de custo/tamanho ao alimentar o Claude
_MAX_LEADS_ANALYSIS = 45      # nº máx de conversas enviadas para análise
_MAX_MSGS_PER_LEAD = 16       # mensagens por conversa
_MAX_MSG_CHARS = 200          # chars por mensagem


def _yesterday_brt() -> str:
    """Data (YYYY-MM-DD) de ONTEM no fuso BRT."""
    return (datetime.now(BRT) - timedelta(days=1)).strftime("%Y-%m-%d")


def _is_created_on(lead: Dict, day: str) -> bool:
    """True se o lead foi criado no dia informado (BRT)."""
    ca = lead.get("created_at") or ""
    if not ca:
        return False
    try:
        dt = datetime.fromisoformat(ca.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(BRT).strftime("%Y-%m-%d") == day
    except Exception:
        return False


# ── Segmentação PF × PJ ─────────────────────────────────────────────────────

def _segment(lead: Dict) -> str:
    """Classifica o lead como 'PJ' (corporativo) ou 'PF' (individual)."""
    trail = (lead.get("trail") or "").strip().upper()
    ti = (lead.get("tipo_interesse") or "").lower()
    company = (lead.get("company") or lead.get("empresa") or "").strip()
    if trail == "B" or company or any(k in ti for k in ("fechada", "corporativ")) or is_turma_fechada(lead):
        return "PJ"
    return "PF"


# ── Coleta de dados (síncrona, sqlite direto) ──────────────────────────────

# Atendente se identificando como FACULDADE (não Treinamentos). "graduação/pós" sozinho NÃO
# vale (consultor de Treinamentos usa pra qualificar), por isso exigimos "da faculdade"/"[Fac]".
_FAC_AGENT_RE = re.compile(
    r"consultor[ae]?\s+d[ao]\s+faculdade|faculdade\s+impacta|setor\s+\[?fac|\[fac\b|d[ao]\s+faculdade\s+impacta",
    re.I,
)
_AGENT_ROLES = ("consultant", "operator", "agent", "assistant")

# Cliente falando de GRADUAÇÃO / tecnólogo / ensino superior = Faculdade, NÃO Treinamentos.
# "tecnologia"/"programação"/"TI" continuam sendo Treinamento (só casa 'tecnólogo/a', não 'tecnologia').
_GRADUACAO_RE = re.compile(
    r"tecn[óo]log[oa]s?\b|gradua[çc][ãa]o|graduand|bacharel|licenciatura|vestibular|"
    r"an[áa]lise e desenvolvimento de sistemas|curso superior|ensino superior|"
    r"n[íi]vel superior|p[óo]s[-\s]?gradua",
    re.I,
)


def _is_fac_pipeline(pl: str) -> bool:
    return any(k in (pl or "").lower() for k in ("faculdade", "graduação", "graduacao", "vestibular"))


def _corporate_phones_sync(day: str) -> List[str]:
    """Telefones da área de Treinamentos no dia (canais Treinamentos/Site), EXCLUINDO conversas
    em que o atendente se identifica como faculdade (anti-vazamento da Faculdade)."""
    db = sqlite3.connect(DB_PATH)
    try:
        phones = [
            r[0] for r in db.execute(
                "SELECT DISTINCT phone_number FROM conversations "
                "WHERE date(datetime(created_at,'-3 hours'))=? AND phone_number IS NOT NULL",
                (day,),
            ).fetchall()
        ]
        corp = []
        for p in phones:
            if _channel_of_sync(p) not in _CORP_CHANNELS:
                continue
            excluir = False
            for role, msg in db.execute(
                "SELECT role, message FROM conversations WHERE phone_number=? LIMIT 60", (p,)
            ):
                if not msg:
                    continue
                # (a) atendente se identifica como faculdade  (b) cliente pede graduação/tecnólogo
                if role in _AGENT_ROLES and _FAC_AGENT_RE.search(msg):
                    excluir = True
                    break
                if role == "user" and _GRADUACAO_RE.search(msg):
                    excluir = True
                    break
            if not excluir:
                corp.append(p)
        return corp
    finally:
        db.close()


def _messages_sync(phone: str) -> List[Tuple[str, str]]:
    db = sqlite3.connect(DB_PATH)
    try:
        rows = db.execute(
            "SELECT role, message FROM conversations WHERE phone_number=? ORDER BY id DESC LIMIT ?",
            (phone, _MAX_MSGS_PER_LEAD),
        ).fetchall()
    finally:
        db.close()
    return list(reversed([(r[0] or "", r[1] or "") for r in rows]))


def _lead_sync(phone: str) -> Dict[str, Any]:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute("SELECT * FROM leads WHERE phone_number=?", (phone,)).fetchone()
        return dict(row) if row else {}
    finally:
        db.close()


def _label(lead: Dict, phone: str) -> str:
    return (lead.get("company") or lead.get("contact_name") or phone or "Lead").strip()


_CURSO_GENERICO = {
    "curso_individual", "curso individual", "curso_corporativo", "curso corporativo",
    "treinamento corporativo", "turma_fechada", "turma fechada", "turma_aberta",
    "não definido", "nao definido", "não especificado", "nao especificado",
    "a definir", "outro", "locacao", "locação",
}


def _curso(lead: Dict) -> str:
    """Curso/treinamento específico do lead (Excel, Power BI, Vendas...), best-effort."""
    for f in ("tema_interesse", "training_interest", "servico"):
        v = (lead.get(f) or "").strip()
        if v and v.lower() not in _CURSO_GENERICO:
            return v
    return ""


def _norm_curso(s: str) -> str:
    """Normaliza variações para agrupar na contagem (Curso de Power BI → Power BI)."""
    t = (s or "").lower()
    for key, label in (("excel", "Excel"), ("power bi", "Power BI"), ("powerbi", "Power BI"),
                       ("python", "Python"), ("vendas", "Vendas"),
                       ("lógica", "Lógica de Programação"), ("logica", "Lógica de Programação"),
                       ("power point", "PowerPoint"), ("powerpoint", "PowerPoint"),
                       ("word", "Word"), ("sql", "SQL"), ("java", "Java"),
                       ("marketing", "Marketing"), ("liderança", "Liderança"), ("lideranca", "Liderança")):
        if key in t:
            return label
    return s.strip()[:40]


# ── Métricas determinísticas (split PF/PJ + funil) ─────────────────────────

def _blank_seg() -> Dict[str, int]:
    return {"conversas": 0, "quentes": 0, "novos": 0, "turma_fechada": 0}


def _compute_metrics(day: str, records: List[Dict]) -> Dict[str, Any]:
    seg = {"PF": _blank_seg(), "PJ": _blank_seg()}
    funil: Dict[str, int] = {}
    perdidos: List[str] = []
    temas: Dict[str, int] = {}
    cursos: Dict[str, int] = {}
    atend: Dict[str, int] = {}
    for r in records:
        lead = r["lead"]
        s = r["seg"]
        seg[s]["conversas"] += 1
        if (lead.get("lead_temperature") or "").lower() == "quente":
            seg[s]["quentes"] += 1
        if _is_created_on(lead, day):
            seg[s]["novos"] += 1
        if s == "PJ" and is_turma_fechada(lead):
            seg["PJ"]["turma_fechada"] += 1
        etapa = (lead.get("crm_etapa_cache") or "").strip()
        if etapa:
            funil[etapa] = funil.get(etapa, 0) + 1
            if etapa.lower().startswith("perdido"):
                perdidos.append(f"{_label(lead, r['phone'])} — {etapa}")
        ti = (lead.get("tipo_interesse") or "").strip()
        if ti:
            temas[ti] = temas.get(ti, 0) + 1
        cur = r.get("curso") or ""
        if cur:
            k = _norm_curso(cur)
            cursos[k] = cursos.get(k, 0) + 1
        atend[r["atendido"]] = atend.get(r["atendido"], 0) + 1
    return {
        "dia": day,
        "total_conversas": len(records),
        "pf": seg["PF"],
        "pj": seg["PJ"],
        "funil": dict(sorted(funil.items(), key=lambda kv: kv[1], reverse=True)),
        "perdidos": perdidos[:8],
        "atendimento": atend,
        "temas": dict(sorted(temas.items(), key=lambda kv: kv[1], reverse=True)),
        "cursos": dict(sorted(cursos.items(), key=lambda kv: kv[1], reverse=True)),
    }


async def _gather(day: str) -> Tuple[Dict[str, Any], List[Dict]]:
    phones = await asyncio.to_thread(_corporate_phones_sync, day)
    records = []
    for ph in phones[:_MAX_LEADS_ANALYSIS]:
        msgs = await asyncio.to_thread(_messages_sync, ph)
        if not msgs:
            continue
        lead = await asyncio.to_thread(_lead_sync, ph)
        roles = {r for r, _ in msgs}
        tem_bot = "assistant" in roles
        tem_vend = bool({"consultant", "operator", "agent"} & roles)
        atendido = ("Bot + Vendedor" if (tem_bot and tem_vend)
                    else "Vendedor" if tem_vend else "Bot" if tem_bot else "—")
        records.append({"phone": ph, "lead": lead, "msgs": msgs, "atendido": atendido,
                        "seg": _segment(lead), "curso": _curso(lead)})
    # CRM: consultor (para nomear entre parênteses) + pipeline (anti-faculdade)
    meta = await _deal_meta([r["phone"] for r in records])
    records = [r for r in records if not _is_fac_pipeline(meta.get(r["phone"], {}).get("pipeline", ""))]
    for r in records:
        r["consultor"] = meta.get(r["phone"], {}).get("consultor", "")
    metrics = _compute_metrics(day, records)
    return metrics, records


# ── Transcrições compactas p/ o Claude (com funil + insight do CRM) ─────────

def _role_tag(role: str) -> str:
    if role == "user":
        return "CLIENTE"
    if role in ("consultant", "operator", "agent"):
        return "VENDEDOR"
    return "BOT"


def _transcripts_block(records: List[Dict]) -> str:
    blocks = []
    for i, r in enumerate(records, 1):
        lead = r["lead"]
        etapa = (lead.get("crm_etapa_cache") or "").strip()
        insight = (lead.get("crm_insights") or "").strip()
        cab = (f"### CONVERSA {i} [{r['seg']}] — {_label(lead, r['phone'])}"
               f" | CURSO={r.get('curso') or '(ver na conversa)'}"
               f" | trilha={lead.get('trail') or '?'}"
               f" | temp={lead.get('lead_temperature') or '?'}"
               f" | tipo={lead.get('tipo_interesse') or '?'}"
               f" | funil_CRM={etapa or '—'}"
               f" | atendido={r['atendido']}"
               f" | consultor={r.get('consultor') or '—'}")
        linhas = [f"[{_role_tag(role)}] {(msg or '')[:_MAX_MSG_CHARS]}" for role, msg in r["msgs"]]
        corpo = cab + "\n" + "\n".join(linhas)
        if insight:
            corpo += f"\n(insight CRM: {insight[:280]})"
        blocks.append(corpo)
    return "\n\n".join(blocks)


_ANALYST_SYSTEM = (
    "Você é o Diretor Comercial da Impacta (escola de tecnologia). A área de Treinamentos vende para "
    "DOIS públicos: PF (pessoa física — indivíduos comprando cursos/turmas abertas) e PJ (empresas — "
    "treinamento corporativo, turma fechada/in company, locação). Você recebe as conversas de UM dia "
    "dessa área, JÁ CRUZADAS com a etapa do funil do CRM, e produz uma leitura ESTRATÉGICA para o gestor, "
    "SEPARANDO PF e PJ, com foco em ALAVANCAR VENDAS: oportunidades, dinheiro na mesa, onde colocar força. "
    "Seja direto, específico, cite empresas/leads reais e a etapa do funil quando útil. NÃO invente dados. "
    "Português do Brasil. NUNCA cite preços/valores/descontos ao CLIENTE, mas você PODE relatar ao gestor "
    "o que foi praticado nas conversas (é interno)."
)

_ANALYST_INSTRUCTION = (
    "Analise o dia e responda SOMENTE com um objeto JSON válido (sem markdown/cercas), com EXATAMENTE:\n"
    "{\n"
    '  "destaque": "1-2 frases: o que mais importou no dia (cite PF e/ou PJ)",\n'
    '  "termometro": "leitura geral do dia (aquecido/normal/fraco) e por quê, 1 frase",\n'
    '  "cursos_procurados": [{"curso": "nome do curso (ex: Excel, Power BI, Python, Vendas)", "qtd": 3, "obs": "PF/PJ, nível, tendência"}],\n'
    '  "funil_leitura": "1-2 frases lendo o pipeline: negociações, gargalos, perdidos e motivos",\n'
    '  "pf": {"leitura": "1-2 frases sobre o segmento PF", "oportunidades": ["..."], "ligar_hoje": [{"quem":"...","curso":"...","motivo":"..."}]},\n'
    '  "pj": {"leitura": "1-2 frases sobre o segmento PJ", "oportunidades": ["..."], "ligar_hoje": [{"quem":"...","curso":"...","motivo":"..."}]},\n'
    '  "objecoes": [{"objecao": "...", "sugestao": "como contornar"}],\n'
    '  "risco_perda": ["leads quentes/corporativos prestes a esfriar ou sem retorno"],\n'
    '  "recomendacoes": ["ações estratégicas para o gestor amanhã"]\n'
    "}\n"
    "REGRA 1 (CURSO): SEMPRE deixe CLARO qual CURSO/treinamento está em jogo em cada oportunidade, "
    "ligar_hoje e objeção — extraia da conversa quando o campo CURSO vier vazio (ex: 'Excel Módulo II', "
    "'Python básico', 'Power BI', 'Treinamento de Vendas'). Em 'cursos_procurados', consolide os cursos "
    "mais falados no dia, do mais para o menos procurado.\n"
    "REGRA 2 (CONSULTOR): o consultor RESPONSÁVEL pelo lead é APENAS o do campo 'consultor=' do "
    "cabeçalho (dono do deal no CRM). Cite-o entre parênteses (ex: 'Total Química — Rafael (Rosana)'). "
    "NUNCA afirme que alguém 'assumiu' ou 'está atendendo' com base em um nome que aparece no TEXTO da "
    "conversa — vários consultores podem ter respondido (ex: um à noite após transferência), mas o "
    "RESPONSÁVEL é só o do CRM. Se 'consultor=' vier '—', escreva '(consultor não identificado no CRM)' "
    "e não invente nome.\n"
    "Listas vazias são permitidas. Máx 6 itens por lista. Priorize o que gera receita."
)


def _loads_lenient(raw: str) -> Dict[str, Any]:
    """Extrai/parseia o JSON da resposta do modelo, tolerando cercas e vírgulas sobrando."""
    import re
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
    if "{" in s and "}" in s:
        s = s[s.find("{"): s.rfind("}") + 1]
    try:
        return json.loads(s)
    except Exception:
        # remove vírgulas finais antes de } ou ] e tenta de novo
        s2 = re.sub(r",\s*([}\]])", r"\1", s)
        return json.loads(s2)


def _empty_analysis(msg: str = "") -> Dict[str, Any]:
    return {"destaque": msg or "Sem conversas de Treinamentos no dia.", "termometro": "", "funil_leitura": "",
            "cursos_procurados": [],
            "pf": {"leitura": "", "oportunidades": [], "ligar_hoje": []},
            "pj": {"leitura": "", "oportunidades": [], "ligar_hoje": []},
            "objecoes": [], "risco_perda": [], "recomendacoes": []}


async def _analyze(metrics: Dict, records: List[Dict]) -> Dict[str, Any]:
    """Chama o Claude (Sonnet) para a leitura estratégica. Best-effort — nunca quebra o job."""
    if not records:
        return _empty_analysis()
    from app.services.ai_engine import client
    transcripts = _transcripts_block(records)
    _WD = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
    try:
        _wd = _WD[datetime.strptime(metrics["dia"], "%Y-%m-%d").weekday()]
    except Exception:
        _wd = ""
    resumo_metricas = (
        f"Dia: {metrics['dia']} ({_wd}) | Conversas: {metrics['total_conversas']}\n"
        f"PF: {metrics['pf']}\nPJ: {metrics['pj']}\n"
        f"Funil (etapa: qtd): {metrics['funil']}\n"
        f"Perdidos: {metrics['perdidos']}\n"
        f"Atendimento: {metrics['atendimento']}\nInteresses: {metrics['temas']}\n"
    )
    prompt = (f"MÉTRICAS DO DIA:\n{resumo_metricas}\n"
              f"IMPORTANTE: o dia da semana É EXATAMENTE '{_wd}'. Se citar o dia da semana, use este — "
              f"NUNCA calcule a partir da data.\n\nCONVERSAS DO DIA (cruzadas com funil):\n"
              f"{transcripts}\n\n{_ANALYST_INSTRUCTION}")
    model = "claude-sonnet-4-5-20250929"
    try:
        resp = await client.messages.create(
            model=model, max_tokens=4096, system=_ANALYST_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            from app.services.ai_engine import _track_tokens
            asyncio.ensure_future(_track_tokens("sales_digest", "analyze_day", resp.usage, model, ""))
        except Exception:
            pass
        data = _loads_lenient(resp.content[0].text)
        # garante as chaves de segmento
        for seg in ("pf", "pj"):
            if not isinstance(data.get(seg), dict):
                data[seg] = {"leitura": "", "oportunidades": [], "ligar_hoje": []}
        return data
    except Exception as e:
        logger.error(f"[SalesDigest] Falha na análise via IA: {e}")
        return _empty_analysis("Não foi possível gerar a análise estratégica automática.")


# ── Visão OPERACIONAL (supervisor de call center) ──────────────────────────
# Tempos de resposta REAIS (não média) — exemplos de demora e abandono, com hora/minuto,
# consultor responsável e marcação de dentro/fora do expediente.

_SLA_MIN = 15            # resposta humana acima disso (no expediente) = demora sinalizada
_EXP_INI, _EXP_FIM = 8, 18   # expediente: seg-sex, 08h-18h BRT
_OP_MAX_CASOS = 10       # máx de demoras exibidas
_OP_MAX_ABANDONOS = 8


def _parse_brt(ts: str):
    from datetime import datetime as _dt
    try:
        d = _dt.fromisoformat((ts or "").replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(BRT)
    except Exception:
        return None


def _is_expediente(dt) -> bool:
    return dt is not None and dt.weekday() < 5 and _EXP_INI <= dt.hour < _EXP_FIM


def _hhmm(ts) -> str:
    d = _parse_brt(ts)
    return d.strftime("%H:%M") if d else ""


def _fmt_dur(secs: float) -> str:
    m = int(secs // 60)
    if m < 60:
        return f"{m} min"
    return f"{m // 60}h{m % 60:02d}"


# Mensagens AUTOMÁTICAS da RD (não contam como resposta real do atendente).
_SYS_MSG_RE = re.compile(
    r"atendimento (iniciado|encerrado|finalizado|reiniciado)|aguardando atendente|"
    r"foi transferido|transferid[oa]\(a\)|voc[êe] foi transferid|setor \[|"
    r"conversa (foi )?(encerrada|finalizada)|iniciou o atendimento|assumiu o atendimento|"
    r"entrou (na conversa|no atendimento)|^\s*>\s*:|^>:",
    re.I,
)


def _is_system_msg(msg: str) -> bool:
    return bool(_SYS_MSG_RE.search(msg or ""))


# Despedidas / confirmações do cliente — NÃO são pergunta aguardando resposta.
_CLOSING_EXACT = {
    "ok", "okay", "ok!", "okk", "k", "blz", "beleza", "valeu", "vlw", "tá", "ta", "tá bom", "ta bom",
    "tudo bem", "certo", "perfeito", "combinado", "obrigado", "obrigada", "obg", "obgd", "de nada",
    "denada", "show", "ótimo", "otimo", "isso", "isso mesmo", "entendi", "aham", "uhum", "joia", "jóia",
    "ok obrigado", "ok obrigada", "obrigado!", "obrigada!", "valeu!", "sim", "não", "nao", "👍", "🙏",
    "👏", "💪", "👍🏻", "👍🏼",
}
_CLOSING_SUB = (
    "acabei de responder", "acabei de te responder", "já respondi", "ja respondi", "respondi por la",
    "respondi por lá", "respondi ali", "respondi lá", "eu que agradeço", "eu q agradeço",
    "quem agradece", "obrigado pela", "obrigada pela", "agradeço a atenção", "agradeço pela",
    "muito obrigad",
)


def _is_closing(msg: str) -> bool:
    t = (msg or "").strip().lower().rstrip(" .!?…")
    if not t:
        return True
    if t in _CLOSING_EXACT:
        return True
    if len(t) <= 40 and any(s in t for s in _CLOSING_SUB):
        return True
    return False


def _op_scan_sync(day: str, phones: List[str]):
    """Varre as conversas do dia e mede tempos de resposta REAIS (cliente → atendente).

    Um caso só conta como DEMORA quando:
      • a mensagem do cliente é uma pergunta/pedido de verdade (não "ok"/despedida);
      • a resposta que fecha a espera é de um HUMANO e NÃO é automação da RD
        ("atendimento encerrado", "transferido", ">:", etc.);
      • o intervalo é >= _SLA_MIN.
    Respostas do bot (rápidas ou não) e automações fecham/ignoram a espera sem gerar demora.
    ABANDONO = pergunta real do cliente sem NENHUMA resposta real até o fim do dia.
    """
    demoras, abandonos = [], []
    HUMAN = ("consultant", "operator", "agent")
    RESP = ("consultant", "operator", "agent", "assistant")
    db = sqlite3.connect(DB_PATH)
    try:
        for ph in phones:
            rows = db.execute(
                "SELECT id, role, message, created_at FROM conversations "
                "WHERE phone_number=? AND date(datetime(created_at,'-3 hours'))=? ORDER BY id",
                (ph, day),
            ).fetchall()
            n = len(rows)
            pending = None  # índice da 1ª pergunta REAL do cliente sem resposta
            for idx in range(n):
                _id, role, msg, ts = rows[idx]
                if role == "user":
                    if pending is None and msg and not _is_closing(msg):
                        pending = idx
                elif role in RESP:
                    if _is_system_msg(msg):
                        continue  # automação da RD: não é resposta real, espera continua
                    if pending is not None:
                        t0 = _parse_brt(rows[pending][3])
                        t1 = _parse_brt(ts)
                        gap = (t1 - t0).total_seconds() if (t0 and t1) else 0
                        if role in HUMAN and gap >= _SLA_MIN * 60:
                            cli = [(_hhmm(rows[j][3]), rows[j][2]) for j in range(idx)
                                   if rows[j][1] == "user" and rows[j][2] and not _is_system_msg(rows[j][2])][-2:]
                            con, k = [], idx
                            while k < n and len(con) < 2:
                                _i2, r2, m2, ts2 = rows[k]
                                if r2 == "user":
                                    break
                                if r2 in RESP and m2 and not _is_system_msg(m2):
                                    con.append((_hhmm(ts2), m2))
                                k += 1
                            # desfecho: o cliente voltou a falar DEPOIS desta resposta? (inclusive dias após)
                            voltou = db.execute(
                                "SELECT 1 FROM conversations WHERE phone_number=? AND role='user' "
                                "AND id > ? LIMIT 1", (ph, _id),
                            ).fetchone()
                            demoras.append({
                                "phone": ph, "quando": t0, "espera_s": gap,
                                "cliente_msgs": cli or [(_hhmm(rows[pending][3]), rows[pending][2] or "")],
                                "consultor_msgs": con or [(_hhmm(ts), msg or "")],
                                "exp": _is_expediente(t0), "continuou": bool(voltou),
                            })
                        pending = None  # resposta real fecha a espera (mesmo abaixo do SLA)
            if pending is not None:
                t0 = _parse_brt(rows[pending][3])
                if t0 is not None:
                    ctx = [(r, _hhmm(ts2), m) for (_i, r, m, ts2) in rows[max(0, pending - 3): pending + 1]
                           if m and not _is_system_msg(m)]
                    abandonos.append({
                        "phone": ph, "quando": t0, "exp": _is_expediente(t0),
                        "pergunta": rows[pending][2] or "", "contexto": ctx,
                    })
    finally:
        db.close()
    demoras.sort(key=lambda e: e["espera_s"], reverse=True)
    abandonos.sort(key=lambda e: e["quando"], reverse=True)
    return demoras, abandonos


def _pretty_consultor(s: str) -> str:
    """Deixa o nome do consultor apresentável (o CRM às vezes guarda o e-mail)."""
    import re
    s = (s or "").strip()
    if not s:
        return ""
    if "@" in s:  # e-mail → nome a partir do local-part (vivian@... → Vivian; joao.silva → João Silva)
        local = s.split("@")[0]
        parts = [p for p in re.split(r"[._-]+", local) if p and not p.isdigit()]
        pretty = " ".join(p.capitalize() for p in parts)
        return pretty or s
    return s


async def _deal_meta(phones: List[str]) -> Dict[str, Dict[str, str]]:
    """{phone: {consultor, pipeline}} do CRM. Best-effort, tolera falhas."""
    from app.services.rd_crm import get_deal_info
    if not phones:
        return {}
    res = await asyncio.gather(*[get_deal_info(p) for p in phones], return_exceptions=True)
    out = {}
    for p, r in zip(phones, res):
        if not isinstance(r, Exception) and isinstance(r, dict):
            out[p] = {"consultor": _pretty_consultor(r.get("consultor") or ""),
                      "pipeline": (r.get("pipeline") or "").strip()}
        else:
            out[p] = {"consultor": "", "pipeline": ""}
    return out


_SUP_SYSTEM = (
    "Você é um SUPERVISOR DE ATENDIMENTO / call center da Impacta (área de Treinamentos). "
    "Recebe casos REAIS de ontem: demoras de resposta e abandonos, com horário, tempo de espera e o "
    "consultor responsável. Faça uma leitura OPERACIONAL (não de vendas): SLA, filas, abandonos, "
    "desempenho por consultor e o efeito de estar FORA DO EXPEDIENTE (quando só o bot responde e o "
    "cliente pode ficar sem humano). Seja direto e objetivo. Use SOMENTE os horários/tempos fornecidos "
    "— nunca invente números. Português do Brasil."
)

_SUP_INSTRUCTION = (
    "Responda SOMENTE JSON válido: {"
    '"leitura": "2-3 frases de supervisor sobre o atendimento de ontem (SLA, filas, off-hours)", '
    '"padroes": ["padrões observados: ex. demoras concentradas em um consultor, abandonos fora do expediente"], '
    '"recomendacoes": ["ações operacionais objetivas (escala, plantão, transbordo do bot, etc.)"]}. '
    "Máx 5 por lista. Cite consultores e horários quando relevante."
)


async def _analyze_operacional(casos: List[Dict], abandonos: List[Dict], resumo: Dict) -> Dict[str, Any]:
    if not casos and not abandonos:
        return {"leitura": "", "padroes": [], "recomendacoes": []}
    from app.services.ai_engine import client
    def _linha(c):
        janela = "no expediente" if c.get("exp") else "FORA do expediente"
        cli = " / ".join(f"[{h}] {m}" for h, m in (c.get("cliente_msgs") or [])[-2:])
        con = " / ".join(f"[{h}] {m}" for h, m in (c.get("consultor_msgs") or [])[:2])
        desf = "cliente VOLTOU a falar depois" if c.get("continuou") else "cliente NÃO voltou (possível abandono pela demora)"
        return (f"- {c.get('data','')} {c.get('hora','')} ({janela}) | consultor: {c.get('consultor') or '—'} | "
                f"espera: {c.get('espera','')} | lead: {c.get('label','')} | "
                f"cliente disse: \"{cli[:200]}\" | consultor respondeu: \"{con[:160]}\" | DESFECHO: {desf}")
    txt_dem = "\n".join(_linha(c) for c in casos) or "(nenhuma demora acima do SLA)"
    txt_ab = "\n".join(
        f"- {a.get('data','')} {a.get('hora','')} ({'no expediente' if a.get('exp') else 'FORA do expediente'}) | "
        f"consultor: {a.get('consultor') or '—'} | lead: {a.get('label','')} | "
        f"do que se tratava: \"{' / '.join(f'[{h}] {m}' for _, h, m in (a.get('contexto') or []))[:240]}\""
        for a in abandonos
    ) or "(nenhum abandono)"
    prompt = (f"RESUMO: {resumo}\nSLA considerado: {_SLA_MIN} min | Expediente: seg-sex {_EXP_INI}h-{_EXP_FIM}h\n\n"
              f"DEMORAS (>= SLA):\n{txt_dem}\n\nABANDONOS (sem resposta até o fim do dia):\n{txt_ab}\n\n{_SUP_INSTRUCTION}")
    model = "claude-sonnet-4-5-20250929"
    try:
        resp = await client.messages.create(
            model=model, max_tokens=1500, system=_SUP_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            from app.services.ai_engine import _track_tokens
            asyncio.ensure_future(_track_tokens("sales_digest", "analyze_operacional", resp.usage, model, ""))
        except Exception:
            pass
        return _loads_lenient(resp.content[0].text)
    except Exception as e:
        logger.error(f"[SalesDigest] Falha na análise operacional: {e}")
        return {"leitura": "", "padroes": [], "recomendacoes": []}


async def build_operacional(day: str) -> Dict[str, Any]:
    """Monta a visão operacional (supervisor) do dia."""
    phones = await asyncio.to_thread(_corporate_phones_sync, day)
    meta = await _deal_meta(phones)
    phones = [p for p in phones if not _is_fac_pipeline(meta.get(p, {}).get("pipeline", ""))]
    demoras, abandonos = await asyncio.to_thread(_op_scan_sync, day, phones)
    top_dem = demoras[:_OP_MAX_CASOS]
    top_ab = abandonos[:_OP_MAX_ABANDONOS]

    flagged = list({e["phone"] for e in top_dem + top_ab})
    consultores = {p: meta.get(p, {}).get("consultor", "") for p in flagged}
    labels = {p: _label(await asyncio.to_thread(_lead_sync, p), p) for p in flagged}

    def _mk(e, is_ab=False):
        dt = e["quando"]
        d = {
            "label": labels.get(e["phone"], e["phone"]),
            "consultor": consultores.get(e["phone"], ""),
            "hora": dt.strftime("%H:%M") if dt else "",
            "data": dt.strftime("%d/%m") if dt else "",
            "exp": e.get("exp", False),
        }
        if is_ab:
            d["pergunta"] = e.get("pergunta", "")
            d["contexto"] = e.get("contexto", [])
        else:
            d["espera"] = _fmt_dur(e["espera_s"])
            d["espera_s"] = e["espera_s"]
            d["cliente_msgs"] = e.get("cliente_msgs", [])
            d["consultor_msgs"] = e.get("consultor_msgs", [])
            d["continuou"] = e.get("continuou")
        return d

    casos = [_mk(e) for e in top_dem]
    abandonos_l = [_mk(e, is_ab=True) for e in top_ab]

    dem_exp = sum(1 for e in demoras if e["exp"])
    ab_exp = sum(1 for e in abandonos if e["exp"])
    resumo = {
        "demoras": len(demoras), "demoras_expediente": dem_exp, "demoras_fora": len(demoras) - dem_exp,
        "abandonos": len(abandonos), "abandonos_expediente": ab_exp, "abandonos_fora": len(abandonos) - ab_exp,
        "maior_espera": _fmt_dur(demoras[0]["espera_s"]) if demoras else "—",
        "demoras_sem_retorno": sum(1 for e in demoras if not e.get("continuou")),
    }
    supervisor = await _analyze_operacional(casos, abandonos_l, resumo)
    return {
        "sla_min": _SLA_MIN, "expediente": f"seg-sex {_EXP_INI}h-{_EXP_FIM}h",
        "resumo": resumo, "casos": casos, "abandonos": abandonos_l, "supervisor": supervisor,
    }


async def build_digest(day: Optional[str] = None) -> Dict[str, Any]:
    """Monta o pacote completo do dia: {dia, metrics, analysis, operacional, records}."""
    day = day or _yesterday_brt()
    metrics, records = await _gather(day)
    analysis = await _analyze(metrics, records)
    operacional = await build_operacional(day)
    return {"dia": day, "metrics": metrics, "analysis": analysis,
            "operacional": operacional, "records": len(records)}


# ── Job agendado ────────────────────────────────────────────────────────────

async def run_daily_digest(day: Optional[str] = None) -> bool:
    """Entry-point do scheduler: monta e envia o resumo estratégico de ontem."""
    from app.core.database import get_bot_config
    from app.services.email_service import send_sales_digest
    try:
        cfg = await get_bot_config()
        digest = await build_digest(day)
        ok = await send_sales_digest(digest, cfg)
        logger.info(f"[SalesDigest] Resumo {digest['dia']} — "
                    f"{digest['metrics']['total_conversas']} conversas "
                    f"(PF {digest['metrics']['pf']['conversas']} / PJ {digest['metrics']['pj']['conversas']}) "
                    f"— enviado={ok}")
        return ok
    except Exception as e:
        logger.error(f"[SalesDigest] Erro no job diário: {e}", exc_info=True)
        return False
