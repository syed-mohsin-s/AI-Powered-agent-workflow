"""Tests for the DAG engine and core workflow execution."""

import asyncio
import pytest
from sentinel_ai.core.dag import WorkflowDAG, CycleDetectedError, DependencyNotFoundError
from sentinel_ai.core.dag import ExecutionGroup
from sentinel_ai.core.state import (
    transition_workflow, transition_task, InvalidTransitionError,
    validate_workflow_transition, validate_task_transition,
)
from sentinel_ai.core.event_bus import EventBus, Event, EventType
from sentinel_ai.core.engine import WorkflowEngine
from sentinel_ai.models.workflow import TaskDefinition, WorkflowExecution, TaskStatus, WorkflowStatus


class TestDAG:
    """Test the DAG builder."""

    def test_simple_dag(self):
        dag = WorkflowDAG()
        dag.add_tasks([
            TaskDefinition(id="a", name="A", agent_type="intake"),
            TaskDefinition(id="b", name="B", agent_type="policy", dependencies=["a"]),
            TaskDefinition(id="c", name="C", agent_type="execution", dependencies=["b"]),
        ])
        groups = dag.build()
        assert len(groups) == 3
        assert groups[0].task_ids == ["a"]
        assert groups[1].task_ids == ["b"]
        assert groups[2].task_ids == ["c"]

    def test_parallel_group(self):
        dag = WorkflowDAG()
        dag.add_tasks([
            TaskDefinition(id="a", name="A", agent_type="intake"),
            TaskDefinition(id="b", name="B", agent_type="policy", dependencies=["a"]),
            TaskDefinition(id="c", name="C", agent_type="execution", dependencies=["a"]),
            TaskDefinition(id="d", name="D", agent_type="verification", dependencies=["b", "c"]),
        ])
        groups = dag.build()
        assert len(groups) == 3
        # b and c should be in the same group (parallel)
        assert set(groups[1].task_ids) == {"b", "c"}

    def test_cycle_detection(self):
        dag = WorkflowDAG()
        dag.add_tasks([
            TaskDefinition(id="a", name="A", agent_type="intake", dependencies=["c"]),
            TaskDefinition(id="b", name="B", agent_type="policy", dependencies=["a"]),
            TaskDefinition(id="c", name="C", agent_type="execution", dependencies=["b"]),
        ])
        with pytest.raises(CycleDetectedError):
            dag.build()

    def test_missing_dependency(self):
        dag = WorkflowDAG()
        dag.add_tasks([
            TaskDefinition(id="a", name="A", agent_type="intake", dependencies=["nonexistent"]),
        ])
        with pytest.raises(DependencyNotFoundError):
            dag.build()

    def test_single_task(self):
        dag = WorkflowDAG()
        dag.add_task(TaskDefinition(id="a", name="A", agent_type="intake"))
        groups = dag.build()
        assert len(groups) == 1
        assert groups[0].task_ids == ["a"]

    def test_dag_to_dict(self):
        dag = WorkflowDAG()
        dag.add_tasks([
            TaskDefinition(id="a", name="A", agent_type="intake"),
            TaskDefinition(id="b", name="B", agent_type="policy", dependencies=["a"]),
        ])
        dag.build()
        data = dag.to_dict()
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1


class TestStateMachine:
    """Test workflow and task state transitions."""

    def test_valid_workflow_transition(self):
        result = transition_workflow(WorkflowStatus.CREATED, WorkflowStatus.RUNNING)
        assert result == WorkflowStatus.RUNNING

    def test_invalid_workflow_transition(self):
        with pytest.raises(InvalidTransitionError):
            transition_workflow(WorkflowStatus.COMPLETED, WorkflowStatus.RUNNING)

    def test_valid_task_transition(self):
        result = transition_task(TaskStatus.PENDING, TaskStatus.QUEUED)
        assert result == TaskStatus.QUEUED

    def test_task_retry_flow(self):
        state = transition_task(TaskStatus.PENDING, TaskStatus.QUEUED)
        state = transition_task(state, TaskStatus.RUNNING)
        state = transition_task(state, TaskStatus.FAILED)
        state = transition_task(state, TaskStatus.RETRYING)
        state = transition_task(state, TaskStatus.RUNNING)
        state = transition_task(state, TaskStatus.SUCCESS)
        assert state == TaskStatus.SUCCESS


class TestEventBus:
    """Test the async event bus."""

    @pytest.mark.asyncio
    async def test_publish_subscribe(self):
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe(EventType.WORKFLOW_CREATED, handler)
        await bus.publish(Event(event_type=EventType.WORKFLOW_CREATED, data={"test": True}))

        assert len(received) == 1
        assert received[0].data["test"] is True

    @pytest.mark.asyncio
    async def test_wildcard_subscription(self):
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("workflow.*", handler)
        await bus.publish(Event(event_type=EventType.WORKFLOW_CREATED))
        await bus.publish(Event(event_type=EventType.WORKFLOW_COMPLETED))
        await bus.publish(Event(event_type=EventType.TASK_STARTED))  # Should NOT match

        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_event_history(self):
        bus = EventBus()
        await bus.publish(Event(event_type=EventType.WORKFLOW_CREATED))
        await bus.publish(Event(event_type=EventType.TASK_STARTED))

        history = bus.get_history()
        assert len(history) == 2


class TestWorkflowModel:
    """Test workflow and task models."""

    def test_task_dependency_check(self):
        task = TaskDefinition(id="a", name="A", agent_type="intake", dependencies=["b", "c"])
        assert not task.can_run({"b"})
        assert task.can_run({"b", "c"})

    def test_workflow_completion(self):
        wf = WorkflowExecution(id="test", workflow_type="p2p")
        wf.tasks = {
            "a": TaskDefinition(id="a", name="A", agent_type="intake", status=TaskStatus.SUCCESS),
            "b": TaskDefinition(id="b", name="B", agent_type="policy", status=TaskStatus.SUCCESS),
        }
        assert wf.is_complete()

    def test_workflow_has_failures(self):
        wf = WorkflowExecution(id="test", workflow_type="p2p")
        wf.tasks = {
            "a": TaskDefinition(id="a", name="A", agent_type="intake", status=TaskStatus.SUCCESS),
            "b": TaskDefinition(id="b", name="B", agent_type="policy", status=TaskStatus.FAILED),
        }
        assert wf.has_failures()

    def test_workflow_get_ready_tasks(self):
        wf = WorkflowExecution(id="test", workflow_type="p2p")
        wf.tasks = {
            "a": TaskDefinition(id="a", name="A", agent_type="intake", status=TaskStatus.SUCCESS),
            "b": TaskDefinition(id="b", name="B", agent_type="policy", dependencies=["a"]),
            "c": TaskDefinition(id="c", name="C", agent_type="execution", dependencies=["a"]),
        }
        ready = wf.get_ready_tasks()
        assert len(ready) == 2  # Both b and c are ready since a is complete


class TestRetryBackoff:
    def test_backoff_without_jitter(self):
        engine = WorkflowEngine()
        engine._config.agents.retry_base_seconds = 1.0
        engine._config.agents.retry_max_backoff_seconds = 30.0
        engine._config.agents.retry_jitter_ratio = 0.0

        assert engine._compute_retry_backoff(1) == 2.0
        assert engine._compute_retry_backoff(2) == 4.0

    def test_backoff_respects_max(self):
        engine = WorkflowEngine()
        engine._config.agents.retry_base_seconds = 2.0
        engine._config.agents.retry_max_backoff_seconds = 10.0
        engine._config.agents.retry_jitter_ratio = 0.0

        assert engine._compute_retry_backoff(5) == 10.0


class TestEngineParallelism:
    def test_group_parallel_limit_resolution(self):
        engine = WorkflowEngine()
        engine._config.agents.max_parallel_tasks = 3

        assert engine._get_group_parallel_limit(10) == 3
        assert engine._get_group_parallel_limit(2) == 2

    @pytest.mark.asyncio
    async def test_execute_group_respects_max_parallel_tasks(self):
        engine = WorkflowEngine()
        engine._config.agents.max_parallel_tasks = 2

        current_running = 0
        max_observed = 0
        lock = asyncio.Lock()

        async def fake_agent(_context):
            nonlocal current_running, max_observed
            async with lock:
                current_running += 1
                max_observed = max(max_observed, current_running)
            await asyncio.sleep(0.02)
            async with lock:
                current_running -= 1
            return {"ok": True}

        engine.register_agent("intake", fake_agent)

        workflow = WorkflowExecution(id="wf-parallel", workflow_type="test")
        tasks = {
            f"t{i}": TaskDefinition(id=f"t{i}", name=f"Task {i}", agent_type="intake")
            for i in range(5)
        }
        workflow.tasks = tasks
        group = ExecutionGroup(depth=0, task_ids=list(tasks.keys()))

        await engine._execute_group(workflow, group)

        assert max_observed <= 2
        assert all(task.status == TaskStatus.SUCCESS for task in workflow.tasks.values())
