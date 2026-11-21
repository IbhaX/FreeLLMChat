"""MemoryService — the centralized facade for all private memory access.

Every private-memory read, write, search, mutation and graph operation in
the application funnels through this class (or the lower-level
``astra_memory`` / ``memory_graph`` functions it wraps, which apply the
same mandatory tenant filters). Callers pass the *resolved* user context;
the service derives the immutable tenant key itself and fails closed when
there is none.

Isolation model: application-enforced filtering on shared Astra
collections — ``{"user_id": tenant}`` is injected into every query,
including similarity search, so cross-tenant retrieval, mutation, and
relationship construction are impossible by construction. This is not
database-level row security; the Astra application token is trusted to
run only through this service.

Static class — state lives in Astra + SQLite, never in the process.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from app.services import astra_memory, memory_graph


class MemoryService:
    """Tenant-enforcing memory operations. ``user`` is the ORM User (or any
    object with ``.id``) resolved from verified authentication."""

    # ---------------------------------------------------------- helpers

    @staticmethod
    def _tenant(user: Any | None) -> str:
        return astra_memory.tenant_key(user)

    # ------------------------------------------------------------ write

    @classmethod
    def create_memory(cls, user: Any | None, content: str,
                      *, memory_type: str = "conversation",
                      conversation_id: Optional[str] = None,
                      role: str = "user") -> None:
        """Store a memory. Long-term (``knowledge``/``personal``) goes to
        mem_long; conversation turns to mem_short with a conversation id."""
        tenant = cls._tenant(user)
        if memory_type in ("conversation", "turn") and conversation_id:
            astra_memory.store_short(tenant, conversation_id, role, content)
        else:
            astra_memory.store_long(tenant, content,
                                    source=memory_type)

    @classmethod
    def update_memory(cls, user: Any | None, doc_id: str, content: str,
                      *, collection: str = astra_memory.LONG) -> bool:
        """Rewrite a memory's text — only when owned by the caller."""
        return astra_memory.update_doc_text(
            cls._tenant(user), collection, doc_id, content)

    @classmethod
    def delete_memory(cls, user: Any | None, doc_id: str,
                      *, collection: str = astra_memory.LONG) -> int:
        """Delete a memory — only when owned by the caller. 0 = denied."""
        return astra_memory.delete_doc(cls._tenant(user), collection, doc_id)

    # ------------------------------------------------------------- read

    @classmethod
    def retrieve_memory(cls, user: Any | None, doc_id: str,
                        *, collection: str = astra_memory.LONG
                        ) -> Optional[dict[str, Any]]:
        """Fetch one memory — returns None unless the caller owns it."""
        return astra_memory.get_doc(cls._tenant(user), collection, doc_id)

    @classmethod
    def search_memories(cls, user: Any | None, query: str, limit: int = 6,
                        *, conversation_id: Optional[str] = None
                        ) -> list[str]:
        """Semantic search — vectorized, filtered by tenant inside Astra."""
        tenant = cls._tenant(user)
        hits = astra_memory.recall_long(tenant, query, limit)
        if conversation_id:
            hits += astra_memory.recall_short(tenant, query,
                                              conversation_id, limit)
        return hits

    @classmethod
    def list_memories(cls, user: Any | None, limit: int = 100
                      ) -> list[dict[str, Any]]:
        """This user's graph nodes (bounded) — the tenant-scoped listing."""
        return memory_graph._fetch_docs(  # noqa: SLF001 — same package
            astra_memory.LONG, cls._tenant(user), limit)

    @classmethod
    def retrieve_related_memories(cls, user: Any | None, doc_id: str
                                  ) -> list[dict[str, Any]]:
        """Memories connected to ``doc_id`` — within the caller's graph
        only; edges can never reach another tenant's nodes."""
        graph = memory_graph.graph_cached(cls._tenant(user))
        related: list[dict[str, Any]] = []
        for e in graph["edges"]:
            other = e["b"] if e["a"] == doc_id else (
                e["a"] if e["b"] == doc_id else None)
            if other:
                node = next((n for n in graph["nodes"] if n["id"] == other),
                            None)
                if node:
                    related.append({"edge": e, "node": node})
        return related

    @classmethod
    def inspect_memory_graph(cls, user: Any | None) -> dict[str, Any]:
        """The caller's Memory Explorer graph — nodes, edges, stats."""
        return memory_graph.graph_cached(cls._tenant(user))

    # -------------------------------------------------------- lifecycle

    @classmethod
    async def consolidate_memories(cls, gateway: Any, user: Any | None,
                                   user_text: str, assistant_text: str
                                   ) -> None:
        """Extract durable facts from an exchange and merge them into the
        caller's long-term store (per-tenant dedup happens inside)."""
        await astra_memory.extract_and_store(
            gateway, cls._tenant(user), user_text, assistant_text)

    @classmethod
    async def summarize_memories(cls, user: Any | None, query: str = "",
                                 conversation_id: Optional[str] = None
                                 ) -> str:
        """Prompt-ready memory block — tenant-scoped SQL + vector recall."""
        from app.services.agents import get_memory_text

        return await get_memory_text(query, conversation_id, user=user)

    @classmethod
    def tenant_for(cls, user: Any | None) -> str:
        """Expose the derived tenant key for callers that must thread it
        through background tasks (never accepts a tenant from a payload)."""
        return cls._tenant(user)


# Convenience alias matching the module-level call style used elsewhere.
async def summarize_async(user: Any | None, query: str = "",
                          conversation_id: Optional[str] = None) -> str:
    return await MemoryService.summarize_memories(user, query,
                                                  conversation_id)
