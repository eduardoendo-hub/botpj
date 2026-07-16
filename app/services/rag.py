"""RAG — recuperação semântica sobre a base de conhecimento e conversas.

Guarda os vetores DENTRO do SQLite do bot (sqlite-vec), e usa o serviço de
embeddings compartilhado (EMBED_URL) para embutir textos. Sem servidor extra.

Fluxo:
  • index_*  → embute textos e guarda no vetor (rag_docs + rag_vec).
  • search   → embute a pergunta e traz os trechos mais relevantes (KNN).

Desligado com segurança se EMBED_URL não estiver configurado (search retorna []).
"""

import os
import struct
import asyncio
import logging

import httpx
import sqlite3
import sqlite_vec

from app.core.database import DB_PATH

logger = logging.getLogger(__name__)

EMBED_URL = os.getenv("EMBED_URL", "").rstrip("/")
EMBED_API_KEY = os.getenv("EMBED_API_KEY", "")
DIM = 384  # paraphrase-multilingual-MiniLM-L12-v2


def is_enabled() -> bool:
    return bool(EMBED_URL)


def _serialize(vec) -> bytes:
    return struct.pack("%df" % len(vec), *vec)


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    return db


def _init_sync():
    db = _connect()
    try:
        db.execute(
            """CREATE TABLE IF NOT EXISTS rag_docs(
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 text TEXT NOT NULL,
                 source TEXT DEFAULT '',
                 doc_type TEXT DEFAULT '',
                 ref_id TEXT DEFAULT '',
                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
        )
        db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS rag_vec USING vec0(embedding float[{DIM}])")
        db.commit()
    finally:
        db.close()


async def init_rag():
    await asyncio.to_thread(_init_sync)


async def embed(texts):
    """Chama o serviço de embeddings compartilhado."""
    if not EMBED_URL:
        raise RuntimeError("EMBED_URL não configurado")
    headers = {"X-API-Key": EMBED_API_KEY} if EMBED_API_KEY else {}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{EMBED_URL}/embed", json={"texts": texts}, headers=headers)
        r.raise_for_status()
        return r.json()["embeddings"]


def _chunk(text: str, size: int = 900, overlap: int = 150):
    """Quebra textos longos em pedaços com sobreposição (parágrafo-aware simples)."""
    text = (text or "").strip()
    if len(text) <= size:
        return [text] if text else []
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + size])
        i += size - overlap
    return out


def _index_sync(rows):
    """rows: list de (text, source, doc_type, ref_id, vec)."""
    db = _connect()
    try:
        for text, source, doc_type, ref_id, vec in rows:
            cur = db.execute(
                "INSERT INTO rag_docs(text, source, doc_type, ref_id) VALUES (?,?,?,?)",
                (text, source, doc_type, ref_id),
            )
            db.execute("INSERT INTO rag_vec(rowid, embedding) VALUES (?,?)", (cur.lastrowid, _serialize(vec)))
        db.commit()
    finally:
        db.close()


def _clear_source_sync(source: str):
    db = _connect()
    try:
        ids = [r[0] for r in db.execute("SELECT id FROM rag_docs WHERE source=?", (source,)).fetchall()]
        for i in ids:
            db.execute("DELETE FROM rag_vec WHERE rowid=?", (i,))
        db.execute("DELETE FROM rag_docs WHERE source=?", (source,))
        db.commit()
    finally:
        db.close()


async def index_items(items):
    """items: list de dict {text, source, doc_type, ref_id}. Embute e guarda."""
    items = [it for it in items if (it.get("text") or "").strip()]
    if not items:
        return 0
    vecs = await embed([it["text"] for it in items])
    rows = [
        (it["text"], it.get("source", ""), it.get("doc_type", ""), str(it.get("ref_id", "")), v)
        for it, v in zip(items, vecs)
    ]
    await asyncio.to_thread(_index_sync, rows)
    return len(rows)


async def reindex_knowledge_base():
    """(Re)indexa a base de conhecimento ativa, em chunks. Fonte='kb'."""
    from app.core.database import get_db
    await init_rag()
    await asyncio.to_thread(_clear_source_sync, "kb")
    db = await get_db()
    try:
        cur = await db.execute("SELECT id, title, content FROM knowledge_base WHERE is_active=1")
        rows = await cur.fetchall()
    finally:
        await db.close()
    items = []
    for r in rows:
        title = r["title"] if "title" in r.keys() else ""
        for chunk in _chunk(r["content"]):
            items.append({"text": f"{title}\n{chunk}".strip(), "source": "kb",
                          "doc_type": "knowledge", "ref_id": r["id"]})
    total = 0
    for i in range(0, len(items), 32):  # lotes de 32
        total += await index_items(items[i:i + 32])
    logger.info(f"[RAG] Base de conhecimento reindexada: {total} chunks.")
    return total


def _search_sync(vec, k):
    db = _connect()
    try:
        rows = db.execute(
            "SELECT d.text, d.source, d.doc_type, d.ref_id, v.distance "
            "FROM rag_vec v JOIN rag_docs d ON d.id = v.rowid "
            "WHERE v.embedding MATCH ? ORDER BY v.distance LIMIT ?",
            (_serialize(vec), k),
        ).fetchall()
        return [{"text": r[0], "source": r[1], "doc_type": r[2], "ref_id": r[3], "distance": r[4]} for r in rows]
    finally:
        db.close()


async def search(query: str, k: int = 5):
    """Retorna os k trechos mais relevantes para a pergunta. [] se desativado."""
    if not is_enabled() or not (query or "").strip():
        return []
    try:
        vecs = await embed([query])
        return await asyncio.to_thread(_search_sync, vecs[0], k)
    except Exception as e:
        logger.error(f"[RAG] Erro na busca: {e}")
        return []
