"""LLM service for local vLLM inference with tensor parallelism.

Provides interface to local vLLM server (with tensor parallelism across GPUs) for:
- Phase 1: Pattern analysis & code generation
- Phase 3: Quality evaluation

For Phase 4 execution, uses generated deterministic code (no LLM).

The vLLM server should be started with:
    ./scripts/start_vllm.sh
This enables tensor parallelism across all available GPUs.

LocalLLMClient.generate() is one-shot: every call is an independent request,
no history carried between them. LLMSession is for the opposite case - a
generate-validate-retry loop against a single document, where each retry
should see its own earlier code and the failure that followed it rather than
re-deriving that context from scratch. Open one session per document's retry
loop and let it go out of scope when the loop ends; carrying it across
documents would grow the conversation (and the token cost) forever.
"""
import logging
import json
from typing import Optional, Dict, Any
import httpx
import requests
from . import config

logger = logging.getLogger(__name__)

# Track tensor parallelism info
_TP_INFO: Optional[Dict[str, Any]] = None


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
        """Check if vLLM server is responding and get tensor parallelism info."""
        try:
            response = requests.get(
                f"{self.api_base}/v1/models",
                timeout=5
            )
            if response.status_code == 200:
                # Fetch server info to get tensor parallelism details
                self._get_server_info()
                return True
            return False
        except Exception as e:
            logger.error(f"vLLM health check failed: {e}")
            return False

    def _get_server_info(self) -> Dict[str, Any]:
        """Get vLLM server info including tensor parallelism configuration."""
        global _TP_INFO
        try:
            response = requests.get(
                f"{self.api_base}/v1/models",
                timeout=5
            )
            response.raise_for_status()
            models = response.json()
            if models.get('data'):
                model_info = models['data'][0]
                _TP_INFO = {
                    "model": model_info.get('id'),
                    "max_model_len": model_info.get('max_model_len'),
                }
            return _TP_INFO or {}
        except Exception as e:
            logger.warning(f"Could not fetch server info: {e}")
            return {}

    def chat(self,
             messages: list,
             temperature: float = 0.0,
             max_tokens: int = 2000,
             json_schema: Optional[Dict] = None) -> str:
        """Send a full messages list (system/user/assistant turns) as-is.

        This is the shared primitive: generate() wraps a single prompt into a
        one-message list and calls this; LLMSession accumulates turns across
        a retry loop and calls this with the growing list.

        Args:
            messages: OpenAI-style chat messages, e.g.
                [{"role": "system", ...}, {"role": "user", ...}, ...]
            temperature: Sampling temperature (0.0 for deterministic)
            max_tokens: Maximum tokens in response
            json_schema: JSON schema for structured output (optional)

        Returns:
            Generated text (raw response content)
        """
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

    async def achat(self,
                     messages: list,
                     temperature: float = 0.0,
                     max_tokens: int = 2000,
                     json_schema: Optional[Dict] = None) -> str:
        """Async twin of chat(), for callers running under asyncio (the
        per-sheet fan-out in extraction/core/pipeline_agent.py). Same
        payload, same response shape, same error mapping - built on
        httpx.AsyncClient instead of requests so the request doesn't block
        the event loop.
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
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
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.completions_url, json=payload)
                response.raise_for_status()
                result = response.json()
                return result["choices"][0]["message"]["content"]
        except httpx.TimeoutException:
            raise TimeoutError(f"vLLM request timed out after {self.timeout}s")
        except httpx.HTTPError as e:
            raise RuntimeError(f"vLLM request failed: {e}")

    def generate(self,
                 prompt: str,
                 system_prompt: Optional[str] = None,
                 temperature: float = 0.0,
                 max_tokens: int = 2000,
                 json_schema: Optional[Dict] = None) -> str:
        """Generate text from the local vLLM model, as one independent request.

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
        return self.chat(messages, temperature, max_tokens, json_schema)

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


class LLMSession:
    """Stateful multi-turn conversation for one document's retry loop.

    Each turn sees every earlier turn: its own previous code and the failure
    that followed it, so a retry is "fix this" rather than a rebuilt prompt
    that re-derives the same document structure from nothing. Scope one
    session to one document's generate-validate-retry loop and drop it when
    the loop ends (pass or exhausted) - do not reuse across documents, or the
    conversation (and its token cost) grows without bound.
    """

    def __init__(self, client: LocalLLMClient, system_prompt: Optional[str] = None):
        self.client = client
        self.messages: list = []
        if system_prompt:
            self.messages.append({"role": "system", "content": system_prompt})

    def send(self, prompt: str, temperature: float = 0.0, max_tokens: int = 2000) -> str:
        """Add a user turn, get the model's reply, and record both turns.

        On failure the user turn is rolled back rather than left dangling,
        so a caller that retries send() does not build up consecutive user
        turns with no reply between them.
        """
        self.messages.append({"role": "user", "content": prompt})
        try:
            reply = self.client.chat(self.messages, temperature, max_tokens)
        except Exception:
            self.messages.pop()
            raise
        self.messages.append({"role": "assistant", "content": reply})
        return reply

    async def asend(self, prompt: str, temperature: float = 0.0, max_tokens: int = 2000) -> str:
        """Async twin of send(), same turn bookkeeping, via client.achat()."""
        self.messages.append({"role": "user", "content": prompt})
        try:
            reply = await self.client.achat(self.messages, temperature, max_tokens)
        except Exception:
            self.messages.pop()
            raise
        self.messages.append({"role": "assistant", "content": reply})
        return reply


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
