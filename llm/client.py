import os

import aiohttp

# DeepSeek exposes an OpenAI-compatible chat-completions API. Defaults can be
# overridden via environment variables so the model can be swapped later.
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
REQUEST_TIMEOUT = 120  # seconds


class LLMError(Exception):
    """Raised when the LLM call cannot be completed. The caller is expected to
    handle this by falling back to deterministic behavior."""


class LLMClient:
    """Thin OpenAI-compatible chat client over aiohttp.

    The surface is intentionally provider-agnostic (``generate(system, user)``)
    so the underlying model/provider can be swapped without touching callers.
    """

    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        self.base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.model = model or os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)

    async def generate(self, system: str, user: str) -> str:
        """Send a single chat completion request and return the text content.

        Asks for a JSON object response (OpenAI-style JSON mode). Raises
        ``LLMError`` on missing key, non-200 status, or network failure.
        """
        if not self.api_key:
            raise LLMError("DEEPSEEK_API_KEY is not set")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "response_format": {"type": "json_object"},
        }

        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise LLMError(f"LLM request failed ({resp.status}): {body[:500]}")
                    data = await resp.json()
        except aiohttp.ClientError as e:
            raise LLMError(f"LLM network error: {e}") from e

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"Unexpected LLM response shape: {data}") from e
