"""Ingestor automático de conversas do RD Conversas (Tallos) — Bot SDR PJ.

Puxa periodicamente o histórico completo (cliente + consultor + bot) das conversas
ATIVAS recentes para o banco local, alimentando o histórico e (futuro) o RAG.

IMPORTANTE (PJ): diferente do MBA, o histórico do PJ NÃO vem pela API
`get_recent_messages` (retorna vazio) — vem pelo endpoint criptografado JWK
(`tallos_history.get_conversation_history`, o mesmo que o Radar usa). Por isso
este ingestor usa essa fonte e sintetiza um external_id estável para deduplicar.

Cuidados de produção: throttle entre chamadas + limite por rodada (evita 429) e
trava de instância única (flock) → roda em UM worker só.
"""

import re
import fcntl
import hashlib
import asyncio
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_singleton_locks = {}

# papel do Tallos -> papel local
_ROLE_MAP = {"customer": "user", "operator": "consultant", "bot": "assistant"}


def acquire_singleton_lock(name: str) -> bool:
    """Retorna True em exatamente um processo (worker). flock não-bloqueante."""
    try:
        fh = open(f"/tmp/{name}.lock", "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _singleton_locks[name] = fh  # mantém vivo
        return True
    except (IOError, OSError):
        return False


def _extract_contact_id(notes: str) -> str:
    for part in re.split(r"[|;]", notes or ""):
        part = part.strip()
        if part.startswith("tallos_contact_id:"):
            return part.split(":", 1)[1].strip()
    return ""


def _is_recent(updated_at: str, cutoff: datetime) -> bool:
    if not updated_at:
        return True
    try:
        dt = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= cutoff
    except (ValueError, TypeError):
        return True


def _external_id(customer_id: str, msg: dict) -> str:
    """ID estável para dedup (o histórico JWK não traz id de mensagem)."""
    base = f"{customer_id}|{msg.get('created_at','')}|{msg.get('role','')}|{(msg.get('message','') or '')[:40]}"
    return "jwk:" + hashlib.md5(base.encode("utf-8")).hexdigest()


async def _sync_one(phone_number: str, contact_id: str, contact_name: str, limit_per: int) -> int:
    """Importa o histórico JWK de um contato. Retorna nº de msgs novas."""
    from app.services.tallos_history import get_conversation_history
    from app.core.database import save_message_external

    result = await get_conversation_history(contact_id, page=1, limit=limit_per)
    messages = result.get("messages", []) if isinstance(result, dict) else []
    imported = 0
    for m in messages:
        text = (m.get("message", "") or "").strip()
        if not text:
            continue
        role = _ROLE_MAP.get(m.get("role", ""), "user")
        name = m.get("operator_name") or contact_name
        ok = await save_message_external(
            phone_number=phone_number,
            role=role,
            message=text,
            contact_name=name,
            channel="tallos",
            external_id=_external_id(contact_id, m),
            created_at=m.get("created_at", ""),
        )
        if ok:
            imported += 1
    return imported


async def run_full_sync(
    recent_days: int = 3,
    limit_per: int = 50,
    max_leads: int = 250,
    delay: float = 0.5,
) -> dict:
    """Sincroniza o histórico (JWK) das conversas PJ ativas nos últimos `recent_days`."""
    from app.core.database import get_all_leads

    cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)
    leads = await get_all_leads()

    synced = imported = errors = skipped = 0
    for lead in leads:
        if synced >= max_leads:
            logger.info(f"[Ingest] limite de {max_leads} leads/rodada atingido — resto na próxima.")
            break
        if not _is_recent(lead.get("updated_at", ""), cutoff):
            break
        cid = _extract_contact_id(lead.get("notes", ""))
        if not cid:
            skipped += 1
            continue
        try:
            imported += await _sync_one(lead["phone_number"], cid, lead.get("contact_name", ""), limit_per)
            synced += 1
        except Exception as e:
            errors += 1
            logger.error(f"[Ingest] Erro ao sincronizar {lead.get('phone_number')}: {e}")
        await asyncio.sleep(delay)

    logger.info(
        f"[Ingest] ✅ {synced} conversas | {imported} msgs novas | "
        f"{errors} erros | {skipped} sem contact_id (últimos {recent_days}d)"
    )
    return {"leads_synced": synced, "msgs_imported": imported, "errors": errors}
