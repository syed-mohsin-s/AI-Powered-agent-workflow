"""
Sentinel-AI Workflow Execution Engine.

The core engine that orchestrates DAG-based workflow execution,
dispatching task groups to agents with parallel execution support.
"""

import asyncio
import random
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sentinel_ai.config import get_config
from sentinel_ai.core.dag import WorkflowDAG, ExecutionGroup
from sentinel_ai.core.event_bus import Event, EventType, get_event_bus
from sentinel_ai.core.scheduler import get_scheduler
from sentinel_ai.core.state import transition_task, transition_workflow, InvalidTransitionError
from sentinel_ai.models.audit import create_audit_record
from sentinel_ai.models.workflow import (
    TaskDefinition, TaskResult, TaskStatus,
    WorkflowExecution, WorkflowStatus,
)
from sentinel_ai.utils.logger import get_logger, set_workflow_context
from sentinel_ai.utils.metrics import get_metrics

logger = get_logger("engine")


class WorkflowEngine:
    """
    Central workflow execution engine.
    
    Responsibilities:
    - Build DAGs from workflow definitions
    - Execute task groups (parallel via asyncio.gather)
    - Track per-task state transitions
    - Enforce retry limits and timeouts
    - Emit events on all state changes
    - Integrate with Supervisor for health checks
    """

    def __init__(self):
        self._config = get_config()
        self._event_bus = get_event_bus()
        self._scheduler = get_scheduler()
        self._metrics = get_metrics()
        self._agent_registry: dict[str, Callable] = {}
        self._active_workflows: dict[str, WorkflowExecution] = {}
        self._workflow_dags: dict[str, WorkflowDAG] = {}

    def register_agent(self, agent_type: str, execute_fn: Callable) -> None:
        """Register an agent's execution function for task dispatch."""
        self._agent_registry[agent_type] = execute_fn
        logger.info(f"Agent registered: {agent_type}")

    def get_active_workflows(self) -> dict[str, WorkflowExecution]:
        """Get all currently active workflows."""
        return self._active_workflows

    def get_workflow(self, workflow_id: str) -> Optional[WorkflowExecution]:
        """Get a workflow by ID."""
        return self._active_workflows.get(workflow_id)

    async def submit_workflow(
        self,
        workflow: WorkflowExecution,
        tasks: list[TaskDefinition],
        sla_minutes: Optional[int] = None,
    ) -> str:
        """
        Submit a workflow for execution.
        
        1. Build the DAG
        2. Register SLA tracking
        3. Start execution
        
        Returns the workflow ID.
        """
        set_workflow_context(workflow.id)

        # Build DAG
        dag = WorkflowDAG()
        dag.add_tasks(tasks)
        execution_groups = dag.build()

        # Store task references in workflow
        workflow.tasks = dag.tasks

        # Register
        self._active_workflows[workflow.id] = workflow
        self._workflow_dags[workflow.id] = dag

        # SLA tracking
        if sla_minutes:
            self._scheduler.register_workflow(workflow, sla_minutes)

        # Emit creation event
        await self._event_bus.publish(Event(
            event_type=EventType.WORKFLOW_CREATED,
            workflow_id=workflow.id,
            data=workflow.get_summary(),
            source="engine",
        ))

        # Create audit record
        create_audit_record(
            agent="engine",
            trigger_event="workflow_submitted",
            context=f"Workflow {workflow.workflow_type} submitted with {len(tasks)} tasks",
            decision="Accept and execute workflow",
            reasoning=f"DAG built successfully with {len(execution_groups)} execution groups",
            confidence=1.0,
            action_taken="Workflow queued for execution",
            prior_state="none",
            resulting_state="created",
            why="Valid workflow submission with all dependencies resolved",
            trade_offs="None - standard workflow acceptance",
        )

        logger.info(
            f"Workflow submitted: {workflow.id} ({workflow.workflow_type})",
            extra_data={
                "workflow_id": workflow.id,
                "type": workflow.workflow_type,
                "tasks": len(tasks),
                "groups": len(execution_groups),
            },
        )

        # Start execution in background
        asyncio.create_task(self._execute_workflow(workflow.id))

        return workflow.id

    async def _execute_workflow(self, workflow_id: str) -> None:
        """Execute a workflow through its DAG execution groups."""
        workflow = self._active_workflows.get(workflow_id)
        if not workflow:
            return

        dag = self._workflow_dags.get(workflow_id)
        if not dag:
            return

        try:
            # Transition to RUNNING
            workflow.status = transition_workflow(workflow.status, WorkflowStatus.RUNNING)
            workflow.started_at = datetime.now(timezone.utc)

            await self._event_bus.publish(Event(
                event_type=EventType.WORKFLOW_STARTED,
                workflow_id=workflow_id,
                data=workflow.get_summary(),
                source="engine",
            ))

            # Execute each group sequentially; tasks within a group run in parallel
            execution_groups = dag.get_execution_order()

            for group in execution_groups:
                if workflow.status != WorkflowStatus.RUNNING:
                    break  # Workflow was paused or cancelled

                await self._execute_group(workflow, group)

                # Check for critical failures after each group
                if workflow.has_failures():
                    # Try recovery for failed tasks
                    recovered = await self._attempt_recovery(workflow)
                    if not recovered:
                        workflow.status = transition_workflow(
                            workflow.status, WorkflowStatus.FAILED
                        )
                        workflow.error_message = "One or more tasks failed permanently"
                        break

            # Determine final state
            if workflow.status == WorkflowStatus.RUNNING:
                if workflow.is_complete():
                    workflow.status = transition_workflow(
                        workflow.status, WorkflowStatus.COMPLETED
                    )
                    workflow.completed_at = datetime.now(timezone.utc)

                    # Check SLA
                    tracker = self._scheduler.get_tracker(workflow_id)
                    if tracker:
                        workflow.sla_met = not tracker.is_breached
                        self._metrics.record_sla_outcome(workflow.sla_met)

                    # Collect output from all tasks
                    workflow.output_data = {
                        task_id: task.result.output_data
                        for task_id, task in workflow.tasks.items()
                        if task.result and task.result.output_data
                    }

                    # Record workflow duration
                    if workflow.started_at:
                        duration = (workflow.completed_at - workflow.started_at).total_seconds()
                        self._metrics.record_workflow_duration(workflow.workflow_type, duration)

            # Publish final event
            final_event = (
                EventType.WORKFLOW_COMPLETED if workflow.status == WorkflowStatus.COMPLETED
                else EventType.WORKFLOW_FAILED
            )
            await self._event_bus.publish(Event(
                event_type=final_event,
                workflow_id=workflow_id,
                data=workflow.get_summary(),
                source="engine",
            ))

            create_audit_record(
                agent="engine",
                trigger_event="workflow_completed",
                context=f"Workflow {workflow_id} finished",
                decision=f"Workflow {workflow.status.value}",
                reasoning=f"All {len(workflow.tasks)} tasks processed",
                confidence=1.0,
                action_taken=f"Workflow marked as {workflow.status.value}",
                prior_state="running",
                resulting_state=workflow.status.value,
                why=f"Workflow execution completed with status: {workflow.status.value}",
                trade_offs="None",
            )

        except Exception as e:
            logger.error(f"Workflow execution error: {e}", exc_info=True)
            try:
                workflow.status = transition_workflow(workflow.status, WorkflowStatus.FAILED)
            except InvalidTransitionError:
                workflow.status = WorkflowStatus.FAILED
            workflow.error_message = str(e)
            workflow.completed_at = datetime.now(timezone.utc)

            await self._event_bus.publish(Event(
                event_type=EventType.WORKFLOW_FAILED,
                workflow_id=workflow_id,
                data={"error": str(e), **workflow.get_summary()},
                source="engine",
            ))

        finally:
            self._scheduler.unregister_workflow(workflow_id)

    async def _execute_group(
        self, workflow: WorkflowExecution, group: ExecutionGroup
    ) -> None:
        """Execute a group of tasks in parallel."""
        tasks_to_run = []
        for task_id in group.task_ids:
            task = workflow.tasks.get(task_id)
            if task and task.status == TaskStatus.PENDING:
                tasks_to_run.append(task)

        if not tasks_to_run:
            return

        logger.info(
            f"Executing group depth={group.depth}: {len(tasks_to_run)} tasks in parallel",
            extra_data={"depth": group.depth, "tasks": [t.id for t in tasks_to_run]},
        )

        max_parallel = self._get_group_parallel_limit(len(tasks_to_run))

        # Execute tasks with bounded concurrency
        if max_parallel >= len(tasks_to_run):
            await asyncio.gather(
                *(self._execute_task(workflow, task) for task in tasks_to_run),
                return_exceptions=True,
            )
            return

        semaphore = asyncio.Semaphore(max_parallel)

        async def _run_with_limit(task: TaskDefinition) -> None:
            async with semaphore:
                await self._execute_task(workflow, task)

        await asyncio.gather(
            *(_run_with_limit(task) for task in tasks_to_run),
            return_exceptions=True,
        )

    def _get_group_parallel_limit(self, group_size: int) -> int:
        """Resolve bounded parallelism for a group using configuration."""
        configured = int(getattr(self._config.agents, "max_parallel_tasks", 10) or 10)
        if configured <= 0:
            configured = 1
        return min(group_size, configured)

    async def _execute_task(
        self, workflow: WorkflowExecution, task: TaskDefinition
    ) -> None:
        """Execute a single task with retry logic and timeout."""
        agent_fn = self._agent_registry.get(task.agent_type)
        if not agent_fn:
            task.status = TaskStatus.FAILED
            task.result = TaskResult(
                success=False,
                error_message=f"No agent registered for type: {task.agent_type}",
            )
            self._metrics.record_task_completion(False)
            return

        # Transition to RUNNING
        task.status = transition_task(task.status, TaskStatus.QUEUED)
        task.status = transition_task(task.status, TaskStatus.RUNNING)
        task.started_at = datetime.now(timezone.utc)
        task.attempt_count += 1

        await self._event_bus.publish(Event(
            event_type=EventType.TASK_STARTED,
            workflow_id=workflow.id,
            data={"task_id": task.id, "task_name": task.name, "agent": task.agent_type, "attempt": task.attempt_count},
            source="engine",
        ))

        start_time = time.time()

        try:
            # Build task context
            context = {
                "workflow_id": workflow.id,
                "workflow_type": workflow.workflow_type,
                "task_id": task.id,
                "task_name": task.name,
                "input_data": {**workflow.input_data, **task.input_data},
                "shared_context": workflow.shared_context,
                "attempt": task.attempt_count,
            }

            # Execute with timeout
            result = await asyncio.wait_for(
                agent_fn(context),
                timeout=task.timeout_seconds,
            )

            duration = time.time() - start_time

            if isinstance(result, TaskResult):
                task.result = result
            else:
                task.result = TaskResult(
                    success=True,
                    output_data=result if isinstance(result, dict) else {"result": result},
                    duration_seconds=duration,
                )

            task.result.duration_seconds = duration

            if task.result.success:
                task.status = transition_task(task.status, TaskStatus.SUCCESS)
                task.completed_at = datetime.now(timezone.utc)

                # Update shared context with task output
                workflow.shared_context[task.id] = task.result.output_data

                self._metrics.record_task_completion(True)
                self._metrics.record_agent_execution(task.agent_type, duration, True)
                self._metrics.record_autonomous_decision()

                await self._event_bus.publish(Event(
                    event_type=EventType.TASK_COMPLETED,
                    workflow_id=workflow.id,
                    data={
                        "task_id": task.id,
                        "task_name": task.name,
                        "agent": task.agent_type,
                        "confidence": task.result.confidence,
                        "duration": duration,
                    },
                    source="engine",
                ))
            else:
                await self._handle_task_failure(
                    workflow, task, task.result.error_message or "Agent returned failure"
                )

        except asyncio.TimeoutError:
            duration = time.time() - start_time
            await self._handle_task_failure(
                workflow, task, f"Task timed out after {task.timeout_seconds}s"
            )

        except Exception as e:
            duration = time.time() - start_time
            await self._handle_task_failure(workflow, task, str(e))

    async def _handle_task_failure(
        self, workflow: WorkflowExecution, task: TaskDefinition, error: str
    ) -> None:
        """Handle a task failure with retry logic."""
        task.result = TaskResult(
            success=False,
            error_message=error,
        )

        if task.attempt_count < task.max_retries:
            # Retry
            task.status = transition_task(task.status, TaskStatus.RETRYING)

            await self._event_bus.publish(Event(
                event_type=EventType.TASK_RETRYING,
                workflow_id=workflow.id,
                data={
                    "task_id": task.id,
                    "attempt": task.attempt_count,
                    "max_retries": task.max_retries,
                    "error": error,
                },
                source="engine",
            ))

            logger.warning(
                f"Task {task.id} failed (attempt {task.attempt_count}/{task.max_retries}), retrying...",
                extra_data={"task_id": task.id, "error": error},
            )

            # Config-driven exponential backoff with jitter
            backoff = self._compute_retry_backoff(task.attempt_count)
            await asyncio.sleep(backoff)

            # Reset to RUNNING for retry
            task.status = transition_task(task.status, TaskStatus.RUNNING)

            # Re-execute
            await self._execute_task(workflow, task)
        else:
            # Permanently failed
            task.status = transition_task(task.status, TaskStatus.FAILED)
            task.completed_at = datetime.now(timezone.utc)

            self._metrics.record_task_completion(False)
            self._metrics.record_agent_execution(task.agent_type, 0, False)

            await self._event_bus.publish(Event(
                event_type=EventType.TASK_FAILED,
                workflow_id=workflow.id,
                data={
                    "task_id": task.id,
                    "task_name": task.name,
                    "error": error,
                    "attempts": task.attempt_count,
                },
                source="engine",
            ))

            create_audit_record(
                agent=task.agent_type,
                trigger_event="task_failed_permanently",
                context=f"Task {task.name} failed after {task.attempt_count} attempts",
                decision="Mark task as failed",
                reasoning=f"Exhausted all {task.max_retries} retry attempts. Last error: {error}",
                confidence=1.0,
                action_taken="Task marked as permanently failed",
                prior_state="retrying",
                resulting_state="failed",
                status="failed",
                why="All retry attempts exhausted",
                trade_offs="Workflow may fail if this was a critical task",
            )

    def _compute_retry_backoff(self, attempt_count: int) -> float:
        """Compute jittered exponential backoff based on configured retry policy."""
        agents_cfg = self._config.agents
        base = max(0.1, float(getattr(agents_cfg, "retry_base_seconds", 1.0)))
        max_backoff = max(base, float(getattr(agents_cfg, "retry_max_backoff_seconds", 30.0)))
        jitter_ratio = float(getattr(agents_cfg, "retry_jitter_ratio", 0.2))
        jitter_ratio = min(max(jitter_ratio, 0.0), 1.0)

        exponential = min(base * (2 ** max(attempt_count, 1)), max_backoff)
        jitter_window = exponential * jitter_ratio

        if jitter_window <= 0:
            return exponential

        lower_bound = max(0.0, exponential - jitter_window)
        upper_bound = min(max_backoff, exponential + jitter_window)
        return random.uniform(lower_bound, upper_bound)

    async def _attempt_recovery(self, workflow: WorkflowExecution) -> bool:
        """
        Attempt to recover a workflow with failed tasks.
        
        Uses the recovery agent if registered, otherwise returns False.
        """
        recovery_fn = self._agent_registry.get("recovery")
        if not recovery_fn:
            return False

        failed_tasks = [
            t for t in workflow.tasks.values()
            if t.status == TaskStatus.FAILED
        ]

        for task in failed_tasks:
            try:
                context = {
                    "workflow_id": workflow.id,
                    "failed_task": {
                        "id": task.id,
                        "name": task.name,
                        "agent": task.agent_type,
                        "error": task.result.error_message if task.result else "Unknown",
                        "attempts": task.attempt_count,
                    },
                    "shared_context": workflow.shared_context,
                }
                result = await recovery_fn(context)
                if isinstance(result, TaskResult) and result.success:
                    # Recovery succeeded — reset task
                    task.status = TaskStatus.PENDING
                    task.attempt_count = 0
                    task.result = None
                    recovery_time = result.duration_seconds
                    self._metrics.record_recovery(recovery_time)
                    return True
            except Exception as e:
                logger.error(f"Recovery failed for task {task.id}: {e}")

        return False

    async def pause_workflow(self, workflow_id: str) -> bool:
        """Pause a running workflow."""
        workflow = self._active_workflows.get(workflow_id)
        if not workflow or workflow.status != WorkflowStatus.RUNNING:
            return False
        workflow.status = transition_workflow(workflow.status, WorkflowStatus.PAUSED)
        await self._event_bus.publish(Event(
            event_type=EventType.WORKFLOW_PAUSED,
            workflow_id=workflow_id,
            data=workflow.get_summary(),
            source="engine",
        ))
        return True

    async def resume_workflow(self, workflow_id: str) -> bool:
        """Resume a paused workflow."""
        workflow = self._active_workflows.get(workflow_id)
        if not workflow or workflow.status != WorkflowStatus.PAUSED:
            return False
        workflow.status = transition_workflow(workflow.status, WorkflowStatus.RUNNING)
        await self._event_bus.publish(Event(
            event_type=EventType.WORKFLOW_RESUMED,
            workflow_id=workflow_id,
            data=workflow.get_summary(),
            source="engine",
        ))
        # Re-execute remaining tasks
        asyncio.create_task(self._execute_workflow(workflow_id))
        return True

    async def cancel_workflow(self, workflow_id: str) -> bool:
        """Cancel a workflow."""
        workflow = self._active_workflows.get(workflow_id)
        if not workflow:
            return False
        try:
            workflow.status = transition_workflow(workflow.status, WorkflowStatus.FAILED)
        except InvalidTransitionError:
            workflow.status = WorkflowStatus.FAILED
        workflow.error_message = "Cancelled by user"
        workflow.completed_at = datetime.now(timezone.utc)
        self._scheduler.unregister_workflow(workflow_id)
        return True


# Global engine singleton
_engine: Optional[WorkflowEngine] = None


def get_engine() -> WorkflowEngine:
    global _engine
    if _engine is None:
        _engine = WorkflowEngine()
    return _engine
