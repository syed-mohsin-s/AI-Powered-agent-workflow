"""
Sentinel-AI Vector Memory Store.

Provides semantic search over past executions, plans, and recovery strategies
using ChromaDB as the vector backend.  Falls back to a lightweight keyword-
based search when ChromaDB is unavailable.

Connected bidirectionally to:
- **Planner Agent** — recalls relevant past workflow patterns for RAG
- **Execution Agent** — recalls past tool performance for smarter tool selection
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sentinel_ai.utils.logger import get_logger

logger = get_logger("core.vector_store")

# Try to import ChromaDB; degrade gracefully if absent
try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    logger.info("ChromaDB not installed — vector store will use keyword fallback")


# ---------------------------------------------------------------------------
# Collection names
# ---------------------------------------------------------------------------

COLL_TOOL_SEQUENCES = "tool_sequences"
COLL_WORKFLOW_PATTERNS = "workflow_patterns"
COLL_RECOVERY_STRATEGIES = "recovery_strategies"
COLL_DOMAIN_KNOWLEDGE = "domain_knowledge"


# ---------------------------------------------------------------------------
# Vector Memory Store
# ---------------------------------------------------------------------------


class VectorMemoryStore:
    """Semantic memory backed by ChromaDB with keyword fallback.

    Provides RAG (Retrieval-Augmented Generation) context that can be
    injected into LLM prompts so that the Planner and Execution agents
    benefit from prior experience.
    """

    def __init__(self, persist_dir: str = "data/vector_db"):
        self._persist_dir = persist_dir
        self._client = None
        self._collections: dict[str, Any] = {}
        self._fallback_store: dict[str, list[dict]] = {
            COLL_TOOL_SEQUENCES: [],
            COLL_WORKFLOW_PATTERNS: [],
            COLL_RECOVERY_STRATEGIES: [],
            COLL_DOMAIN_KNOWLEDGE: [],
        }
        self._initialised = False

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    def initialise(self) -> None:
        """Lazy-initialise the ChromaDB client and collections."""
        if self._initialised:
            return
        self._initialised = True

        if CHROMADB_AVAILABLE:
            try:
                os.makedirs(self._persist_dir, exist_ok=True)
                self._client = chromadb.Client(
                    ChromaSettings(
                        anonymized_telemetry=False,
                        is_persistent=True,
                        persist_directory=self._persist_dir,
                    )
                )
                for name in (
                    COLL_TOOL_SEQUENCES,
                    COLL_WORKFLOW_PATTERNS,
                    COLL_RECOVERY_STRATEGIES,
                    COLL_DOMAIN_KNOWLEDGE,
                ):
                    self._collections[name] = self._client.get_or_create_collection(
                        name=name,
                        metadata={"hnsw:space": "cosine"},
                    )
                logger.info(
                    f"ChromaDB initialised at {self._persist_dir} with "
                    f"{len(self._collections)} collections"
                )
            except Exception as exc:
                logger.warning(f"ChromaDB init failed, using fallback: {exc}")
                self._client = None
        else:
            logger.info("Using keyword-based fallback vector store")

    def _ensure_init(self) -> None:
        if not self._initialised:
            self.initialise()

    # -------------------------------------------------------------------
    # Generic CRUD
    # -------------------------------------------------------------------

    def store(
        self,
        text: str,
        metadata: dict,
        collection: str = COLL_DOMAIN_KNOWLEDGE,
        doc_id: str | None = None,
    ) -> str:
        """Store a document with optional metadata."""
        self._ensure_init()
        doc_id = doc_id or str(uuid.uuid4())
        metadata = {k: str(v) if not isinstance(v, (str, int, float, bool)) else v
                     for k, v in metadata.items()}

        if self._client and collection in self._collections:
            self._collections[collection].add(
                ids=[doc_id],
                documents=[text],
                metadatas=[metadata],
            )
        else:
            self._fallback_store.setdefault(collection, []).append(
                {"id": doc_id, "text": text, "metadata": metadata}
            )
        return doc_id

    def query(
        self,
        query_text: str,
        collection: str = COLL_DOMAIN_KNOWLEDGE,
        top_k: int = 5,
        where: dict | None = None,
    ) -> list[dict]:
        """Semantic similarity search.  Returns list of {id, text, metadata, score}."""
        self._ensure_init()

        if self._client and collection in self._collections:
            kwargs: dict[str, Any] = {
                "query_texts": [query_text],
                "n_results": top_k,
            }
            if where:
                kwargs["where"] = where

            try:
                results = self._collections[collection].query(**kwargs)
            except Exception as exc:
                logger.warning(f"ChromaDB query failed: {exc}")
                return self._fallback_query(query_text, collection, top_k)

            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            ids = results.get("ids", [[]])[0]
            distances = results.get("distances", [[]])[0]

            return [
                {
                    "id": ids[i],
                    "text": docs[i],
                    "metadata": metas[i] if i < len(metas) else {},
                    "score": round(1.0 - distances[i], 4) if i < len(distances) else 0.0,
                }
                for i in range(len(docs))
            ]

        return self._fallback_query(query_text, collection, top_k)

    # -------------------------------------------------------------------
    # Domain-specific helpers
    # -------------------------------------------------------------------

    def store_execution_result(
        self,
        goal: str,
        tools_used: list[str],
        outcome: str,
        confidence: float,
        context: dict | None = None,
    ) -> str:
        """Persist a tool execution sequence for future recall."""
        text = (
            f"Goal: {goal}\n"
            f"Tools: {' → '.join(tools_used)}\n"
            f"Outcome: {outcome}\n"
            f"Confidence: {confidence}"
        )
        metadata = {
            "goal": goal[:500],
            "tools": json.dumps(tools_used),
            "outcome": outcome,
            "confidence": confidence,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return self.store(text, metadata, COLL_TOOL_SEQUENCES)

    def store_workflow_pattern(
        self,
        goal: str,
        workflow_type: str,
        task_names: list[str],
        outcome: str,
        duration_seconds: float = 0.0,
    ) -> str:
        """Persist a successful workflow pattern."""
        text = (
            f"Goal: {goal}\n"
            f"Type: {workflow_type}\n"
            f"Tasks: {' → '.join(task_names)}\n"
            f"Outcome: {outcome}\n"
            f"Duration: {duration_seconds:.1f}s"
        )
        metadata = {
            "goal": goal[:500],
            "workflow_type": workflow_type,
            "tasks": json.dumps(task_names),
            "outcome": outcome,
            "duration_seconds": duration_seconds,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return self.store(text, metadata, COLL_WORKFLOW_PATTERNS)

    def store_recovery_strategy(
        self,
        error_type: str,
        error_message: str,
        strategy: str,
        outcome: str,
        tool_from: str = "",
        tool_to: str = "",
    ) -> str:
        """Persist a recovery strategy that was applied."""
        text = (
            f"Error: {error_type} — {error_message}\n"
            f"Strategy: {strategy}\n"
            f"Switched: {tool_from} → {tool_to}\n"
            f"Outcome: {outcome}"
        )
        metadata = {
            "error_type": error_type,
            "strategy": strategy,
            "outcome": outcome,
            "tool_from": tool_from,
            "tool_to": tool_to,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return self.store(text, metadata, COLL_RECOVERY_STRATEGIES)

    # -------------------------------------------------------------------
    # RAG retrieval
    # -------------------------------------------------------------------

    def recall_for_planning(self, goal: str, top_k: int = 3) -> list[dict]:
        """Retrieve past workflow patterns relevant to the given goal."""
        return self.query(goal, COLL_WORKFLOW_PATTERNS, top_k)

    def recall_for_execution(self, action: str, top_k: int = 3) -> list[dict]:
        """Retrieve past tool execution results relevant to the given action."""
        return self.query(action, COLL_TOOL_SEQUENCES, top_k)

    def recall_recovery(self, error_description: str, top_k: int = 3) -> list[dict]:
        """Retrieve recovery strategies relevant to the error."""
        return self.query(error_description, COLL_RECOVERY_STRATEGIES, top_k)

    def get_rag_context(
        self,
        query: str,
        collections: list[str] | None = None,
        top_k: int = 3,
    ) -> str:
        """Unified RAG retrieval — returns a formatted string for LLM prompt injection."""
        collections = collections or [COLL_WORKFLOW_PATTERNS, COLL_TOOL_SEQUENCES]
        parts: list[str] = []

        for coll_name in collections:
            results = self.query(query, coll_name, top_k)
            if results:
                parts.append(f"=== {coll_name.replace('_', ' ').title()} ===")
                for i, r in enumerate(results, 1):
                    score_str = f" (relevance: {r['score']:.0%})" if r.get("score") else ""
                    parts.append(f"[{i}]{score_str} {r['text']}")

        if not parts:
            return ""

        return "Relevant past experience:\n" + "\n".join(parts)

    # -------------------------------------------------------------------
    # Fallback keyword search
    # -------------------------------------------------------------------

    def _fallback_query(
        self, query_text: str, collection: str, top_k: int
    ) -> list[dict]:
        """Simple token-overlap search when ChromaDB is not available."""
        entries = self._fallback_store.get(collection, [])
        if not entries:
            return []

        query_tokens = set(query_text.lower().split())
        scored: list[tuple[float, dict]] = []

        for entry in entries:
            doc_tokens = set(entry["text"].lower().split())
            if not doc_tokens:
                continue
            overlap = len(query_tokens & doc_tokens)
            score = overlap / max(len(query_tokens | doc_tokens), 1)
            scored.append((score, entry))

        scored.sort(key=lambda x: -x[0])
        return [
            {
                "id": e["id"],
                "text": e["text"],
                "metadata": e["metadata"],
                "score": round(s, 4),
            }
            for s, e in scored[:top_k]
            if s > 0
        ]

    # -------------------------------------------------------------------
    # Stats
    # -------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return store statistics."""
        self._ensure_init()
        stats: dict[str, int] = {}

        if self._client:
            for name, coll in self._collections.items():
                try:
                    stats[name] = coll.count()
                except Exception:
                    stats[name] = 0
        else:
            for name, entries in self._fallback_store.items():
                stats[name] = len(entries)

        return {
            "backend": "chromadb" if self._client else "keyword_fallback",
            "collections": stats,
        }


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_store: Optional[VectorMemoryStore] = None


def get_vector_store() -> VectorMemoryStore:
    """Get or create the global vector memory store singleton."""
    global _store
    if _store is None:
        _store = VectorMemoryStore()
    return _store
