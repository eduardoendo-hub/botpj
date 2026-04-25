"""
Motor de Farol — semáforo operacional de leads PJ.

Classifica cada lead como VERDE / AMARELO / VERMELHO com base em análise
comercial B2B completa: histórico de conversa, dados CRM, datas, porte,
proposta/orçamento, follow-up e sinais de interesse/esfriamento.

Cache em memória: TTL de 20 min, invalidado quando chega nova mensagem
ou muda a etapa do CRM.
"""

import hashlib
import json
import logging
import re
import time
from typing import Dict, Optional, Tuple

from anthropic import AsyncAnthropic

from app.core.config import settings
from app.services.token_tracker import track as _track_tokens

logger = logging.getLogger(__name__)

client = AsyncAnthropic(api_key=settings.anthropic_api_key)

# ── Cache: phone → (result_dict, timestamp, cache_key) ────────────────────────
_cache: Dict[str, Tuple[dict, float, str]] = {}
_CACHE_TTL = 1_200  # 20 minutos

_DEFAULT = {
    "semaforo":        "AMARELO",
    "score_risco":     30,
    "urgencia":        "MÉDIA",
    "pendencia_principal": "INDEFINIDO",
    "motivo_principal": "Análise indisponível — dados insuficientes ou erro de processamento.",
    "resumo_executivo": "",
    "acao_recomendada_supervisor": "Verificar manualmente o status deste lead.",
    "nivel_intervencao_supervisor": "MONITORAR",
}


def _build_cache_key(phone: str, msg_count: int, crm_etapa: str, updated_at: str) -> str:
    raw = f"{phone}:{msg_count // 2}:{crm_etapa}:{updated_at}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _cache_get(phone: str, key: str) -> Optional[dict]:
    entry = _cache.get(phone)
    if not entry:
        return None
    result, ts, cached_key = entry
    if (time.time() - ts) >= _CACHE_TTL:
        return None
    if cached_key != key:
        return None
    return result


def _cache_set(phone: str, key: str, result: dict):
    _cache[phone] = (result, time.time(), key)


def invalidate_cache(phone: str):
    _cache.pop(phone, None)


# ── System Prompt ──────────────────────────────────────────────────────────────

_SYSTEM = """Atue como especialista em operação comercial B2B, inteligência de pipeline e priorização de leads PJ para treinamentos corporativos.

Sua função: analisar o histórico do lead e retornar UM JSON com o semáforo operacional e métricas de risco.

SEMÁFORO:
VERDE → fluxo ativo, próxima etapa clara, sem pendência crítica do time, proposta acompanhada, pendência do cliente dentro do prazo.
AMARELO → atraso moderado, follow-up fraco, lead esfriando, reunião sem próximo passo, proposta sem confirmação, cliente demorando a responder.
VERMELHO → pedido do cliente sem resposta, orçamento não enviado/comunicado, promessa não cumprida, lead quente parado, empresa grande sem atendimento, fase avançada travada, atraso interno acima do aceitável.

REGRA CRÍTICA: falha do TIME é mais grave que demora do CLIENTE. Em fase avançada ou empresa grande, atrasos pesam mais.

SCORE DE RISCO (0-100):
Pedido explícito de orçamento/proposta: +30
Pedido sem resposta >1 dia útil: +35
Pedido sem resposta >2 dias úteis: +45
Promessa não cumprida: +35
Orçamento não comunicado: +45
Proposta sem follow-up: +20
Reunião sem próximo passo: +20
Lead parado em fase avançada: +25
Empresa grande: +10 | Estratégica: +15
Cliente com urgência declarada: +25
Silêncio do time após sinal claro: +35
Sem atividade relevante por muitos dias: +15 a +30
Sem próxima etapa definida: +15

REDUTORES:
Proposta enviada e confirmada: -20
Follow-up recente correto: -15
Próxima etapa com data: -15
Pendência claramente do cliente no prazo: -20
Interação recente e fluxo saudável: -20

INTERPRETAÇÃO: 0-29=VERDE | 30-59=AMARELO | 60-100=VERMELHO
(contexto pode elevar o semáforo mesmo com score menor)

EVENTOS CRÍTICOS que forçam VERMELHO direto:
- Cliente pediu orçamento/proposta sem retorno adequado
- Orçamento preparado mas cliente não foi avisado
- Pedido relevante sem resposta >1 dia útil
- Promessa importante não cumprida
- Lead grande/estratégico travado por falha interna
- Reunião com interesse claro sem continuidade
- Mensagens do cliente aguardando retorno há tempo excessivo

PENDÊNCIA:
TIME → próximo passo era do vendedor e não aconteceu
CLIENTE → time fez tudo certo e aguarda resposta do cliente
AMBOS → falha dos dois lados
INDEFINIDO → dados insuficientes

Retorne APENAS um JSON válido com exatamente estes campos (sem campos extras, sem markdown):
{
  "semaforo": "VERDE | AMARELO | VERMELHO",
  "score_risco": 0,
  "urgencia": "BAIXA | MÉDIA | ALTA | IMEDIATA",
  "pendencia_principal": "TIME | CLIENTE | AMBOS | INDEFINIDO",
  "motivo_principal": "texto curto com a principal razão (máx 120 chars)",
  "resumo_executivo": "situação geral do lead em 1-2 linhas (máx 200 chars)",
  "acao_recomendada_supervisor": "ação objetiva e prática (máx 120 chars)",
  "nivel_intervencao_supervisor": "NENHUM | MONITORAR | COBRAR_TIME | INTERVIR_IMEDIATAMENTE"
}"""


# ── Construção do user message ─────────────────────────────────────────────────

def _build_user_message(lead: dict, msgs: list, crm: dict) -> str:
    lines = ["DADOS DO LEAD:"]

    fields = {
        "Nome":             lead.get("contact_name") or lead.get("nome") or lead.get("id") or "",
        "Empresa":          lead.get("company") or lead.get("empresa") or "",
        "Porte":            lead.get("empresa_tier") or "",
        "Setor":            lead.get("setor") or "",
        "Cidade":           lead.get("cidade") or "",
        "Cargo":            lead.get("job_title") or lead.get("cargo") or "",
        "Estágio do funil": lead.get("_crm_etapa") or lead.get("funil") or lead.get("stage") or "",
        "Temperatura":      lead.get("lead_temperature") or lead.get("temp") or "",
        "Score bot":        str(lead.get("score") or ""),
        "Trail":            lead.get("trail") or "",
        "Tipo interesse":   lead.get("tipo_interesse") or lead.get("tipo") or "",
        "Tema":             lead.get("tema_interesse") or lead.get("training_interest") or lead.get("tema") or "",
        "Formato":          lead.get("formato") or "",
        "Urgência":         lead.get("urgencia") or "",
        "Prazo":            lead.get("prazo") or "",
        "Qtd participantes": str(lead.get("qtd_participantes") or ""),
        "Próximo passo":    lead.get("proximo_passo") or "",
        "Status conversa":  lead.get("status_conversa") or lead.get("status") or "",
        "Criado em":        lead.get("created_at") or "",
        "Atualizado em":    lead.get("updated_at") or "",
    }
    for k, v in fields.items():
        if v and v not in ("—", "Não informado", "?", "0", "frio"):
            lines.append(f"- {k}: {v}")

    # CRM
    if crm and isinstance(crm, dict):
        lines.append("\nDATAS DO CRM:")
        crm_fields = {
            "Pipeline":          crm.get("pipeline") or crm.get("_crm_pipeline") or "",
            "Etapa":             crm.get("etapa") or crm.get("_crm_etapa") or "",
            "Consultor":         crm.get("consultor") or crm.get("_crm_consultor") or "",
            "Valor":             str(crm.get("valor") or crm.get("_crm_valor") or ""),
            "Próxima tarefa":    crm.get("next_task") or "",
            "Última atividade":  crm.get("last_activity_date") or crm.get("updated_at") or "",
        }
        for k, v in crm_fields.items():
            if v and v not in ("0", "0.0", "—"):
                lines.append(f"- {k}: {v}")

        # Histórico de atividades do CRM
        activities = crm.get("activities") or []
        if activities:
            lines.append("\nÚLTIMAS ATIVIDADES CRM:")
            for act in activities[-5:]:
                if isinstance(act, dict):
                    dt   = act.get("date") or act.get("created_at") or ""
                    tipo = act.get("type") or act.get("activity_type") or ""
                    desc = act.get("description") or act.get("text") or ""
                    autor = act.get("author") or ""
                    linha = f"[{dt}] {tipo}"
                    if autor:
                        linha += f" ({autor})"
                    if desc:
                        linha += f": {desc[:200]}"
                    lines.append(linha)

    # Histórico de conversa
    if msgs:
        lines.append(f"\nHISTÓRICO DA CONVERSA ({len(msgs)} mensagens, últimas 25):")
        for m in msgs[-25:]:
            role  = m.get("role", "")
            txt   = (m.get("message") or m.get("content") or "")[:300]
            hora  = m.get("hora") or m.get("created_at") or ""
            label = "Lead" if role == "user" else "Bot" if role == "assistant" else "Consultor"
            if m.get("operator_name"):
                label = f"Consultor ({m['operator_name']})"
            if txt:
                lines.append(f"[{hora}] {label}: {txt}")
    else:
        lines.append("\nHISTÓRICO: nenhuma mensagem registrada.")

    return "\n".join(lines)


# ── Classificação principal ────────────────────────────────────────────────────

async def classify_farol(
    lead: dict,
    msgs: list,
    crm: dict,
) -> dict:
    """
    Retorna o dict com semáforo e métricas de risco para o lead.
    Usa cache de 20 min. Se a IA falhar, retorna _DEFAULT.
    """
    phone      = lead.get("phone_number") or lead.get("telefone") or lead.get("id") or ""
    msg_count  = len(msgs) if msgs else 0
    crm_etapa  = (crm or {}).get("etapa") or lead.get("_crm_etapa") or ""
    updated_at = lead.get("updated_at") or lead.get("created_at") or ""

    cache_key = _build_cache_key(phone, msg_count, crm_etapa, updated_at)
    cached = _cache_get(phone, cache_key)
    if cached:
        logger.debug(f"[Farol] Cache HIT: {phone}")
        return cached

    if not settings.anthropic_api_key:
        return _DEFAULT.copy()

    # Leads sem nenhum dado relevante: retorna amarelo sem chamar a IA
    has_msgs    = msg_count > 0
    has_company = bool(lead.get("company") or lead.get("contact_name"))
    has_crm     = bool((crm or {}).get("etapa"))
    if not has_msgs and not has_crm and not has_company:
        fallback = _DEFAULT.copy()
        fallback["motivo_principal"] = "Sem dados suficientes para análise de farol."
        fallback["resumo_executivo"] = "Lead recém-criado sem histórico de conversa ou CRM."
        return fallback

    user_msg = _build_user_message(lead, msgs, crm)

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,   # Schema simplificado cabe bem em 400 tokens
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        import asyncio
        asyncio.ensure_future(_track_tokens("farol_engine", "classify_farol", response.usage, "claude-haiku-4-5-20251001", phone))
        raw = response.content[0].text.strip()

        # Extrai o primeiro objeto JSON da resposta
        match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
        if not match:
            # Tenta o padrão mais amplo (JSON aninhado)
            match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            logger.warning(f"[Farol] JSON não encontrado para {phone}. Resposta: {raw[:200]}")
            result = _DEFAULT.copy()
            _cache_set(phone, cache_key, result)
            return result

        result = json.loads(match.group())

        # Valida semáforo — se vier valor inválido, corrige
        if result.get("semaforo") not in ("VERDE", "AMARELO", "VERMELHO"):
            result["semaforo"] = "AMARELO"

        # Garante todos os campos obrigatórios
        for field, default in _DEFAULT.items():
            if field not in result or result[field] is None:
                result[field] = default

        _cache_set(phone, cache_key, result)
        logger.info(
            f"[Farol] {phone} → {result.get('semaforo')} "
            f"score={result.get('score_risco')} urgencia={result.get('urgencia')}"
        )
        return result

    except json.JSONDecodeError as e:
        logger.warning(f"[Farol] JSON inválido para {phone}: {e} | raw={raw[:200] if 'raw' in dir() else 'N/A'}")
        result = _DEFAULT.copy()
        _cache_set(phone, cache_key, result)
        return result
    except Exception as e:
        logger.warning(f"[Farol] Falha para {phone}: {e}")
        return _DEFAULT.copy()
