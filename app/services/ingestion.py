"""Ingestor automático de conversas do RD Conversas (Tallos).

Puxa periodicamente o histórico completo (cliente + consultor) das conversas
ATIVAS recentes para o banco local. Substitui o sync manual do cockpit por um
job agendado — garante que as conversas dos vendedores sejam sempre capturadas,
mesmo quando o bot não atua. Base para o histórico do bot e (futuro) o RAG.
"""

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def _extract_contact_id(notes: str) -> str:
    for part in (notes or "").split("|"):
        part = part.strip()
        if part.startswith("tallos_contact_id:"):
            return part.split(":", 1)[1].strip()
    return ""


def _is_recent(updated_at: str, cutoff: datetime) -> bool:
    if not updated_at:
        return True  # sem data — sincroniza por segurança
    try:
        dt = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= cutoff
    except (ValueError, TypeError):
        return True


async def run_full_sync(recent_days: int = 3, limit_per: int = 50) -> dict:
    """Sincroniza o histórico das conversas ativas nos últimos `recent_days`.

    Leads vêm ordenados por updated_at DESC, então paramos ao cruzar o corte.
    """
    from app.core.database import get_all_leads
    from app.services.tallos import tallos_service

    cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)
    leads = await get_all_leads()

    synced = imported = errors = skipped = 0
    for lead in leads:
        if not _is_recent(lead.get("updated_at", ""), cutoff):
            break
        cid = _extract_contact_id(lead.get("notes", ""))
        if not cid:
            skipped += 1
            continue
        try:
            n = await tallos_service.sync_conversations(
                phone_number=lead["phone_number"],
                contact_id=cid,
                contact_name=lead.get("contact_name", ""),
                limit=limit_per,
            )
            imported += n
            synced += 1
        except Exception as e:
            errors += 1
            logger.error(f"[Ingest] Erro ao sincronizar {lead.get('phone_number')}: {e}")

    logger.info(
        f"[Ingest] ✅ {synced} conversas | {imported} msgs novas | "
        f"{errors} erros | {skipped} sem contact_id (últimos {recent_days}d)"
    )
    return {"leads_synced": synced, "msgs_imported": imported, "errors": errors}
