"""
Sentinel-AI Configuration Module.

Loads settings from YAML config + environment variables with Pydantic validation.
"""

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


# ---------------------------------------------------------------------------
# Sub-models for nested YAML sections
# ---------------------------------------------------------------------------

class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True
    workers: int = 1


class DatabaseConfig(BaseModel):
    url: str = "postgresql+asyncpg://sentinel:sentinel_pass@localhost:5432/sentinel_ai"
    echo: bool = False
    pool_size: int = 10
    max_overflow: int = 20


class LLMProviderConfig(BaseModel):
    api_key: str = ""
    model: str = ""
    temperature: float = 0.2
    max_tokens: int = 4096


class LLMConfig(BaseModel):
    default_provider: str = "gemini"
    openai: LLMProviderConfig = LLMProviderConfig(model="gpt-4o")
    anthropic: LLMProviderConfig = LLMProviderConfig(model="claude-sonnet-4-20250514")
    gemini: LLMProviderConfig = LLMProviderConfig(api_key="${GEMINI_API_KEY}", model="gemini-1.5-flash")


class AgentTimingConfig(BaseModel):
    max_parallel_tasks: int = 10
    planning_timeout_seconds: int = 30
    health_check_interval_seconds: int = 5
    max_retries: int = 3
    retry_base_seconds: float = 1.0
    retry_max_backoff_seconds: float = 30.0
    retry_jitter_ratio: float = 0.2
    circuit_breaker_threshold: int = 5
    circuit_breaker_reset_seconds: int = 60
    extraction_confidence_threshold: float = 0.7
    approval_threshold: float = 0.85
    timeout_seconds: int = 30
    sla_check_interval_seconds: int = 10
    stall_detection_threshold_seconds: int = 300
    max_recovery_attempts: int = 3
    escalation_confidence_threshold: float = 0.3


class AtlassianMCPConfig(BaseModel):
    command: str = "npx"
    args: list[str] = Field(default_factory=lambda: ["-y", "@modelcontextprotocol/server-atlassian"])


class MockIntegrationConfig(BaseModel):
    type: str = "mock"
    simulate_delay_ms: int = 500


class IntegrationsConfig(BaseModel):
    atlassian_mcp: AtlassianMCPConfig = AtlassianMCPConfig()
    erp: MockIntegrationConfig = MockIntegrationConfig(simulate_delay_ms=500)
    email: MockIntegrationConfig = MockIntegrationConfig(simulate_delay_ms=200)
    servicenow: MockIntegrationConfig = MockIntegrationConfig(simulate_delay_ms=400)


class AuditConfig(BaseModel):
    hash_algorithm: str = "sha256"
    chain_verification_interval_seconds: int = 300
    retention_days: int = 365
    export_format: str = "json"


class MetricsConfig(BaseModel):
    collection_interval_seconds: int = 5
    rolling_window_minutes: int = 60
    export_enabled: bool = True


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"
    file: str = "logs/sentinel.log"
    max_size_mb: int = 100
    backup_count: int = 5


# ---------------------------------------------------------------------------
# Root configuration
# ---------------------------------------------------------------------------

class SentinelConfig(BaseModel):
    """Root configuration for Sentinel-AI system."""
    
    server: ServerConfig = ServerConfig()
    database: DatabaseConfig = DatabaseConfig()
    llm: LLMConfig = LLMConfig()
    agents: AgentTimingConfig = AgentTimingConfig()
    integrations: IntegrationsConfig = IntegrationsConfig()
    audit: AuditConfig = AuditConfig()
    metrics: MetricsConfig = MetricsConfig()
    logging: LoggingConfig = LoggingConfig()


def _resolve_env_vars(data: dict) -> dict:
    """Recursively resolve ${ENV_VAR} references in config values."""
    resolved = {}
    for key, value in data.items():
        if isinstance(value, dict):
            resolved[key] = _resolve_env_vars(value)
        elif isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            env_var = value[2:-1]
            resolved[key] = os.environ.get(env_var, "")
        elif isinstance(value, list):
            resolved[key] = [
                os.environ.get(v[2:-1], "") if isinstance(v, str) and v.startswith("${") and v.endswith("}") else v
                for v in value
            ]
        else:
            resolved[key] = value
    return resolved


def load_config(config_path: Optional[str] = None) -> SentinelConfig:
    """Load configuration from YAML file with env var substitution."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # python-dotenv not installed, assuming env vars set correctly
        
    if config_path is None:
        # Try common locations
        candidates = [
            Path("config/sentinel.yaml"),
            Path("sentinel.yaml"),
            Path(__file__).parent.parent / "config" / "sentinel.yaml",
        ]
        for candidate in candidates:
            if candidate.exists():
                config_path = str(candidate)
                break
    
    if config_path and Path(config_path).exists():
        with open(config_path, "r") as f:
            raw = yaml.safe_load(f) or {}
        data = _resolve_env_vars(raw)
        return SentinelConfig(**data)
    
    # Fallback to defaults
    return SentinelConfig()


# Global config singleton
_config: Optional[SentinelConfig] = None


def get_config() -> SentinelConfig:
    """Get the global configuration singleton."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reset_config() -> None:
    """Reset the global config (useful for testing)."""
    global _config
    _config = None
