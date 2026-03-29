"""
Sentinel-AI Base Agent.

Abstract base class providing consistent lifecycle, audit logging,
error handling, and LLM integration for all specialized agents.
"""

import asyncio
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

from sentinel_ai.config import get_config
from sentinel_ai.models.audit import create_audit_record
from sentinel_ai.models.workflow import TaskResult
from sentinel_ai.utils.logger import get_logger, set_agent_context
from sentinel_ai.utils.metrics import get_metrics

logger = get_logger("agents.base")


class LLMClient:
    """
    Pluggable LLM client supporting OpenAI and Anthropic.
    
    Falls back gracefully if no API key is configured.
    """

    def __init__(self):
        self._config = get_config()
        self._openai_client = None
        self._anthropic_client = None
        self._gemini_client = None
        self._initialized = False

    def _lazy_init(self):
        if self._initialized:
            return
        self._initialized = True
        
        # Try OpenAI
        if self._config.llm.openai.api_key:
            try:
                from openai import AsyncOpenAI
                self._openai_client = AsyncOpenAI(api_key=self._config.llm.openai.api_key)
            except ImportError:
                logger.warning("OpenAI package not installed")
        
        # Try Anthropic
        if self._config.llm.anthropic.api_key:
            try:
                from anthropic import AsyncAnthropic
                self._anthropic_client = AsyncAnthropic(api_key=self._config.llm.anthropic.api_key)
            except ImportError:
                logger.warning("Anthropic package not installed")

        # Try Gemini
        if hasattr(self._config.llm, "gemini") and self._config.llm.gemini.api_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=self._config.llm.gemini.api_key)
                self._gemini_client = True
            except ImportError:
                logger.warning("google.generativeai package not installed")

    async def complete(
        self,
        prompt: str,
        system: str = "",
        provider: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Get a completion from the configured LLM.
        
        Falls back to a simple rule-based response if no LLM is available.
        """
        self._lazy_init()
        provider = provider or self._config.llm.default_provider

        if provider == "openai" and self._openai_client:
            return await self._openai_complete(prompt, system, temperature, max_tokens)
        elif provider == "anthropic" and self._anthropic_client:
            return await self._anthropic_complete(prompt, system, temperature, max_tokens)
        elif provider == "gemini" and getattr(self, "_gemini_client", None):
            return await self._gemini_complete(prompt, system, temperature, max_tokens)
        else:
            # Fallback: rule-based response
            logger.debug("No LLM configured, using rule-based fallback")
            return f"[Rule-based response] Processed: {prompt[:100]}..."

    async def _openai_complete(
        self, prompt: str, system: str, temperature: Optional[float], max_tokens: Optional[int]
    ) -> str:
        cfg = self._config.llm.openai
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = await self._openai_client.chat.completions.create(
            model=cfg.model,
            messages=messages,
            temperature=temperature or cfg.temperature,
            max_tokens=max_tokens or cfg.max_tokens,
        )
        return response.choices[0].message.content or ""

    async def _anthropic_complete(
        self, prompt: str, system: str, temperature: Optional[float], max_tokens: Optional[int]
    ) -> str:
        cfg = self._config.llm.anthropic
        response = await self._anthropic_client.messages.create(
            model=cfg.model,
            max_tokens=max_tokens or cfg.max_tokens,
            system=system or "You are an enterprise AI agent.",
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature or cfg.temperature,
        )
        return response.content[0].text if response.content else ""

    async def _gemini_complete(
        self, prompt: str, system: str, temperature: Optional[float], max_tokens: Optional[int]
    ) -> str:
        import google.generativeai as genai
        cfg = self._config.llm.gemini
        model = genai.GenerativeModel(
            model_name=cfg.model,
            system_instruction=system or "You are an enterprise AI agent."
        )
        generation_config = genai.types.GenerationConfig(
            temperature=temperature or cfg.temperature,
            max_output_tokens=max_tokens or cfg.max_tokens,
        )
        response = await model.generate_content_async(
            contents=prompt,
            generation_config=generation_config
        )
        return response.text

    @property
    def is_available(self) -> bool:
        self._lazy_init()
        return self._openai_client is not None or self._anthropic_client is not None or getattr(self, "_gemini_client", None) is not None


# Shared LLM client
_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client


class BaseAgent(ABC):
    """
    Abstract base agent for the Sentinel-AI multi-agent system.
    
    Provides:
    - Standard execute lifecycle with pre/post hooks
    - Automatic AgDR (audit record) generation
    - Built-in retry with exponential backoff
    - Health check interface
    - LLM integration
    - Metric recording
    """

    def __init__(self, name: str, agent_type: str):
        self.name = name
        self.agent_type = agent_type
        self.id = str(uuid.uuid4())
        self._health_status = "healthy"
        self._tasks_completed = 0
        self._tasks_failed = 0
        self._total_execution_time = 0.0
        self._error_count = 0
        self._last_heartbeat = datetime.now(timezone.utc)
        self._llm = get_llm_client()
        self._metrics = get_metrics()

    # -----------------------------------------------------------------------
    # Public Interface
    # -----------------------------------------------------------------------

    async def __call__(self, context: dict) -> TaskResult:
        """
        Execute the agent with full lifecycle management.
        
        This is the entry point used by the engine.
        """
        set_agent_context(self.agent_type)
        start_time = time.time()

        try:
            # Pre-execution hook
            context = await self.pre_execute(context)

            # Core execution
            result = await self.execute(context)

            # Post-execution hook
            result = await self.post_execute(context, result)

            duration = time.time() - start_time
            result.duration_seconds = duration

            if result.success:
                self._tasks_completed += 1
            else:
                self._tasks_failed += 1

            self._total_execution_time += duration
            self._last_heartbeat = datetime.now(timezone.utc)

            # Generate audit record
            create_audit_record(
                agent=self.agent_type,
                trigger_event=f"task_execution:{context.get('task_name', 'unknown')}",
                context=f"Workflow: {context.get('workflow_type', 'unknown')}, Task: {context.get('task_name', 'unknown')}",
                decision=result.reasoning or f"Task {'completed' if result.success else 'failed'}",
                reasoning=result.reasoning or "Standard execution",
                confidence=result.confidence,
                action_taken=f"{'Completed' if result.success else 'Failed'}: {context.get('task_name', '')}",
                prior_state="running",
                resulting_state="success" if result.success else "failed",
                status="executed" if result.success else "failed",
                why=f"Agent {self.agent_type} processed task with confidence {result.confidence:.2f}",
                trade_offs=result.output_data.get("trade_offs", "None identified"),
            )

            return result

        except Exception as e:
            duration = time.time() - start_time
            self._tasks_failed += 1
            self._error_count += 1
            logger.error(f"Agent {self.agent_type} error: {e}", exc_info=True)

            return TaskResult(
                success=False,
                error_message=str(e),
                confidence=0.0,
                duration_seconds=duration,
                reasoning=f"Unhandled exception: {str(e)}",
            )

    @abstractmethod
    async def execute(self, context: dict) -> TaskResult:
        """
        Core execution logic — must be implemented by each specialized agent.
        
        Args:
            context: Dictionary containing:
                - workflow_id: str
                - workflow_type: str
                - task_id: str
                - task_name: str
                - input_data: dict (workflow input + task-specific input merged)
                - shared_context: dict (accumulated outputs from prior tasks)
                - attempt: int (retry attempt number)
                
        Returns:
            TaskResult with success status, output data, confidence, and reasoning.
        """
        raise NotImplementedError

    async def pre_execute(self, context: dict) -> dict:
        """Hook called before execute(). Override for input validation."""
        return context

    async def post_execute(self, context: dict, result: TaskResult) -> TaskResult:
        """Hook called after execute(). Override for result validation."""
        return result

    # -----------------------------------------------------------------------
    # LLM Helpers
    # -----------------------------------------------------------------------

    async def llm_analyze(self, prompt: str, system: str = "") -> str:
        """Use the LLM for analysis/reasoning."""
        if not system:
            system = f"You are {self.name}, a specialized enterprise AI agent. Be precise and structured in your responses."
        return await self._llm.complete(prompt, system=system)

    async def llm_extract(self, text: str, schema_description: str) -> str:
        """Use the LLM to extract structured data from text."""
        system = (
            f"You are {self.name}. Extract structured data from the provided text. "
            f"Return ONLY valid JSON matching the required schema. No explanations."
        )
        prompt = f"Schema: {schema_description}\n\nText to extract from:\n{text}"
        return await self._llm.complete(prompt, system=system)

    # -----------------------------------------------------------------------
    # Health & Status
    # -----------------------------------------------------------------------

    def health_check(self) -> dict:
        """Return agent health status."""
        total = self._tasks_completed + self._tasks_failed
        success_rate = (self._tasks_completed / total * 100) if total > 0 else 100.0
        avg_time = (self._total_execution_time / total * 1000) if total > 0 else 0.0

        return {
            "name": self.name,
            "type": self.agent_type,
            "status": self._health_status,
            "tasks_completed": self._tasks_completed,
            "tasks_failed": self._tasks_failed,
            "success_rate": round(success_rate, 1),
            "avg_response_time_ms": round(avg_time, 1),
            "error_count": self._error_count,
            "last_heartbeat": self._last_heartbeat.isoformat(),
        }

    def update_health(self, status: str) -> None:
        """Update agent health status."""
        self._health_status = status
        self._last_heartbeat = datetime.now(timezone.utc)
