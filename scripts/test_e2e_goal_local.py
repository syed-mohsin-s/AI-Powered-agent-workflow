"""Run an end-to-end goal-driven workflow using the local engine (no HTTP)."""

import asyncio
import json
import time

from sentinel_ai.core.engine import get_engine
from sentinel_ai.workflows.goal_workflow import create_goal_workflow

# Let's manually register agents if main.py fails to import due to fastapi
def _register_agents():
    from sentinel_ai.core.engine import get_engine
    from sentinel_ai.agents.orchestrator import OrchestratorAgent
    from sentinel_ai.agents.supervisor import SupervisorAgent
    from sentinel_ai.agents.intake import IntakeAgent
    from sentinel_ai.agents.policy import PolicyAgent
    from sentinel_ai.agents.decision import DecisionAgent
    from sentinel_ai.agents.execution import ExecutionAgent
    from sentinel_ai.agents.verification import VerificationAgent
    from sentinel_ai.agents.monitoring import MonitoringAgent
    from sentinel_ai.agents.recovery import RecoveryAgent
    from sentinel_ai.agents.reliability_guard import ReliabilityGuardAgent
    from sentinel_ai.agents.planner import PlannerAgent
    
    agents_map = {
        "orchestrator": OrchestratorAgent(),
        "supervisor": SupervisorAgent(),
        "intake": IntakeAgent(),
        "policy": PolicyAgent(),
        "decision": DecisionAgent(),
        "execution": ExecutionAgent(),
        "verification": VerificationAgent(),
        "monitoring": MonitoringAgent(),
        "recovery": RecoveryAgent(),
        "reliability_guard": ReliabilityGuardAgent(),
        "planner": PlannerAgent(),
    }
    engine = get_engine()
    for name, agent in agents_map.items():
        engine.register_agent(name, agent)
        
    # Register mock tools
    from sentinel_ai.core.tool_registry import get_tool_registry
    from sentinel_ai.integrations.mock_adapters import ERPAdapter, EmailAdapter, ServiceNowAdapter
    registry = get_tool_registry()
    for adapter in [ERPAdapter(), EmailAdapter(), ServiceNowAdapter()]:
        for cap, fn in adapter.get_tool_capabilities():
            registry.register_tool(cap, fn)

async def main():
    _register_agents()
    
    engine = get_engine()
    
    goal = "Process Acme Corp invoice INV-2026-001 for $15000 and create a Jira ticket for tracking"
    print(f"Goal: {goal}")
    
    workflow, tasks = create_goal_workflow(
        goal=goal,
        constraints={"time_limit_minutes": 30},
        priority=5,
    )
    
    workflow_id = await engine.submit_workflow(workflow, tasks, sla_minutes=60)
    print(f"Workflow {workflow_id} submitted.")
    
    # Wait for completion
    timeout = 60
    deadline = time.time() + timeout
    
    while time.time() < deadline:
        wf = engine.get_workflow(workflow_id)
        status = wf.status.value
        task_count = len(wf.tasks)
        completed = sum(1 for t in wf.tasks.values() if t.status.value == "completed")
        print(f"Status: {status} | Tasks: {completed}/{task_count}")
        
        if status in {"completed", "failed", "escalated"}:
            break
            
        await asyncio.sleep(2)
        
    wf = engine.get_workflow(workflow_id)
    print("\n--- Final Workflow State ---")
    print(f"Status: {wf.status.value}")
    print(f"Total Tasks: {len(wf.tasks)}")
    
    for tid, task in wf.tasks.items():
        print(f"  - {task.name} ({task.agent_type}): {task.status.value}")
        if task.result and getattr(task.result, "tool_used", None):
            print(f"    Tool Used: {task.result.tool_used}")
            print(f"    Reasoning: {task.result.reasoning}")

if __name__ == "__main__":
    asyncio.run(main())
