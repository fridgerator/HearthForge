"""
Async HTTP client for Ollama's /api/chat endpoint.
Thin wrapper - no framework dependencies.
"""

import json
import logging

import httpx

from config import OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_OPTIONS

logger = logging.getLogger(__name__)


class OllamaClient:
    def __init__(
        self,
        host: str = OLLAMA_HOST,
        model: str = OLLAMA_MODEL,
    ):
        self.host = host.rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(300.0))

    async def chat(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float | None = None,
    ) -> str:
        """
        Send a chat completion request to Ollama.
        Returns the assistant's response text.
        """
        options = {**OLLAMA_OPTIONS}
        if temperature is not None:
            options["temperature"] = temperature

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "options": options,
            "stream": False,
        }

        url = f"{self.host}/api/chat"
        logger.debug(f"POST {url} model={self.model}")

        try:
            response = await self._client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return data["message"]["content"]
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama HTTP error: {e.response.status_code} - {e.response.text}")
            raise
        except httpx.ConnectError:
            logger.error(f"Cannot connect to Ollama at {self.host}")
            raise
        except Exception as e:
            logger.error(f"Ollama request failed: {e}")
            raise

    async def chat_json(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float | None = None,
    ) -> dict:
        """
        Chat with Ollama and parse the response as JSON.
        Strips markdown code fences if present.
        """
        raw = await self.chat(system_prompt, user_message, temperature)

        # Strip common markdown wrapping
        cleaned = raw.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Ollama response as JSON: {e}")
            logger.debug(f"Raw response: {raw[:500]}")
            raise ValueError(f"LLM did not return valid JSON: {e}") from e

    async def close(self):
        await self._client.aclose()
