"""Gemini implementation of VLMProvider.

This is the ONLY file that imports the Google GenAI SDK. It uses the
Interactions API (client.interactions.create), which Google recommends for
new projects as of June 2026 (google-genai >= 2.3.0).

It only turns (instructions, text, screenshot) into the model's text reply.
Parsing that reply into a Decision and validating it is shared code in vlm.py.
"""

import base64
import io
import os

from google import genai
from google.genai import types

from agent.vlm import VLMProvider
from agent.vlm_types import VLMAPIError, VLMError, VLMResponseError, VLMTimeoutError


class GeminiProvider(VLMProvider):
    name = "gemini"
    API_KEY_ENV = "GEMINI_API_KEY"
    DEFAULT_MODEL = "gemini-3.8-flash"
    DEFAULT_THINKING_LEVEL = "low"   # used when GEMINI_THINKING_LEVEL is empty

    def __init__(self, api_key: str, model: str, timeout: float):
        super().__init__(model, timeout)
        # minimal | low | medium | high (not every model accepts every level)
        self.thinking_level = (os.getenv("GEMINI_THINKING_LEVEL", "").strip().lower()
                               or self.DEFAULT_THINKING_LEVEL)
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(timeout * 1000)),  # milliseconds
        )

    def _generate(self, system_prompt, user_text, image, schema):
        try:
            interaction = self._client.interactions.create(
                model=self.model,
                store=False,                         # don't keep screenshots server-side
                system_instruction=system_prompt,
                input=[
                    {"type": "text", "text": user_text},
                    {"type": "image", "data": image_to_base64_png(image),
                     "mime_type": "image/png"},
                ],
                response_format={"type": "text", "mime_type": "application/json",
                                 "schema": schema},
                generation_config={"thinking_level": self.thinking_level},
            )
        except Exception as e:  # any SDK/network error -> our own error type
            raise translate_error(e) from e
        return response_text(interaction)


# --- Gemini-specific helpers (module-level so tests can use them) --------------

def image_to_base64_png(image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def response_text(interaction) -> str:
    """Get the text reply out of a Gemini interaction, or raise VLMResponseError."""
    text = getattr(interaction, "output_text", None)
    if not isinstance(text, str) or not text.strip():
        status = getattr(interaction, "status", "unknown")
        raise VLMResponseError(f"Gemini returned an empty response (status: {status})")
    return text


def translate_error(error: Exception) -> VLMError:
    """Map SDK/network exceptions to provider-neutral VLM errors.

    The Interactions API's exception classes live in a private SDK module, so
    we classify by name/attributes instead of importing them.
    """
    if isinstance(error, VLMError):
        return error
    if "timeout" in type(error).__name__.lower():
        return VLMTimeoutError(f"Gemini request timed out: {error}")
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    message = str(error).strip() or type(error).__name__
    prefix = f"Gemini API error {status}" if status else f"Gemini API error ({type(error).__name__})"
    return VLMAPIError(f"{prefix}: {message[:300]}")
