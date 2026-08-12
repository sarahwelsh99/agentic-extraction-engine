"""LLM service for local vLLM inference.

Provides interface to local vLLM server for:
- Phase 1: Pattern analysis & code generation
- Phase 3: Quality evaluation

For Phase 4 execution, uses generated deterministic code (no LLM).
"""
import logging
import json
from typing import Optional, Dict, Any
import requests
import config

logger = logging.getLogger(__name__)


class LocalLLMClient:
    """Client for local vLLM server (OpenAI-compatible API)."""

    def __init__(self, api_base: str = config.VLLM_API_BASE,
                 model: str = config.VLLM_MODEL,
                 timeout: int = config.VLLM_TIMEOUT):
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.completions_url = f"{self.api_base}/v1/chat/completions"

    def is_healthy(self) -> bool:
        """Check if vLLM server is responding."""
        try:
            response = requests.get(
                f"{self.api_base}/v1/models",
                timeout=5
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"vLLM health check failed: {e}")
            return False

    def generate(self,
                 prompt: str,
                 system_prompt: Optional[str] = None,
                 temperature: float = 0.0,
                 max_tokens: int = 2000,
                 json_schema: Optional[Dict] = None) -> str:
        """Generate text from the local vLLM model.

        Args:
            prompt: User prompt
            system_prompt: System prompt (optional)
            temperature: Sampling temperature (0.0 for deterministic)
            max_tokens: Maximum tokens in response
            json_schema: JSON schema for structured output (optional)

        Returns:
            Generated text (raw response content)
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # Add structured output if json_schema provided
        if json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": json_schema,
                    "strict": True
                }
            }

        try:
            response = requests.post(
                self.completions_url,
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]
        except requests.exceptions.Timeout:
            raise TimeoutError(f"vLLM request timed out after {self.timeout}s")
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"vLLM request failed: {e}")

    def batch_generate(self,
                      prompts: list,
                      system_prompt: Optional[str] = None,
                      temperature: float = 0.0,
                      max_tokens: int = 2000) -> list:
        """Generate responses for multiple prompts sequentially."""
        results = []
        for i, prompt in enumerate(prompts):
            try:
                result = self.generate(prompt, system_prompt, temperature, max_tokens)
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to generate for prompt {i}: {e}")
                results.append(None)
        return results


def get_llm_client() -> LocalLLMClient:
    """Get or create global LLM client."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LocalLLMClient()
        if not _llm_client.is_healthy():
            raise RuntimeError(
                f"vLLM server at {config.VLLM_API_BASE} is not responding. "
                "Check that vLLM is running."
            )
    return _llm_client


_llm_client: Optional[LocalLLMClient] = None
