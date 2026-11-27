"""
Hybrid Compute & Privacy-First Routing Engine.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)

Routes continuous background observation, PII scrubbing, DOM parsing, and classification
to local inference runtimes (Ollama/vLLM/llama.cpp).
Routes only anonymized high-complexity synthesis to cloud frontier models when local confidence < threshold.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("HybridRouter")


class PiiScrubber:
    """
    Local regex & token-based PII masking engine ensuring raw sensitive data
    never leaves the machine if offloaded to cloud models.
    """
    EMAIL_REGEX = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")
    # Credit cards first (more specific pattern)
    CREDIT_CARD_REGEX = re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")
    # Phone regex that doesn't match 16-digit sequences
    PHONE_REGEX = re.compile(r"(?<!\d)(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}(?!\d)")

    @classmethod
    def scrub(cls, text: str) -> tuple[str, dict[str, str]]:
        token_map: dict[str, str] = {}
        idx = 1

        def replace_email(match):
            nonlocal idx
            token = f"[REDACTED_EMAIL_{idx}]"
            token_map[token] = match.group(0)
            idx += 1
            return token

        def replace_phone(match):
            nonlocal idx
            token = f"[REDACTED_PHONE_{idx}]"
            token_map[token] = match.group(0)
            idx += 1
            return token

        def replace_card(match):
            nonlocal idx
            token = f"[REDACTED_CARD_{idx}]"
            token_map[token] = match.group(0)
            idx += 1
            return token

        # Order matters: credit cards before phones to avoid 16-digit matches
        scrubbed = cls.EMAIL_REGEX.sub(replace_email, text)
        scrubbed = cls.CREDIT_CARD_REGEX.sub(replace_card, scrubbed)
        scrubbed = cls.PHONE_REGEX.sub(replace_phone, scrubbed)
        return scrubbed, token_map

    @classmethod
    def restore(cls, text: str, token_map: dict[str, str]) -> str:
        restored = text
        for token, original in token_map.items():
            restored = restored.replace(token, original)
        return restored


class OllamaClient:
    """Async client for Ollama native API with JSON schema support."""

    def __init__(self, base_url: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        format_schema: str | dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = await self._get_client()
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if format_schema:
            payload["format"] = format_schema
        if options:
            payload["options"] = options

        response = await client.post(f"{self.base_url}", json=payload)
        response.raise_for_status()
        return response.json()

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()


class OpenAICompatibleClient:
    """Async client for OpenAI-compatible local endpoints (vLLM, llama.cpp)."""

    def __init__(self, base_url: str, api_key: str = "", timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(timeout=self.timeout, headers=headers)
        return self._client

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        client = await self._get_client()
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if response_format:
            payload["response_format"] = response_format

        response = await client.post(f"{self.base_url}/chat/completions", json=payload)
        response.raise_for_status()
        return response.json()

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()


class CloudFrontierClient:
    """Async client for cloud frontier model escalation (optional)."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        return self._client

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = await self._get_client()
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if response_format:
            payload["response_format"] = response_format
        response = await client.post(f"{self.base_url}/chat/completions", json=payload)
        response.raise_for_status()
        return response.json()

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()


class HybridRouter:
    """
    Determines whether a task should run on Local LLM (Ollama/vLLM)
    or escalate to Cloud Frontier API.
    """

    def __init__(
        self,
        ollama_url: str | None = None,
        local_openai_url: str | None = None,
        cloud_api_url: str | None = None,
        cloud_api_key: str | None = None,
        fast_model: str | None = None,
        reasoning_model: str | None = None,
        confidence_threshold: float = 0.80,
        mock_mode: bool | None = None,
    ):
        self.ollama_url = ollama_url or settings.OLLAMA_URL
        self.local_openai_url = local_openai_url or settings.LOCAL_OPENAI_URL
        self.cloud_api_url = cloud_api_url or settings.CLOUD_API_URL
        self.cloud_api_key = cloud_api_key or settings.CLOUD_API_KEY
        self.fast_model = fast_model or settings.FAST_MODEL
        self.reasoning_model = reasoning_model or settings.REASONING_MODEL
        self.confidence_threshold = confidence_threshold
        self.mock_mode = settings.MOCK_LLM if mock_mode is None else mock_mode

        self._ollama: OllamaClient | None = None
        self._local_openai: OpenAICompatibleClient | None = None
        self._cloud: CloudFrontierClient | None = None

    @property
    def ollama(self) -> OllamaClient:
        if self._ollama is None:
            self._ollama = OllamaClient(self.ollama_url)
        return self._ollama

    @property
    def local_openai(self) -> OpenAICompatibleClient | None:
        if self.local_openai_url and self._local_openai is None:
            self._local_openai = OpenAICompatibleClient(self.local_openai_url)
        return self._local_openai

    @property
    def cloud(self) -> CloudFrontierClient | None:
        if self.cloud_api_url and self.cloud_api_key and self._cloud is None:
            self._cloud = CloudFrontierClient(self.cloud_api_url, self.cloud_api_key)
        return self._cloud

    def _select_model(self, task_type: str) -> str:
        """Select model based on task complexity."""
        if task_type in ("classification", "dom_parse", "whatsapp_classification", "pii_scrub", "financial_triage"):
            return self.fast_model
        return self.reasoning_model

    async def route_and_generate(
        self,
        prompt: str,
        task_type: str = "classification",  # "classification" | "dom_parse" | "complex_reasoning"
        system_prompt: str | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Executes local inference first. If confidence score is below threshold
        and task requires complex reasoning, anonymizes PII and routes to Cloud.
        """
        # Tasks that must NEVER leave local compute:
        strictly_local_tasks = ["dom_parse", "whatsapp_classification", "pii_scrub", "financial_triage"]

        if task_type in strictly_local_tasks:
            logger.info(f"Task '{task_type}' locked to local-first privacy boundary.")
            return await self._call_local_llm(prompt, system_prompt, task_type, response_format)

        # Attempt local execution
        local_result = await self._call_local_llm(prompt, system_prompt, task_type, response_format)
        confidence = local_result.get("confidence", 1.0)

        if confidence >= self.confidence_threshold or task_type != "complex_reasoning":
            return local_result

        # Escalate to Cloud Frontier Model (only if configured)
        if not self.cloud:
            logger.warning("Cloud escalation requested but no cloud endpoint configured. Returning local result.")
            return local_result

        logger.info(f"Local confidence ({confidence:.2f}) < threshold ({self.confidence_threshold}). Escalating to Cloud...")
        scrubbed_prompt, token_map = PiiScrubber.scrub(prompt)

        cloud_result = await self._call_cloud_llm(scrubbed_prompt, system_prompt, response_format)
        # Restore scrubbed tokens locally
        cloud_result["text"] = PiiScrubber.restore(cloud_result["text"], token_map)
        # Only override routed_to if cloud call succeeded (not a fallback)
        if cloud_result.get("routed_to") == "frontier_cloud":
            cloud_result["routed_to"] = "frontier_cloud"
        return cloud_result

    async def _call_local_llm(
        self,
        prompt: str,
        system_prompt: str | None,
        task_type: str,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call local LLM (Ollama preferred, fallback to OpenAI-compatible)."""
        if self.mock_mode:
            return self._mock_response(prompt, task_type, "local_ollama")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        model = self._select_model(task_type)

        # Try Ollama first
        try:
            format_schema: str | dict[str, Any] | None = None
            if response_format and response_format.get("type") == "json_object":
                format_schema = "json"

            result = await self.ollama.chat(
                model=model,
                messages=messages,
                format_schema=format_schema,
                options={"temperature": 0.1, "num_predict": 2048},
            )

            text = result.get("message", {}).get("content", "")
            return {
                "text": text,
                "confidence": self._estimate_confidence(text, task_type),
                "routed_to": "local_ollama",
                "model": model,
            }
        except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as e:
            logger.warning(f"Ollama call failed: {e}. Trying OpenAI-compatible endpoint...")

        # Fallback to OpenAI-compatible local endpoint
        if self.local_openai:
            try:
                result = await self.local_openai.chat(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    temperature=0.1,
                )
                text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                return {
                    "text": text,
                    "confidence": self._estimate_confidence(text, task_type),
                    "routed_to": "local_openai_compatible",
                    "model": model,
                }
            except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as e:
                logger.error(f"Local OpenAI-compatible call failed: {e}")

        # Final fallback to mock
        logger.warning("All local LLM calls failed. Falling back to mock response.")
        return self._mock_response(prompt, task_type, "local_mock_fallback")

    async def _call_cloud_llm(
        self,
        scrubbed_prompt: str,
        system_prompt: str | None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call cloud frontier model."""
        if self.mock_mode:
            return self._mock_response(scrubbed_prompt, "complex_reasoning", "frontier_cloud")

        cloud_client = self.cloud
        if cloud_client is None:
            logger.warning("Cloud client not configured. Falling back to mock response.")
            return self._mock_response(scrubbed_prompt, "complex_reasoning", "frontier_cloud_mock_fallback")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": scrubbed_prompt})

        try:
            result = await cloud_client.chat(
                model="gpt-4o",  # Default frontier model
                messages=messages,
                response_format=response_format,
                temperature=0.3,
            )
            text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {
                "text": text,
                "confidence": 0.95,
                "routed_to": "frontier_cloud",
            }
        except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as e:
            logger.error(f"Cloud LLM call failed: {e}")
            # Fallback to local mock on cloud failure
            return self._mock_response(scrubbed_prompt, "complex_reasoning", "frontier_cloud_mock_fallback")

    def _mock_response(self, prompt: str, task_type: str, routed_to: str) -> dict[str, Any]:
        """Deterministic mock response for offline/CI testing."""
        model = self._select_model(task_type)
        return {
            "text": f"[MOCK:{routed_to}] Processed {task_type} for: {prompt[:60]}...",
            "confidence": 0.90 if "local" in routed_to else 0.98,
            "routed_to": routed_to,
            "model": model,
        }

    def _estimate_confidence(self, text: str, task_type: str) -> float:
        """Heuristic confidence estimation based on response quality indicators."""
        if not text or len(text.strip()) < 10:
            return 0.3
        if task_type in ("classification", "dom_parse", "whatsapp_classification", "pii_scrub", "financial_triage"):
            # Structured tasks: check for JSON-like or clear structured output
            if text.strip().startswith("{") or "classification" in text.lower():
                return 0.9
            return 0.75
        # Complex reasoning: length and coherence heuristic
        if len(text) > 200:
            return 0.85
        return 0.7

    async def close(self):
        """Close all HTTP clients."""
        if self._ollama:
            await self._ollama.close()
        if self._local_openai:
            await self._local_openai.close()
        if self._cloud:
            await self._cloud.close()


# Global router instance
_global_router: HybridRouter | None = None


def get_hybrid_router() -> HybridRouter:
    global _global_router
    if _global_router is None:
        _global_router = HybridRouter()
    return _global_router


async def shutdown_hybrid_router():
    global _global_router
    if _global_router:
        await _global_router.close()
        _global_router = None
