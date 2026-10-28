"""Provider-neutral data types shared by every VLM provider.

Nothing in this file knows about Gemini, Claude, OpenAI, ... The rest of the
application only ever sees these types.
"""

from dataclasses import dataclass, field

from PIL import Image

import config
from agent.prompts import COORD_SCALE


# --- Errors a provider may raise internally ------------------------------------
# Providers translate their SDK's own exceptions into these, so callers never
# need to import a provider SDK to handle errors.

class VLMError(Exception):
    """Base class for all VLM problems."""


class VLMConfigError(VLMError):
    """Missing/invalid configuration (unknown provider, no API key, ...)."""


class VLMAPIError(VLMError):
    """The provider's API returned an error (bad key, quota, server error, ...)."""


class VLMTimeoutError(VLMError):
    """The request took longer than config.VLM_TIMEOUT."""


class VLMResponseError(VLMError):
    """The model answered, but the answer was empty or not the expected JSON."""


# --- Request / response --------------------------------------------------------

@dataclass
class VLMRequest:
    """Everything a provider needs to decide or verify one step."""
    task: str                                          # the user's goal
    screenshot: Image.Image                            # current screen
    history: list[str] = field(default_factory=list)   # previous steps, one line each
    note: str = ""                                     # extra context, e.g. last error


@dataclass
class Decision:
    """Normalized answer to 'what should I do next?'.

    action is a raw action dict in the MODEL's coordinate space (0-1000 grid).
    It has NOT been validated yet: it must go through
    vlm.decision_to_action() -> actions.parse_action() before execution.
    """
    ok: bool
    observation: str = ""          # what the model says it sees
    action: dict | None = None     # e.g. {"action": "CLICK", "x": 500, "y": 300, ...}
    error: str = ""                # filled when ok is False
    raw_text: str = ""             # the model's raw reply, for logs/debugging


@dataclass
class Verification:
    """Normalized answer to 'did the expected outcome happen?'."""
    ok: bool                       # False = the check itself failed (API error, bad JSON)
    success: bool = False          # the model's judgement, meaningful only if ok
    reason: str = ""
    confidence: float | None = None  # 0-1, None if the model gave none
    confirmation_needed: bool = False  # a confirmation dialog must be answered first
    error: str = ""
    raw_text: str = ""


# --- Visual grounding --------------------------------------------------------------

@dataclass
class GroundingRequest:
    """Where on the screenshot is `target`? (e.g. "Calculator")."""
    target: str                     # short description of ONE UI element
    screenshot: Image.Image
    context: str = ""               # optional extra context, e.g. the user's task


# GroundingResult.status values
GROUNDING_USABLE = "usable"                  # found, valid box, confidence >= threshold
GROUNDING_LOW_CONFIDENCE = "low_confidence"  # found, but confidence too low to act on
GROUNDING_NOT_FOUND = "not_found"            # the model says the target is not visible
GROUNDING_ERROR = "error"                    # API error / unusable reply


@dataclass
class GroundingResult:
    """Normalized answer to 'where is this target?'. Data only, never executed.

    bbox is (x_min, y_min, x_max, y_max) on the 0-1000 grid, x first. It is
    kept even when confidence is too low, so it can be logged.
    """
    ok: bool                        # False = the call itself failed (API error, bad JSON)
    found: bool = False             # the model's judgement, meaningful only if ok
    target: str = ""
    bbox: tuple[int, int, int, int] | None = None
    confidence: float | None = None  # 0-1
    reason: str = ""
    error: str = ""                 # filled when ok is False
    raw_text: str = ""

    @property
    def center(self) -> tuple[int, int] | None:
        """Centre of bbox on the 0-1000 grid, or None without a box."""
        if self.bbox is None:
            return None
        x_min, y_min, x_max, y_max = self.bbox
        return (x_min + x_max) // 2, (y_min + y_max) // 2

    def usable(self, min_confidence: float | None = None) -> bool:
        """ok, found, valid box and confidence >= min_confidence
        (default config.GROUNDING_MIN_CONFIDENCE)."""
        if min_confidence is None:
            min_confidence = config.GROUNDING_MIN_CONFIDENCE
        return (self.ok and self.found and _valid_bbox(self.bbox)
                and self.confidence is not None and self.confidence >= min_confidence)

    @property
    def is_usable(self) -> bool:
        return self.usable()

    @property
    def status(self) -> str:
        """One of GROUNDING_USABLE / _LOW_CONFIDENCE / _NOT_FOUND / _ERROR."""
        if not self.ok:
            return GROUNDING_ERROR
        if not self.found:
            return GROUNDING_NOT_FOUND
        return GROUNDING_USABLE if self.is_usable else GROUNDING_LOW_CONFIDENCE

    def to_dict(self) -> dict:
        """Plain JSON-friendly form for logs."""
        data = {"ok": self.ok, "status": self.status, "found": self.found,
                "target": self.target,
                "bbox": list(self.bbox) if self.bbox is not None else None,
                "confidence": self.confidence, "reason": self.reason}
        if self.error:
            data["error"] = self.error
        if not self.ok and self.raw_text:
            data["raw_text"] = self.raw_text   # keep the bad reply for debugging
        return data


def _valid_bbox(bbox) -> bool:
    """True for (x_min, y_min, x_max, y_max) integers on the 0-COORD_SCALE grid
    with x_min < x_max and y_min < y_max."""
    if not isinstance(bbox, (tuple, list)) or len(bbox) != 4:
        return False
    if any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= COORD_SCALE
           for v in bbox):
        return False
    x_min, y_min, x_max, y_max = bbox
    return x_min < x_max and y_min < y_max
