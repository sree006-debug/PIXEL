"""Provider-independent VLM interface.

The rest of the application talks ONLY to this module:

    provider = create_provider()                 # picks provider from .env
    decision = provider.decide_action(request)   # -> Decision (never raises for API trouble)
    action   = decision_to_action(decision, screen_size)   # -> validated Action
    located  = provider.ground(grounding_request)  # -> GroundingResult (data only)

Adding a provider (Claude, OpenAI, Qwen, a local model, ...) means:
  1. write agent/<name>_provider.py with a VLMProvider subclass that implements
     _generate() - the only method that talks to that provider's API
  2. add one line to PROVIDERS below
Nothing else (validator, controller, loop, GUI) changes.
"""

import importlib
import json
import os
import re

import config
from agent import prompts, screen
from agent.actions import Action, ActionError, parse_action
from agent.vlm_types import (Decision, GroundingRequest, GroundingResult, Verification,
                             VLMConfigError, VLMError, VLMRequest, VLMResponseError)

# name -> "module:ClassName". Imported lazily, so an SDK is only loaded
# when its provider is actually selected.
PROVIDERS = {
    "gemini": "agent.gemini_provider:GeminiProvider",
}


class VLMProvider:
    """Base class. Subclasses implement _generate(); everything else is shared."""

    name = "base"
    API_KEY_ENV = ""        # e.g. "GEMINI_API_KEY"
    DEFAULT_MODEL = ""      # used when VLM_MODEL is empty

    def __init__(self, model: str, timeout: float = config.VLM_TIMEOUT):
        self.model = model
        self.timeout = timeout

    # --- the one provider-specific method ---------------------------------
    def _generate(self, system_prompt: str, user_text: str, image, schema: dict) -> str:
        """Send instructions + text + screenshot; return the model's raw text reply.

        Must raise a VLMError subclass (VLMAPIError, VLMTimeoutError,
        VLMResponseError) for any problem, never an SDK-specific exception.
        """
        raise NotImplementedError

    # --- shared, provider-independent logic -------------------------------
    def decide_action(self, request: VLMRequest) -> Decision:
        """Ask the model for the next action. Problems come back as ok=False."""
        text = prompts.build_decide_text(request.task, request.history, request.note)
        try:
            raw = self._generate(prompts.DECIDE_SYSTEM_PROMPT, text,
                                 self._prepare_image(request.screenshot),
                                 prompts.DECISION_SCHEMA)
        except VLMError as e:
            return Decision(ok=False, error=f"{type(e).__name__}: {e}")
        return parse_decision(raw)

    def verify_state(self, request: VLMRequest, expected_outcome: str) -> Verification:
        """Ask the model whether expected_outcome is visible on the screenshot."""
        text = prompts.build_verify_text(request.task, expected_outcome, request.history[-2:])
        try:
            raw = self._generate(prompts.VERIFY_SYSTEM_PROMPT, text,
                                 self._prepare_image(request.screenshot),
                                 prompts.VERIFICATION_SCHEMA)
        except VLMError as e:
            return Verification(ok=False, error=f"{type(e).__name__}: {e}")
        return parse_verification(raw)

    def ground(self, request: GroundingRequest) -> GroundingResult:
        """Ask the model where request.target is (0-1000 box). Never raises for API trouble."""
        text = prompts.build_ground_text(request.target, request.context)
        try:
            raw = self._generate(prompts.GROUND_SYSTEM_PROMPT, text,
                                 self._prepare_image(request.screenshot),
                                 prompts.GROUNDING_SCHEMA)
        except VLMError as e:
            return GroundingResult(ok=False, target=request.target,
                                   error=f"{type(e).__name__}: {e}")
        return parse_grounding(raw, request.target)

    @staticmethod
    def _prepare_image(image):
        # Coordinates are on a 0-1000 grid, so shrinking the image is safe
        # and saves tokens/latency.
        return screen.resize_for_model(image, config.VLM_IMAGE_MAX_WIDTH)


# --- Provider selection ----------------------------------------------------------

def create_provider(provider: str | None = None, model: str | None = None,
                    env=None) -> VLMProvider:
    """Build the provider named by VLM_PROVIDER (or the argument).

    Raises VLMConfigError with a readable message if something is missing.
    env defaults to os.environ (which config.py fills from .env).
    """
    env = os.environ if env is None else env
    name = (provider or env.get("VLM_PROVIDER", "")).strip().lower()
    if not name:
        raise VLMConfigError(
            f"VLM_PROVIDER is not set. Put e.g. VLM_PROVIDER=gemini in .env "
            f"(available: {', '.join(PROVIDERS)})")
    if name not in PROVIDERS:
        raise VLMConfigError(
            f"Unknown VLM provider {name!r} (available: {', '.join(PROVIDERS)})")

    module_name, class_name = PROVIDERS[name].split(":")
    try:
        cls = getattr(importlib.import_module(module_name), class_name)
    except ImportError as e:
        raise VLMConfigError(
            f"Provider {name!r} needs a package that is not installed: {e}") from e

    api_key = env.get(cls.API_KEY_ENV, "").strip()
    if not api_key:
        raise VLMConfigError(f"{cls.API_KEY_ENV} is not set. Add it to .env (see README).")

    model = (model or env.get("VLM_MODEL", "")).strip() or cls.DEFAULT_MODEL
    return cls(api_key=api_key, model=model, timeout=config.VLM_TIMEOUT)


# --- Parsing the model's JSON (same contract for every provider) ---------------

def extract_json(text: str) -> dict:
    """Pull a JSON object out of a model reply (tolerates ```json fences / chatter)."""
    if not text or not text.strip():
        raise VLMResponseError("empty response")
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    if candidate.lstrip().startswith("["):
        raise VLMResponseError("expected one JSON object, got a JSON array")
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end < start:
        raise VLMResponseError(f"no JSON object in response: {text[:200]!r}")
    try:
        data = json.loads(candidate[start:end + 1])
    except json.JSONDecodeError as e:
        raise VLMResponseError(f"malformed JSON ({e.msg}): {text[:200]!r}") from e
    return data


def parse_decision(raw_text: str) -> Decision:
    """Turn a raw model reply into a Decision (ok=False if it is unusable)."""
    try:
        data = extract_json(raw_text)
    except VLMResponseError as e:
        return Decision(ok=False, error=str(e), raw_text=raw_text or "")

    action = data.get("action")
    if isinstance(action, str):
        # Model answered with a flat action ({"action": "CLICK", "x": ...}).
        action = {k: v for k, v in data.items() if k != "observation"}
    if not isinstance(action, dict):
        return Decision(ok=False, error="response has no 'action' object", raw_text=raw_text)

    return Decision(ok=True, observation=str(data.get("observation", "")),
                    action=action, raw_text=raw_text)


def parse_verification(raw_text: str) -> Verification:
    try:
        data = extract_json(raw_text)
    except VLMResponseError as e:
        return Verification(ok=False, error=str(e), raw_text=raw_text or "")
    if not isinstance(data.get("success"), bool):
        return Verification(ok=False, error="'success' must be true or false", raw_text=raw_text)
    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) \
            or not 0 <= confidence <= 1:
        confidence = None               # missing or nonsense -> unknown
    reason = data.get("reason", data.get("evidence", ""))
    return Verification(ok=True, success=data["success"], reason=str(reason),
                        confidence=confidence,
                        confirmation_needed=data.get("confirmation_needed") is True,
                        raw_text=raw_text)


_BOX_FIELDS = ("x_min", "y_min", "x_max", "y_max")
_REASON_RE = re.compile(r'"reason"\s*:\s*("(?:[^"\\]|\\.)*")')


def _salvage_reason(raw_text) -> str:
    """Best-effort "reason" string from a reply that is not valid JSON (e.g. truncated)."""
    match = _REASON_RE.search(raw_text or "")
    if not match:
        return ""
    try:
        return str(json.loads(match.group(1)))
    except json.JSONDecodeError:
        return ""


def parse_grounding(raw_text: str, target: str = "") -> GroundingResult:
    """Turn a raw grounding reply into a GroundingResult (ok=False if it is unusable).

    Low confidence is NOT an error: the result stays ok/found with its box, and
    GroundingResult.is_usable decides whether it may be acted on. Errors keep
    raw_text and, when it can be read, the model's reason for debugging.
    """
    def error(message, reason=""):
        return GroundingResult(ok=False, target=target, error=message, reason=reason,
                               raw_text=raw_text or "")

    try:
        data = extract_json(raw_text)
    except VLMResponseError as e:
        return error(str(e), _salvage_reason(raw_text))
    reason = str(data.get("reason", ""))
    found = data.get("found")
    if not isinstance(found, bool):
        return error("'found' must be true or false", reason)

    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) \
            or not 0 <= confidence <= 1:
        if found:
            return error(f"'confidence' must be a number from 0 to 1, got {confidence!r}",
                         reason)
        confidence = None               # not found: confidence is only informative

    if not found:
        return GroundingResult(ok=True, found=False, target=target, confidence=confidence,
                               reason=reason, raw_text=raw_text)

    box = []
    for key in _BOX_FIELDS:
        if key not in data:
            return error(f"incomplete box: '{key}' is missing", reason)
        value = data[key]
        if isinstance(value, bool) or not isinstance(value, int) \
                or not 0 <= value <= prompts.COORD_SCALE:
            return error(f"'{key}' must be an integer on the 0-{prompts.COORD_SCALE} grid, "
                         f"got {value!r}", reason)
        box.append(value)
    x_min, y_min, x_max, y_max = box
    if not (x_min < x_max and y_min < y_max):
        return error(f"invalid box {box}: need x_min < x_max and y_min < y_max", reason)
    return GroundingResult(ok=True, found=True, target=target, bbox=tuple(box),
                           confidence=float(confidence), reason=reason, raw_text=raw_text)


# --- From model output to a validated Action ------------------------------------

_POINT_FIELDS = ("x", "y", "to_x", "to_y")


def decision_to_action(decision: Decision, screen_size) -> Action:
    """Convert the model's 0-1000 coordinates to pixels, then run parse_action().

    This is the ONLY way model output becomes an Action. Raises ActionError
    (with a message that can be sent back to the model) if anything is wrong.
    """
    if not decision.ok or decision.action is None:
        raise ActionError(f"no usable action: {decision.error or 'empty decision'}")

    # Drop fields the model filled with null (e.g. "x": null on a TYPE action).
    data = {k: v for k, v in decision.action.items() if v is not None}

    for x_key, y_key in (("x", "y"), ("to_x", "to_y")):
        if x_key in data and y_key in data:
            nx, ny = data[x_key], data[y_key]
            for key, value in ((x_key, nx), (y_key, ny)):
                if isinstance(value, bool) or not isinstance(value, int) \
                        or not 0 <= value <= prompts.COORD_SCALE:
                    raise ActionError(
                        f"'{key}' must be an integer on the 0-{prompts.COORD_SCALE} grid, got {value!r}")
            data[x_key], data[y_key] = screen.normalized_to_pixels(
                nx, ny, screen_size, prompts.COORD_SCALE)

    return parse_action(data, screen_size)
