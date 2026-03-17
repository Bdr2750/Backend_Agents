import asyncio
import itertools
import json
import logging
from typing import Optional

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


class GeminiClient:
    def __init__(self, api_keys: list[str], model: str = "gemini-2.0-flash"):
        self._clients = [genai.Client(api_key=key) for key in api_keys]
        self._model = model
        self._key_cycle = itertools.cycle(range(len(self._clients)))
        self._lock = asyncio.Lock()

    async def _next_client(self) -> genai.Client:
        async with self._lock:
            idx = next(self._key_cycle)
        return self._clients[idx]

    async def generate(
        self,
        system_prompt: str,
        user_message: str,
    ) -> str:
        client = await self._next_client()

        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self._model,
                contents=user_message,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                ),
            )
            return response.text
        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            raise

    async def generate_text(
        self,
        system_prompt: str,
        user_message: str,
    ) -> str:
        client = await self._next_client()

        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self._model,
                contents=user_message,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                ),
            )
            return response.text
        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            raise
