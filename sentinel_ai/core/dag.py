"""
Sentinel-AI DAG Builder.

Constructs directed acyclic graphs from workflow templates,
performs topological sorting, and detects parallel execution groups.
"""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

from sentinel_ai.models.workflow import TaskDefinition, TaskStatus
from sentinel_ai.utils.logger import get_logger

logger = get_logger("dag")


class CycleDetectedError(Exception):
    """Raised when a cycle is detected in the task graph."""
    pass


class DependencyNotFoundError(Exception):
    """Raised when a task references a non-existent dependency."""
    pass


@dataclass
class ExecutionGroup:
    """A group of tasks that can run in parallel (same DAG depth)."""
    depth: int
    task_ids: list[str] = field(default_factory=list)


class WorkflowDAG:
    """
    Directed Acyclic Graph for workflow task orchestration.
    
    Provides:
    - Topological sorting for execution order
    - Parallel group detection (tasks at same depth with no mutual deps)
    - Cycle detection
    - Dependency validation
    """

    def __init__(self):
        self.tasks: dict[str, TaskDefinition] = {}
        self._adjacency: dict[str, list[str]] = defaultdict(list)  # parent -> children
        self._reverse: dict[str, list[str]] = defaultdict(list)    # child -> parents
        self._execution_order: list[ExecutionGroup] = []
        self._built = False

    def add_task(self, task: TaskDefinition) -> None:
        """Add a task to the DAG."""
        self.tasks[task.id] = task
        self._built = False

    def add_tasks(self, tasks: list[TaskDefinition]) -> None:
        """Add multiple tasks to the DAG."""
        for task in tasks:
            self.add_task(task)

    def build(self) -> list[ExecutionGroup]:
        """
        Build the execution plan.
        
        1. Validate all dependencies exist
        2. Detect cycles
        3. Compute topological order
        4. Group tasks by depth for parallel execution
        
        Returns a list of ExecutionGroups ordered by depth.
        """
        self._validate_dependencies()
        self._build_adjacency()
        self._detect_cycles()
        self._compute_depths()
        self._build_execution_groups()
        self._built = True

        logger.info(
            f"DAG built: {len(self.tasks)} tasks, {len(self._execution_order)} execution groups",
            extra_data={
                "total_tasks": len(self.tasks),
                "groups": len(self._execution_order),
                "max_parallelism": max(len(g.task_ids) for g in self._execution_order) if self._execution_order else 0,
            },
        )

        return self._execution_order

    def _validate_dependencies(self) -> None:
        """Ensure all dependency references point to existing tasks."""
        for task_id, task in self.tasks.items():
            for dep_id in task.dependencies:
                if dep_id not in self.tasks:
                    raise DependencyNotFoundError(
                        f"Task '{task_id}' depends on '{dep_id}' which does not exist"
                    )

    def _build_adjacency(self) -> None:
        """Build adjacency lists from task dependencies."""
        self._adjacency.clear()
        self._reverse.clear()
        for task_id, task in self.tasks.items():
            for dep_id in task.dependencies:
                self._adjacency[dep_id].append(task_id)
                self._reverse[task_id].append(dep_id)

    def _detect_cycles(self) -> None:
        """Detect cycles using DFS coloring (white/gray/black)."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {tid: WHITE for tid in self.tasks}
        path: list[str] = []

        def dfs(node: str) -> None:
            color[node] = GRAY
            path.append(node)
            for neighbor in self._adjacency.get(node, []):
                if color[neighbor] == GRAY:
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:] + [neighbor]
                    raise CycleDetectedError(
                        f"Cycle detected in task graph: {' → '.join(cycle)}"
                    )
                if color[neighbor] == WHITE:
                    dfs(neighbor)
            path.pop()
            color[node] = BLACK

        for task_id in self.tasks:
            if color[task_id] == WHITE:
                dfs(task_id)

    def _compute_depths(self) -> None:
        """Compute DAG depth for each task using BFS from roots."""
        in_degree = {tid: len(task.dependencies) for tid, task in self.tasks.items()}
        queue = deque()

        # Start with root nodes (no dependencies)
        for tid, degree in in_degree.items():
            if degree == 0:
                self.tasks[tid].dag_depth = 0
                queue.append(tid)

        while queue:
            current = queue.popleft()
            for child in self._adjacency.get(current, []):
                # Child's depth = max depth of all parents + 1
                parent_depth = self.tasks[current].dag_depth
                self.tasks[child].dag_depth = max(
                    self.tasks[child].dag_depth,
                    parent_depth + 1,
                )
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

    def _build_execution_groups(self) -> None:
        """Group tasks by depth for parallel execution."""
        depth_groups: dict[int, list[str]] = defaultdict(list)
        for task_id, task in self.tasks.items():
            depth_groups[task.dag_depth].append(task_id)

        self._execution_order = [
            ExecutionGroup(depth=depth, task_ids=sorted(task_ids))
            for depth, task_ids in sorted(depth_groups.items())
        ]

    def get_execution_order(self) -> list[ExecutionGroup]:
        """Get the computed execution order (builds DAG if not already built)."""
        if not self._built:
            self.build()
        return self._execution_order

    def get_task(self, task_id: str) -> Optional[TaskDefinition]:
        """Get a task by ID."""
        return self.tasks.get(task_id)

    def get_dependents(self, task_id: str) -> list[str]:
        """Get tasks that depend on the given task."""
        return self._adjacency.get(task_id, [])

    def get_dependencies(self, task_id: str) -> list[str]:
        """Get tasks that the given task depends on."""
        return self._reverse.get(task_id, [])

    def to_dict(self) -> dict:
        """Export DAG as a serializable dictionary for visualization."""
        nodes = []
        edges = []
        
        for task_id, task in self.tasks.items():
            nodes.append({
                "id": task_id,
                "name": task.name,
                "agent": task.agent_type,
                "status": task.status.value,
                "depth": task.dag_depth,
                "priority": task.priority,
            })
            for dep in task.dependencies:
                edges.append({"from": dep, "to": task_id})
        
        return {"nodes": nodes, "edges": edges}
