"""
Sentinel-AI Agent Memory System.

Persistent memory for agent learning. Stores and recalls:
- Successful tool sequences (which tools worked for which goals)
- Recovery strategies (what worked when things failed)
- Workflow patterns (how similar goals were achieved before)

Uses in-memory storage with JSON file persistence.
"""

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sentinel_ai.utils.logger import get_logger

logger = get_logger("core.memory")


@dataclass
class MemoryEntry:
    """A single memory record."""

    id: str
    memory_type: str  # "tool_sequence", "recovery_strategy", "workflow_pattern"
    goal: str  # What was being achieved
    tools_used: list[str] = field(
        default_factory=list
    )  # Which tools were used (in order)
    outcome: str = "success"  # "success", "partial", "failed"
    reasoning_trace: list[dict] = field(default_factory=list)  # Full chain of reasoning
    context_snapshot: dict = field(
        default_factory=dict
    )  # Relevant context at time of execution
    confidence: float = 0.8
    tags: list[str] = field(default_factory=list)  # Semantic tags for search
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    access_count: int = 0  # How often this memory has been reused
    last_accessed_at: Optional[datetime] = None

    def matches_query(self, query: str) -> float:
        """Compute relevance score for a search query."""
        query_tokens = set(query.lower().replace("_", " ").split())
        if not query_tokens:
            return 0.0

        # Build searchable text
        searchable = f"{self.goal} {' '.join(self.tools_used)} {' '.join(self.tags)} {self.memory_type}"
        searchable_tokens = set(searchable.lower().replace("_", " ").split())

        matched = query_tokens & searchable_tokens
        if not matched:
            # Substring fallback
            score = 0.0
            for qt in query_tokens:
                for st in searchable_tokens:
                    if qt in st or st in qt:
                        score += 0.2
            return min(score / max(len(query_tokens), 1), 0.4)

        base_score = len(matched) / len(query_tokens)

        # Boost successful memories
        if self.outcome == "success":
            base_score *= 1.2

        # Boost frequently accessed memories
        if self.access_count > 5:
            base_score *= 1.1

        return min(base_score, 1.0)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "memory_type": self.memory_type,
            "goal": self.goal,
            "tools_used": self.tools_used,
            "outcome": self.outcome,
            "confidence": self.confidence,
            "tags": self.tags,
            "access_count": self.access_count,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryEntry":
        """Reconstruct from serialized dict."""
        created_str = data.get("created_at", "")
        last_str = data.get("last_accessed_at", "")
        return cls(
            id=data["id"],
            memory_type=data.get("memory_type", ""),
            goal=data.get("goal", ""),
            tools_used=data.get("tools_used", []),
            outcome=data.get("outcome", "success"),
            reasoning_trace=data.get("reasoning_trace", []),
            context_snapshot=data.get("context_snapshot", {}),
            confidence=data.get("confidence", 0.8),
            tags=data.get("tags", []),
            created_at=(
                datetime.fromisoformat(created_str)
                if created_str
                else datetime.now(timezone.utc)
            ),
            access_count=data.get("access_count", 0),
            last_accessed_at=datetime.fromisoformat(last_str) if last_str else None,
        )


class AgentMemory:
    """
    Persistent memory for agent learning.

    Stores successful tool sequences, recovery strategies, and workflow
    patterns. Enables agents to learn from past executions and make
    better decisions over time.
    """

    def __init__(self, persistence_path: Optional[str] = None, max_entries: int = 1000):
        self._memories: dict[str, MemoryEntry] = {}
        self._max_entries = max_entries
        self._persistence_path = persistence_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "data",
            "memory.json",
        )
        self._load()

    # -------------------------------------------------------------------
    # Core Operations
    # -------------------------------------------------------------------

    def store(self, entry: MemoryEntry) -> None:
        """Store a memory entry."""
        self._memories[entry.id] = entry

        # Prune if over limit
        if len(self._memories) > self._max_entries:
            self._prune_oldest(len(self._memories) - self._max_entries)

        self._save()
        logger.debug(
            f"Memory stored: {entry.id} (type={entry.memory_type}, goal='{entry.goal[:50]}')"
        )

    def recall(
        self,
        query: str,
        memory_type: Optional[str] = None,
        top_k: int = 5,
        min_score: float = 0.1,
    ) -> list[MemoryEntry]:
        """
        Recall memories matching a query.

        Returns the top-k most relevant memories, sorted by relevance.
        """
        candidates = list(self._memories.values())

        if memory_type:
            candidates = [m for m in candidates if m.memory_type == memory_type]

        scored = []
        for mem in candidates:
            score = mem.matches_query(query)
            if score >= min_score:
                scored.append((score, mem))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for _, mem in scored[:top_k]:
            mem.access_count += 1
            mem.last_accessed_at = datetime.now(timezone.utc)
            results.append(mem)

        return results

    def recall_tool_sequence(self, goal: str, top_k: int = 3) -> list[list[str]]:
        """
        Recall past successful tool sequences for a similar goal.

        Returns ordered lists of tool names that succeeded before.
        """
        memories = self.recall(
            query=goal,
            memory_type="tool_sequence",
            top_k=top_k,
        )
        return [m.tools_used for m in memories if m.outcome == "success"]

    def recall_recovery_strategy(
        self, error_type: str, top_k: int = 3
    ) -> list[MemoryEntry]:
        """Recall past successful recovery strategies for similar errors."""
        return self.recall(
            query=error_type,
            memory_type="recovery_strategy",
            top_k=top_k,
        )

    # -------------------------------------------------------------------
    # Convenience Storage Methods
    # -------------------------------------------------------------------

    def store_tool_sequence(
        self,
        goal: str,
        tools_used: list[str],
        outcome: str,
        reasoning_trace: list[dict] = None,
        confidence: float = 0.8,
        context: dict = None,
    ) -> str:
        """Store a tool execution sequence as a memory."""
        memory_id = self._make_id(f"tool_seq:{goal}:{':'.join(tools_used)}")

        # Update existing memory if it exists
        existing = self._memories.get(memory_id)
        if existing:
            existing.access_count += 1
            existing.last_accessed_at = datetime.now(timezone.utc)
            if outcome == "success":
                existing.confidence = min(1.0, existing.confidence + 0.05)
            else:
                existing.confidence = max(0.0, existing.confidence - 0.1)
            self._save()
            return memory_id

        entry = MemoryEntry(
            id=memory_id,
            memory_type="tool_sequence",
            goal=goal,
            tools_used=tools_used,
            outcome=outcome,
            reasoning_trace=reasoning_trace or [],
            context_snapshot=self._trim_context(context or {}),
            confidence=confidence,
            tags=self._extract_tags(goal, tools_used),
        )
        self.store(entry)
        return memory_id

    def store_recovery_strategy(
        self,
        error: str,
        original_tool: str,
        recovery_action: str,
        alternative_tool: Optional[str],
        outcome: str,
        confidence: float = 0.6,
    ) -> str:
        """Store a recovery strategy as a memory."""
        memory_id = self._make_id(
            f"recovery:{error[:50]}:{recovery_action}:{alternative_tool}"
        )

        entry = MemoryEntry(
            id=memory_id,
            memory_type="recovery_strategy",
            goal=f"Recover from: {error[:100]}",
            tools_used=[t for t in [original_tool, alternative_tool] if t],
            outcome=outcome,
            context_snapshot={
                "error": error,
                "original_tool": original_tool,
                "recovery_action": recovery_action,
                "alternative_tool": alternative_tool,
            },
            confidence=confidence,
            tags=self._extract_tags(error, [original_tool, recovery_action]),
        )
        self.store(entry)
        return memory_id

    def store_workflow_pattern(
        self,
        goal: str,
        workflow_type: str,
        task_sequence: list[str],
        outcome: str,
        confidence: float = 0.8,
    ) -> str:
        """Store a workflow pattern as a memory."""
        memory_id = self._make_id(f"workflow:{goal}:{workflow_type}")

        entry = MemoryEntry(
            id=memory_id,
            memory_type="workflow_pattern",
            goal=goal,
            tools_used=task_sequence,
            outcome=outcome,
            context_snapshot={"workflow_type": workflow_type},
            confidence=confidence,
            tags=self._extract_tags(goal, task_sequence) + [workflow_type],
        )
        self.store(entry)
        return memory_id

    # -------------------------------------------------------------------
    # Maintenance
    # -------------------------------------------------------------------

    def update_outcome(self, memory_id: str, outcome: str) -> None:
        """Update the outcome of an existing memory."""
        mem = self._memories.get(memory_id)
        if mem:
            mem.outcome = outcome
            if outcome == "success":
                mem.confidence = min(1.0, mem.confidence + 0.1)
            else:
                mem.confidence = max(0.0, mem.confidence - 0.15)
            self._save()

    def prune_stale(self, max_age_days: int = 30) -> int:
        """Remove old, unused memories."""
        cutoff = datetime.now(timezone.utc)
        pruned = 0
        to_remove = []
        for mem_id, mem in self._memories.items():
            age = (cutoff - mem.created_at).days
            if age > max_age_days and mem.access_count < 2:
                to_remove.append(mem_id)

        for mem_id in to_remove:
            del self._memories[mem_id]
            pruned += 1

        if pruned:
            self._save()
            logger.info(f"Pruned {pruned} stale memories")

        return pruned

    def get_stats(self) -> dict:
        """Get memory system statistics."""
        by_type: dict[str, int] = {}
        by_outcome: dict[str, int] = {}
        for mem in self._memories.values():
            by_type[mem.memory_type] = by_type.get(mem.memory_type, 0) + 1
            by_outcome[mem.outcome] = by_outcome.get(mem.outcome, 0) + 1

        return {
            "total_memories": len(self._memories),
            "by_type": by_type,
            "by_outcome": by_outcome,
            "max_entries": self._max_entries,
        }

    # -------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------

    def _save(self) -> None:
        """Persist memories to JSON file."""
        try:
            os.makedirs(os.path.dirname(self._persistence_path), exist_ok=True)
            data = {}
            for mem_id, mem in self._memories.items():
                data[mem_id] = {
                    "id": mem.id,
                    "memory_type": mem.memory_type,
                    "goal": mem.goal,
                    "tools_used": mem.tools_used,
                    "outcome": mem.outcome,
                    "reasoning_trace": mem.reasoning_trace,
                    "context_snapshot": mem.context_snapshot,
                    "confidence": mem.confidence,
                    "tags": mem.tags,
                    "created_at": mem.created_at.isoformat(),
                    "access_count": mem.access_count,
                    "last_accessed_at": (
                        mem.last_accessed_at.isoformat()
                        if mem.last_accessed_at
                        else None
                    ),
                }
            with open(self._persistence_path, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"Failed to persist memories: {e}")

    def _load(self) -> None:
        """Load memories from JSON file."""
        if not os.path.exists(self._persistence_path):
            return
        try:
            with open(self._persistence_path, "r") as f:
                data = json.load(f)
            for mem_id, mem_data in data.items():
                self._memories[mem_id] = MemoryEntry.from_dict(mem_data)
            logger.info(
                f"Loaded {len(self._memories)} memories from {self._persistence_path}"
            )
        except Exception as e:
            logger.warning(f"Failed to load memories: {e}")

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _prune_oldest(self, count: int) -> None:
        """Remove the oldest, least-accessed memories."""
        sorted_mems = sorted(
            self._memories.items(),
            key=lambda x: (x[1].access_count, x[1].created_at),
        )
        for mem_id, _ in sorted_mems[:count]:
            del self._memories[mem_id]

    @staticmethod
    def _make_id(key: str) -> str:
        """Generate a deterministic memory ID."""
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    @staticmethod
    def _extract_tags(text: str, items: list) -> list[str]:
        """Extract semantic tags for search."""
        combined = f"{text} {' '.join(str(i) for i in items if i)}"
        tokens = set()
        for sep in ["_", "-", " ", ".", "/"]:
            tokens.update(combined.lower().split(sep))
        stop_words = {
            "a",
            "an",
            "the",
            "to",
            "for",
            "of",
            "in",
            "on",
            "is",
            "and",
            "or",
            "with",
            "none",
            "",
        }
        return [t.strip() for t in tokens if len(t) > 2 and t not in stop_words]

    @staticmethod
    def _trim_context(context: dict, max_keys: int = 10) -> dict:
        """Trim context to prevent memory bloat."""
        if len(context) <= max_keys:
            return context
        keys = list(context.keys())[:max_keys]
        return {k: context[k] for k in keys}


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_memory: Optional[AgentMemory] = None


def get_agent_memory() -> AgentMemory:
    """Get the global agent memory singleton."""
    global _memory
    if _memory is None:
        _memory = AgentMemory()
    return _memory
