"""Tests for workflow template creation."""

import pytest
from sentinel_ai.workflows.p2p import create_p2p_workflow
from sentinel_ai.workflows.meeting_intel import create_meeting_workflow
from sentinel_ai.workflows.onboarding import create_onboarding_workflow
from sentinel_ai.workflows.contract_clm import create_contract_workflow
from sentinel_ai.core.dag import WorkflowDAG


class TestP2PWorkflow:
    def test_create(self):
        workflow, tasks = create_p2p_workflow({"vendor_name": "Acme", "total_amount": 5000})
        assert workflow.workflow_type == "p2p"
        assert len(tasks) == 10

    def test_dag_valid(self):
        _, tasks = create_p2p_workflow({"vendor_name": "Acme"})
        dag = WorkflowDAG()
        dag.add_tasks(tasks)
        groups = dag.build()
        assert len(groups) >= 1
        # First group should have the extract task only
        assert len(groups[0].task_ids) == 1


class TestMeetingWorkflow:
    def test_create(self):
        workflow, tasks = create_meeting_workflow({"content": "Meeting transcript..."})
        assert workflow.workflow_type == "meeting_intelligence"
        assert len(tasks) == 8

    def test_dag_valid(self):
        _, tasks = create_meeting_workflow({"content": "Test"})
        dag = WorkflowDAG()
        dag.add_tasks(tasks)
        groups = dag.build()
        assert len(groups) >= 1


class TestOnboardingWorkflow:
    def test_create(self):
        workflow, tasks = create_onboarding_workflow({
            "employee_name": "Jane",
            "department": "engineering",
        })
        assert workflow.workflow_type == "onboarding"
        assert len(tasks) == 8

    def test_parallel_provisioning(self):
        _, tasks = create_onboarding_workflow({"employee_name": "Jane"})
        dag = WorkflowDAG()
        dag.add_tasks(tasks)
        groups = dag.build()
        # Should have a parallel group (provision + equipment + notify)
        parallel_group = [g for g in groups if len(g.task_ids) >= 3]
        assert len(parallel_group) >= 1


class TestContractWorkflow:
    def test_create(self):
        workflow, tasks = create_contract_workflow({
            "parties": ["A", "B"],
            "value": 100000,
        })
        assert workflow.workflow_type == "contract_clm"
        assert len(tasks) == 8

    def test_parallel_reviews(self):
        _, tasks = create_contract_workflow({"parties": ["A", "B"]})
        dag = WorkflowDAG()
        dag.add_tasks(tasks)
        groups = dag.build()
        # Legal and financial review should be parallel
        review_group = [g for g in groups if len(g.task_ids) == 2]
        assert len(review_group) >= 1
