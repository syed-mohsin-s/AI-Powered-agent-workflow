"""
Sentinel-AI Dynamic Skill Loader.

Loads specialized skills based on task context. Skills provide:
- Customized system prompts for the LLM
- Preferred tool selections
- Input validation rules

Skills are defined as YAML files in the library/ directory and
loaded on demand when their trigger conditions match.
"""

from typing import Optional

from sentinel_ai.core.skills.base_skill import Skill
from sentinel_ai.utils.logger import get_logger

logger = get_logger("core.skills")


class SkillLoader:
    """
    Loads specialized skills based on task context.

    Skills are matched against the current task's workflow type,
    task name, and input data. Only matching skills are activated,
    keeping the LLM prompt focused and relevant.
    """

    def __init__(self):
        self._skills: list[Skill] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Lazy-load all skill definitions."""
        if self._loaded:
            return
        self._loaded = True
        self._skills = self._load_builtin_skills()
        logger.info(f"Loaded {len(self._skills)} skills")

    def load_skills_for_task(
        self,
        task_name: str,
        workflow_type: str,
        data: dict,
    ) -> list[Skill]:
        """
        Find and return all skills that match the given task context.

        Skills are sorted by match score (best match first).
        """
        self._ensure_loaded()

        data_keys = set(data.keys()) if data else set()
        matched = []

        for skill in self._skills:
            score = skill.matches_context(
                workflow_type=workflow_type,
                task_name=task_name,
                data_keys=data_keys,
            )
            if score > 0.2:
                matched.append((score, skill))

        matched.sort(key=lambda x: (-x[0], -x[1].priority))
        return [skill for _, skill in matched]

    def get_system_prompt(self, skills: list[Skill]) -> str:
        """
        Compose a system prompt from active skills.

        Merges the system prompts of all matched skills into a
        cohesive instruction set for the LLM.
        """
        if not skills:
            return ""

        parts = [
            "You are a specialized enterprise AI agent with the following expertise:\n",
        ]
        for skill in skills:
            if skill.system_prompt:
                parts.append(f"## {skill.name}\n{skill.system_prompt}\n")

        return "\n".join(parts)

    def get_preferred_tools(self, skills: list[Skill]) -> list[str]:
        """Get tool preferences from active skills."""
        tools = []
        seen = set()
        for skill in skills:
            for tool in skill.tools_preferred:
                if tool not in seen:
                    tools.append(tool)
                    seen.add(tool)
        return tools

    def get_validation_rules(self, skills: list[Skill]) -> list[dict]:
        """Get data validation rules from active skills."""
        rules = []
        for skill in skills:
            rules.extend(skill.validation_rules)
        return rules

    def register_skill(self, skill: Skill) -> None:
        """Register a skill programmatically."""
        self._ensure_loaded()
        self._skills.append(skill)

    def _load_builtin_skills(self) -> list[Skill]:
        """Load the built-in skill library."""
        return [
            Skill(
                name="Invoice Processing",
                description="Extract and validate invoice data for payment processing",
                triggers={
                    "workflow_types": ["p2p"],
                    "task_names": ["extract", "invoice", "payment"],
                    "data_contains": ["invoice_number", "vendor_name", "total_amount"],
                },
                system_prompt=(
                    "You are a specialized invoice processing agent. You understand:\n"
                    "- Standard invoice formats (PDF, EDI, XML)\n"
                    "- Three-way matching (PO, receipt, invoice)\n"
                    "- Tax calculations and currency conversion\n"
                    "- Duplicate invoice detection\n"
                    "- Vendor payment terms and early payment discounts\n\n"
                    "Always extract: vendor_name, invoice_number, date, line_items, "
                    "total_amount, currency, po_number, payment_terms.\n"
                    "Flag any discrepancies between invoice amount and PO amount."
                ),
                tools_preferred=[
                    "erp_match_po",
                    "erp_create_payment",
                    "erp_update_record",
                ],
                validation_rules=[
                    {"field": "total_amount", "type": "number", "required": True},
                    {"field": "invoice_number", "type": "string", "required": True},
                    {"field": "vendor_name", "type": "string", "required": True},
                ],
                priority=8,
            ),
            Skill(
                name="Compliance Review",
                description="Regulatory compliance and policy validation",
                triggers={
                    "workflow_types": ["p2p", "contract_clm", "onboarding"],
                    "task_names": ["validate", "compliance", "policy", "review"],
                    "data_contains": ["approved", "compliance"],
                },
                system_prompt=(
                    "You are a compliance review specialist. You understand:\n"
                    "- SOX compliance for financial transactions\n"
                    "- GDPR requirements for personal data handling\n"
                    "- Corporate procurement policies and approval limits\n"
                    "- Vendor due diligence requirements\n"
                    "- Segregation of duties controls\n\n"
                    "Always check: amount limits, required approvals, data privacy, "
                    "regulatory requirements, and policy adherence.\n"
                    "Provide clear approved/rejected decisions with reasoning."
                ),
                tools_preferred=[],
                validation_rules=[],
                priority=9,
            ),
            Skill(
                name="Payment Execution",
                description="Payment processing and ERP integration",
                triggers={
                    "workflow_types": ["p2p"],
                    "task_names": ["pay", "payment", "execute"],
                    "data_contains": ["total_amount", "payment_id"],
                },
                system_prompt=(
                    "You are a payment execution specialist. You understand:\n"
                    "- Payment methods (ACH, wire, check, virtual card)\n"
                    "- Multi-currency transactions and FX rates\n"
                    "- Payment scheduling and batch processing\n"
                    "- Reconciliation with bank statements\n"
                    "- Fraud detection signals\n\n"
                    "Always verify: sufficient budget, correct bank details, "
                    "duplicate payment check, approval chain complete.\n"
                    "Flag high-risk payments (new vendors, amounts > $10K, foreign currencies)."
                ),
                tools_preferred=["erp_create_payment", "erp_update_record"],
                validation_rules=[
                    {
                        "field": "total_amount",
                        "type": "number",
                        "required": True,
                        "max": 100000,
                    },
                ],
                priority=7,
            ),
            Skill(
                name="Issue Tracking",
                description="Jira/ticket management and workflow automation",
                triggers={
                    "workflow_types": ["meeting_intelligence"],
                    "task_names": ["issue", "ticket", "jira", "create", "track"],
                    "data_contains": ["summary", "project", "issue_key"],
                },
                system_prompt=(
                    "You are a project management specialist. You understand:\n"
                    "- Jira issue types (Story, Task, Bug, Epic)\n"
                    "- Sprint planning and backlog management\n"
                    "- Issue prioritization frameworks (MoSCoW, RICE)\n"
                    "- Workflow transitions and status management\n\n"
                    "When creating issues: assign clear titles, detailed descriptions, "
                    "appropriate priority, and accurate story points.\n"
                    "Link related issues and set dependencies when relevant."
                ),
                tools_preferred=[
                    "mcp_jira_create_issue",
                    "mcp_jira_update_issue",
                    "mcp_jira_transition",
                ],
                validation_rules=[
                    {"field": "summary", "type": "string", "required": True},
                ],
                priority=6,
            ),
            Skill(
                name="Contract Analysis",
                description="Contract review, risk assessment, and CLM",
                triggers={
                    "workflow_types": ["contract_clm"],
                    "task_names": ["contract", "legal", "review", "draft"],
                    "data_contains": ["contract_type", "parties", "effective_date"],
                },
                system_prompt=(
                    "You are a contract analysis specialist. You understand:\n"
                    "- Standard contract clauses and boilerplate language\n"
                    "- Risk assessment for contractual obligations\n"
                    "- Renewal and termination provisions\n"
                    "- Liability caps and indemnification clauses\n"
                    "- Governing law and dispute resolution mechanisms\n\n"
                    "Always analyze: key terms, obligations, risks, expiration dates, "
                    "and auto-renewal clauses.\n"
                    "Flag any unusual or non-standard clauses."
                ),
                tools_preferred=["erp_update_record"],
                validation_rules=[
                    {"field": "contract_type", "type": "string", "required": True},
                ],
                priority=7,
            ),
            Skill(
                name="Employee Onboarding",
                description="New hire onboarding process automation",
                triggers={
                    "workflow_types": ["onboarding"],
                    "task_names": ["onboard", "provision", "setup", "welcome"],
                    "data_contains": ["employee_name", "start_date", "department"],
                },
                system_prompt=(
                    "You are an employee onboarding specialist. You understand:\n"
                    "- IT provisioning workflows (accounts, hardware, access)\n"
                    "- HR documentation requirements\n"
                    "- Compliance training assignments\n"
                    "- Buddy program and orientation scheduling\n\n"
                    "Ensure all required accounts are provisioned, equipment is ordered, "
                    "and orientation is scheduled before start date.\n"
                    "Send welcome communications to the new hire and their manager."
                ),
                tools_preferred=[
                    "erp_provision_accounts",
                    "servicenow_create_request",
                    "email_send",
                ],
                validation_rules=[
                    {"field": "employee_name", "type": "string", "required": True},
                    {"field": "start_date", "type": "string", "required": True},
                ],
                priority=6,
            ),
        ]


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_loader: Optional[SkillLoader] = None


def get_skill_loader() -> SkillLoader:
    """Get the global skill loader singleton."""
    global _loader
    if _loader is None:
        _loader = SkillLoader()
    return _loader
