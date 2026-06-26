"""
Sentinel-AI Planner Agent.

Generates task DAGs from natural language goals at runtime.
Uses LLM to decompose objectives into executable task graphs,
with template-based fallback for known workflow types.
"""

import json
import uuid
from typing import Optional

from sentinel_ai.agents.base import BaseAgent
from sentinel_ai.core.tool_registry import get_tool_registry
from sentinel_ai.core.vector_store import get_vector_store
from sentinel_ai.models.workflow import TaskDefinition, TaskResult
from sentinel_ai.utils.logger import get_logger

logger = get_logger("agents.planner")


class PlannerAgent(BaseAgent):
    """
    Generates task DAGs from user objectives at runtime.

    Capabilities:
    - Decompose natural language goals into ordered task steps
    - Assign agent types and tool preferences per task
    - Determine dependencies and parallelism
    - Use workflow templates as starting points for known workflow types
    - Validate generated plans against DAG constraints
    """

    def __init__(self):
        super().__init__(
            name="Planner Agent",
            agent_type="planner",
        )
        self._tool_registry = get_tool_registry()
        self._templates = self._load_templates()

    async def execute(self, context: dict) -> TaskResult:
        """Generate a workflow plan from a goal or input data."""
        input_data = context.get("input_data", {})
        goal = input_data.get("goal", "")
        workflow_type = context.get(
            "workflow_type", input_data.get("workflow_type", "")
        )
        constraints = input_data.get("constraints", {})
        workflow_id = context.get("workflow_id", str(uuid.uuid4()))

        if not goal and not workflow_type:
            return TaskResult(
                success=False,
                error_message="No goal or workflow_type provided for planning",
                confidence=0.0,
                reasoning="Planner requires either a 'goal' or 'workflow_type' to generate a plan",
            )

        # Get available tools for the LLM prompt
        available_tools = self._tool_registry.get_available_tools()

        # Get matching template if available
        template = self._templates.get(workflow_type) if workflow_type else None

        # Generate the plan
        if self._llm.is_available:
            plan = await self._llm_generate_plan(
                goal=goal,
                workflow_type=workflow_type,
                available_tools=available_tools,
                template=template,
                constraints=constraints,
            )
        else:
            plan = self._template_based_plan(goal, workflow_type, template)

        if not plan or not plan.get("tasks"):
            return TaskResult(
                success=False,
                error_message="Failed to generate a valid plan",
                confidence=0.0,
                reasoning="Plan generation returned no tasks",
            )

        # Validate the plan
        validation = self._validate_plan(plan)
        if not validation["valid"]:
            return TaskResult(
                success=False,
                error_message=f"Plan validation failed: {validation['errors']}",
                output_data={"plan": plan, "validation": validation},
                confidence=0.2,
                reasoning=f"Generated plan failed validation: {', '.join(validation['errors'])}",
            )

        # Convert to TaskDefinition list
        task_definitions = self._plan_to_task_definitions(plan, workflow_id)

        return TaskResult(
            success=True,
            output_data={
                "plan": plan,
                "task_definitions": [
                    {
                        "id": t.id,
                        "name": t.name,
                        "agent_type": t.agent_type,
                        "dependencies": t.dependencies,
                        "input_data": t.input_data,
                        "timeout_seconds": t.timeout_seconds,
                    }
                    for t in task_definitions
                ],
                "task_count": len(task_definitions),
                "planning_method": "llm" if self._llm.is_available else "template",
            },
            confidence=plan.get("confidence", 0.8),
            reasoning=f"Generated plan with {len(task_definitions)} tasks for: {goal or workflow_type}",
            reasoning_trace=[
                {
                    "step": "planning",
                    "goal": goal,
                    "workflow_type": workflow_type,
                    "tasks_generated": len(task_definitions),
                    "method": "llm" if self._llm.is_available else "template",
                }
            ],
        )

    async def _llm_generate_plan(
        self,
        goal: str,
        workflow_type: str,
        available_tools: list,
        template: Optional[dict],
        constraints: dict,
    ) -> dict:
        """Use LLM to generate a task DAG from a goal."""
        tools_summary = self._tool_registry.get_tools_for_llm_prompt(available_tools)

        template_hint = ""
        if template:
            template_hint = f"""
Reference template for '{workflow_type}' workflow:
{json.dumps(template, indent=2)}

You may modify, extend, or deviate from this template based on the goal."""

        constraint_text = ""
        if constraints:
            constraint_text = f"\nConstraints: {json.dumps(constraints, indent=2)}"

        # RAG: retrieve relevant past workflow patterns
        rag_context = ""
        try:
            vs = get_vector_store()
            rag_context = vs.get_rag_context(
                query=goal or workflow_type,
                collections=["workflow_patterns", "tool_sequences"],
                top_k=3,
            )
            if rag_context:
                rag_context = f"\n{rag_context}\n"
        except Exception as e:
            logger.debug(f"Vector store recall failed (non-fatal): {e}")

        prompt = f"""You are a workflow planner. Generate a task execution plan for the following objective.

Goal: {goal or f'Execute a {workflow_type} workflow'}
{constraint_text}
{template_hint}
{rag_context}

Available tools in the system:
{tools_summary}

Generate a plan as a JSON object with:
{{
  "tasks": [
    {{
      "name": "Task Name",
      "description": "What this task does",
      "agent_type": "intake|policy|execution|decision|verification",
      "dependencies": ["names of tasks this depends on"],
      "input_data": {{}},
      "timeout_seconds": 30,
      "tool_hint": "optional: preferred tool name from the available tools"
    }}
  ],
  "reasoning": "Why this plan structure was chosen",
  "confidence": 0.0-1.0,
  "estimated_duration_minutes": 0
}}

Rules:
1. Tasks must form a valid DAG (no cycles)
2. Root tasks (no dependencies) are executed first
3. Tasks with the same dependencies can run in parallel
4. Use agent_type "intake" for data extraction, "policy" for compliance checks, 
   "execution" for external actions, "decision" for merging/resolving, 
   "verification" for final checks
5. Keep the plan minimal — only include necessary steps

Return ONLY valid JSON."""

        try:
            response = await self.llm_analyze(prompt)
            cleaned = response.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                cleaned = "\n".join(lines[1:-1])
            return json.loads(cleaned)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"LLM plan generation failed: {e}")
            return self._template_based_plan(goal, workflow_type, template)

    def _template_based_plan(
        self,
        goal: str,
        workflow_type: str,
        template: Optional[dict] = None,
    ) -> dict:
        """Generate a plan from a template or generic structure."""
        if template:
            return template

        # Generic plan structure for unknown goals
        return {
            "tasks": [
                {
                    "name": "Analyze Input",
                    "description": "Extract and structure input data",
                    "agent_type": "intake",
                    "dependencies": [],
                    "input_data": {"goal": goal},
                    "timeout_seconds": 30,
                },
                {
                    "name": "Validate & Check Policy",
                    "description": "Check compliance and policy requirements",
                    "agent_type": "policy",
                    "dependencies": ["Analyze Input"],
                    "timeout_seconds": 20,
                },
                {
                    "name": "Decide Action",
                    "description": "Determine the best course of action",
                    "agent_type": "decision",
                    "dependencies": ["Validate & Check Policy"],
                    "timeout_seconds": 15,
                },
                {
                    "name": "Execute Action",
                    "description": "Perform the determined action",
                    "agent_type": "execution",
                    "dependencies": ["Decide Action"],
                    "input_data": {"goal": goal},
                    "timeout_seconds": 30,
                },
                {
                    "name": "Verify Result",
                    "description": "Verify the execution outcome",
                    "agent_type": "verification",
                    "dependencies": ["Execute Action"],
                    "timeout_seconds": 10,
                },
            ],
            "reasoning": "Generic workflow plan for unrecognized goal",
            "confidence": 0.6,
            "estimated_duration_minutes": 10,
        }

    def _validate_plan(self, plan: dict) -> dict:
        """Validate that a plan is structurally sound."""
        errors = []
        tasks = plan.get("tasks", [])

        if not tasks:
            errors.append("Plan has no tasks")
            return {"valid": False, "errors": errors}

        # Check for duplicate names
        names = [t.get("name", "") for t in tasks]
        if len(names) != len(set(names)):
            errors.append("Duplicate task names found")

        # Check that all dependencies reference existing tasks
        name_set = set(names)
        for task in tasks:
            for dep in task.get("dependencies", []):
                if dep not in name_set:
                    errors.append(
                        f"Task '{task.get('name')}' depends on unknown task '{dep}'"
                    )

        # Check for cycles (simple DFS)
        adjacency = {}
        for task in tasks:
            name = task.get("name", "")
            adjacency[name] = task.get("dependencies", [])

        visited = set()
        rec_stack = set()

        def has_cycle(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            for dep in adjacency.get(node, []):
                if dep not in visited:
                    if has_cycle(dep):
                        return True
                elif dep in rec_stack:
                    return True
            rec_stack.discard(node)
            return False

        for name in names:
            if name not in visited:
                if has_cycle(name):
                    errors.append("Cycle detected in task dependencies")
                    break

        # Check agent types are valid
        valid_types = {
            "intake",
            "policy",
            "execution",
            "decision",
            "verification",
            "orchestrator",
            "supervisor",
            "monitoring",
            "recovery",
            "reliability_guard",
            "guardrail",
            "planner",
        }
        for task in tasks:
            agent_type = task.get("agent_type", "")
            if agent_type and agent_type not in valid_types:
                errors.append(
                    f"Task '{task.get('name')}' has unknown agent_type '{agent_type}'"
                )

        return {"valid": len(errors) == 0, "errors": errors}

    def _plan_to_task_definitions(
        self, plan: dict, workflow_id: str
    ) -> list[TaskDefinition]:
        """Convert a plan dict to a list of TaskDefinition objects."""
        tasks = plan.get("tasks", [])
        name_to_id = {}

        # First pass: assign IDs
        for i, task in enumerate(tasks):
            name = task.get("name", f"task_{i}")
            task_id = f"{workflow_id}_plan_{name.lower().replace(' ', '_')}"
            name_to_id[name] = task_id

        # Second pass: create TaskDefinitions with resolved dependency IDs
        definitions = []
        for task in tasks:
            name = task.get("name", "")
            task_id = name_to_id[name]
            dep_ids = [
                name_to_id[dep]
                for dep in task.get("dependencies", [])
                if dep in name_to_id
            ]

            input_data = task.get("input_data", {})
            if task.get("tool_hint"):
                input_data["tool_hint"] = task["tool_hint"]

            definitions.append(
                TaskDefinition(
                    id=task_id,
                    name=name,
                    agent_type=task.get("agent_type", "execution"),
                    dependencies=dep_ids,
                    input_data=input_data,
                    timeout_seconds=task.get("timeout_seconds", 30),
                )
            )

        return definitions

    def _load_templates(self) -> dict[str, dict]:
        """Load workflow templates for known workflow types."""
        return {
            "p2p": {
                "tasks": [
                    {
                        "name": "Extract Invoice Data",
                        "agent_type": "intake",
                        "dependencies": [],
                        "input_data": {"type": "invoice"},
                        "timeout_seconds": 30,
                    },
                    {
                        "name": "Validate Compliance",
                        "agent_type": "policy",
                        "dependencies": ["Extract Invoice Data"],
                        "timeout_seconds": 20,
                    },
                    {
                        "name": "Match Purchase Order",
                        "agent_type": "execution",
                        "dependencies": ["Extract Invoice Data"],
                        "input_data": {"action": "match_po", "target_system": "erp"},
                        "timeout_seconds": 15,
                    },
                    {
                        "name": "Resolve Discrepancies",
                        "agent_type": "decision",
                        "dependencies": ["Validate Compliance", "Match Purchase Order"],
                        "timeout_seconds": 15,
                    },
                    {
                        "name": "Execute Payment",
                        "agent_type": "execution",
                        "dependencies": ["Resolve Discrepancies"],
                        "input_data": {
                            "action": "create_payment",
                            "target_system": "erp",
                        },
                        "timeout_seconds": 20,
                    },
                    {
                        "name": "Update ERP Records",
                        "agent_type": "execution",
                        "dependencies": ["Execute Payment"],
                        "input_data": {
                            "action": "update_record",
                            "target_system": "erp",
                        },
                        "timeout_seconds": 15,
                    },
                    {
                        "name": "Verify & Audit",
                        "agent_type": "verification",
                        "dependencies": ["Update ERP Records"],
                        "timeout_seconds": 10,
                    },
                ],
                "reasoning": "Standard procure-to-pay workflow template",
                "confidence": 0.95,
                "estimated_duration_minutes": 15,
            },
            "meeting_intelligence": {
                "tasks": [
                    {
                        "name": "Extract Meeting Data",
                        "agent_type": "intake",
                        "dependencies": [],
                        "input_data": {"type": "meeting_transcript"},
                        "timeout_seconds": 30,
                    },
                    {
                        "name": "Identify Action Items",
                        "agent_type": "intake",
                        "dependencies": ["Extract Meeting Data"],
                        "timeout_seconds": 20,
                    },
                    {
                        "name": "Create Tickets",
                        "agent_type": "execution",
                        "dependencies": ["Identify Action Items"],
                        "input_data": {"action": "create_issue"},
                        "timeout_seconds": 20,
                    },
                    {
                        "name": "Send Notifications",
                        "agent_type": "execution",
                        "dependencies": ["Create Tickets"],
                        "input_data": {"action": "send", "target_system": "email"},
                        "timeout_seconds": 15,
                    },
                    {
                        "name": "Verify Completion",
                        "agent_type": "verification",
                        "dependencies": ["Send Notifications"],
                        "timeout_seconds": 10,
                    },
                ],
                "reasoning": "Standard meeting intelligence workflow template",
                "confidence": 0.9,
                "estimated_duration_minutes": 10,
            },
            "onboarding": {
                "tasks": [
                    {
                        "name": "Validate Request",
                        "agent_type": "intake",
                        "dependencies": [],
                        "input_data": {"type": "onboarding_request"},
                        "timeout_seconds": 30,
                    },
                    {
                        "name": "Check Policies",
                        "agent_type": "policy",
                        "dependencies": ["Validate Request"],
                        "timeout_seconds": 20,
                    },
                    {
                        "name": "Provision Accounts",
                        "agent_type": "execution",
                        "dependencies": ["Check Policies"],
                        "input_data": {
                            "action": "provision_accounts",
                            "target_system": "erp",
                        },
                        "timeout_seconds": 30,
                    },
                    {
                        "name": "Create IT Request",
                        "agent_type": "execution",
                        "dependencies": ["Check Policies"],
                        "input_data": {
                            "action": "create_request",
                            "target_system": "servicenow",
                        },
                        "timeout_seconds": 20,
                    },
                    {
                        "name": "Send Welcome Email",
                        "agent_type": "execution",
                        "dependencies": ["Provision Accounts"],
                        "input_data": {"action": "send", "target_system": "email"},
                        "timeout_seconds": 15,
                    },
                    {
                        "name": "Verify Setup",
                        "agent_type": "verification",
                        "dependencies": [
                            "Provision Accounts",
                            "Create IT Request",
                            "Send Welcome Email",
                        ],
                        "timeout_seconds": 10,
                    },
                ],
                "reasoning": "Standard employee onboarding workflow template",
                "confidence": 0.9,
                "estimated_duration_minutes": 60,
            },
        }
