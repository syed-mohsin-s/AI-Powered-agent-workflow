"""
Sentinel-AI Main Application.

FastAPI entry point that initializes all agents, integrations,
and starts the workflow engine.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from sentinel_ai.config import get_config
from sentinel_ai.core.engine import get_engine
from sentinel_ai.core.scheduler import get_scheduler
from sentinel_ai.utils.logger import get_logger
from sentinel_ai.utils.metrics import get_metrics

logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    logger.info("🚀 Sentinel-AI Starting Up...")
    config = get_config()

    # ---------- Initialize Agents ----------
    from sentinel_ai.agents.decision import DecisionAgent
    from sentinel_ai.agents.execution import ExecutionAgent
    from sentinel_ai.agents.intake import IntakeAgent
    from sentinel_ai.agents.monitoring import MonitoringAgent
    from sentinel_ai.agents.orchestrator import OrchestratorAgent
    from sentinel_ai.agents.planner import PlannerAgent
    from sentinel_ai.agents.policy import PolicyAgent
    from sentinel_ai.agents.recovery import RecoveryAgent
    from sentinel_ai.agents.reliability_guard import ReliabilityGuardAgent
    from sentinel_ai.agents.supervisor import SupervisorAgent
    from sentinel_ai.agents.verification import VerificationAgent

    agents = {
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

    # Register agents with engine
    engine = get_engine()
    for agent_type, agent in agents.items():
        engine.register_agent(agent_type, agent)

    # ---------- Initialize Integrations ----------
    from sentinel_ai.integrations.local_system import LocalSystemAdapter
    from sentinel_ai.integrations.mcp_adapter import MCPAdapter
    from sentinel_ai.integrations.mock_adapters import (EmailAdapter,
                                                        ERPAdapter,
                                                        ServiceNowAdapter)

    erp = ERPAdapter()
    await erp.connect()

    email = EmailAdapter()
    await email.connect()

    servicenow = ServiceNowAdapter()
    await servicenow.connect()

    local_fs = LocalSystemAdapter()
    await local_fs.connect()

    # Initialize Atlassian MCP
    atlassian_mcp_config = config.integrations.atlassian_mcp
    atlassian_mcp = MCPAdapter(
        name="Atlassian MCP",
        command=atlassian_mcp_config.command,
        args=atlassian_mcp_config.args,
    )
    await atlassian_mcp.connect()

    # Register integrations with execution agent
    exec_agent: ExecutionAgent = agents["execution"]
    exec_agent.register_integration("atlassian_mcp", atlassian_mcp)
    exec_agent.register_integration("erp", erp)
    exec_agent.register_integration("email", email)
    exec_agent.register_integration("servicenow", servicenow)
    exec_agent.register_integration("local_system", local_fs)

    # ---------- Discover & Register Tool Capabilities ----------
    from sentinel_ai.core.tool_registry import get_tool_registry

    tool_registry = get_tool_registry()
    all_integrations = [erp, email, servicenow, local_fs, atlassian_mcp]

    for adapter in all_integrations:
        await tool_registry.discover_from_integration(adapter)

    # Attempt live MCP tool discovery (adds tools beyond the static fallbacks)
    if atlassian_mcp.is_connected:
        await tool_registry.discover_from_mcp(atlassian_mcp)

    # ---------- Register Agents API ----------
    from sentinel_ai.api.agents import register_agents

    register_agents(agents)

    # ---------- Start Background Services ----------
    scheduler = get_scheduler()
    await scheduler.start_monitoring()

    supervisor: SupervisorAgent = agents["supervisor"]
    await supervisor.start_monitoring()

    registry_summary = tool_registry.get_registry_summary()
    logger.info("✅ Sentinel-AI Ready — All agents initialized")
    logger.info(f"   Agents: {', '.join(agents.keys())}")
    logger.info(
        f"   Integrations: atlassian_mcp({'connected' if atlassian_mcp.is_connected else 'mock'}), erp(mock), email(mock), servicenow(mock)"
    )
    logger.info(
        f"   Tool Registry: {registry_summary['total_tools']} tools registered ({registry_summary['available_tools']} available)"
    )
    logger.info("   Dashboard: http://localhost:8000/dashboard")
    logger.info("   API Docs:  http://localhost:8000/docs")

    yield

    # ---------- Shutdown ----------
    logger.info("Sentinel-AI shutting down...")
    await scheduler.stop_monitoring()
    await supervisor.stop_monitoring()
    logger.info("Sentinel-AI stopped.")


# ---------- Create FastAPI App ----------

app = FastAPI(
    title="Sentinel-AI",
    description="Enterprise-Grade Agentic AI Workflow Engine",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Register API Routes ----------

from sentinel_ai.api.agents import router as agents_router  # noqa: E402
from sentinel_ai.api.audit import router as audit_router  # noqa: E402
from sentinel_ai.api.websocket import router as ws_router  # noqa: E402
from sentinel_ai.api.workflows import router as workflows_router  # noqa: E402

app.include_router(workflows_router)
app.include_router(agents_router)
app.include_router(audit_router)
app.include_router(ws_router)

# ---------- Serve Dashboard Static Files ----------
dashboard_dir = Path(__file__).parent.parent / "dashboard"

if dashboard_dir.exists():
    app.mount(
        "/dashboard/css", StaticFiles(directory=str(dashboard_dir / "css")), name="css"
    )
    app.mount(
        "/dashboard/js", StaticFiles(directory=str(dashboard_dir / "js")), name="js"
    )

    @app.get("/dashboard")
    @app.get("/dashboard/")
    async def serve_dashboard():
        return FileResponse(str(dashboard_dir / "index.html"))


# ---------- Root Endpoint ----------
@app.get("/")
async def root():
    metrics = get_metrics()
    return {
        "name": "Sentinel-AI",
        "version": "1.0.0",
        "status": "operational",
        "description": "Enterprise-Grade Agentic AI Workflow Engine",
        "endpoints": {
            "dashboard": "/dashboard",
            "api_docs": "/docs",
            "workflows": "/api/workflows/",
            "agents": "/api/agents/",
            "audit": "/api/audit/",
            "websocket": "/ws",
        },
        "metrics": metrics.get_dashboard_snapshot()["kpis"],
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "sentinel-ai"}


if __name__ == "__main__":
    import uvicorn

    config = get_config()
    uvicorn.run(
        "sentinel_ai.main:app",
        host=config.server.host,
        port=config.server.port,
        reload=config.server.debug,
    )
