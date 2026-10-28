"""Visual grounding: find ONE described UI element on a screenshot.

    proposal = ground_click(provider, screenshot, "the 7 button", screen_size)
    if proposal.ok:
        controller.execute(proposal.action)   # the CALLER decides to execute

    # or step by step:
    result = locate(provider, screenshot, "Calculator")    # -> GroundingResult
    proposal = propose_click(result, screen_size)          # -> GroundedClick

This module only returns data. It never moves the mouse or presses keys and
does not import the controller or pyautogui: a located target becomes an
Action only through vlm.decision_to_action() -> actions.parse_action(), and is
executed only by Controller.execute(), exactly like any planner action.

Works with every provider: it uses VLMProvider.ground(), which is shared code
on top of the provider's _generate().
"""

from dataclasses import dataclass

import config
from agent import prompts, screen
from agent.actions import Action, ActionError
from agent.vlm import decision_to_action
from agent.vlm_types import (GROUNDING_ERROR, GROUNDING_LOW_CONFIDENCE, GROUNDING_NOT_FOUND,
                             Decision, GroundingRequest, GroundingResult, _valid_bbox)

POINT_ACTIONS = ("CLICK", "DOUBLE_CLICK", "MOVE")

# GroundedClick.status values (besides the GroundingResult ones it passes through)
CLICK_PROPOSED = "proposed"         # a validated CLICK Action is ready (not executed)
CLICK_INVALID_BOX = "invalid_box"   # missing / malformed / zero-area box
CLICK_UNSAFE_BOX = "unsafe_box"     # valid box, but too large to point at one element
CLICK_INVALID_ACTION = "invalid_action"  # rejected by parse_action (e.g. corner zone)


def locate(provider, screenshot, target: str, context: str = "") -> GroundingResult:
    """Ask the provider where `target` is. Never raises for API trouble or bad input."""
    if not isinstance(target, str) or not target.strip():
        return GroundingResult(ok=False, target=str(target or ""),
                               error="grounding target must be a non-empty description")
    target = target.strip()
    return provider.ground(GroundingRequest(target=target, screenshot=screenshot,
                                            context=context))


def bbox_center(bbox) -> tuple[int, int]:
    """Centre of an (x_min, y_min, x_max, y_max) box, on the same 0-1000 grid."""
    x_min, y_min, x_max, y_max = bbox
    return (x_min + x_max) // 2, (y_min + y_max) // 2


def bbox_to_pixels(bbox, screen_size) -> tuple[int, int, int, int]:
    """Convert a 0-1000 box to screen pixels (rounded and clamped, like points)."""
    x_min, y_min, x_max, y_max = bbox
    left, top = screen.normalized_to_pixels(x_min, y_min, screen_size, prompts.COORD_SCALE)
    right, bottom = screen.normalized_to_pixels(x_max, y_max, screen_size, prompts.COORD_SCALE)
    return left, top, right, bottom


def to_click_action(result: GroundingResult, action: str = "CLICK", reason: str = "",
                    expected_outcome: str = "",
                    min_confidence: float | None = None) -> Decision:
    """Build a Decision that points at the centre of the located target.

    The Decision is in grid space and NOT validated: pass it to
    vlm.decision_to_action() before execution. A result that is not usable
    (error, not found, invalid box or low confidence) gives ok=False, which
    decision_to_action() rejects with the reason.
    """
    action = action.strip().upper()
    if action not in POINT_ACTIONS:
        raise ValueError(f"to_click_action supports {', '.join(POINT_ACTIONS)}, got {action!r}")
    if not result.usable(min_confidence):
        detail = result.error or result.reason or "no details"
        conf = "?" if result.confidence is None else f"{result.confidence:.2f}"
        return Decision(ok=False, error=f"grounding of {result.target!r} not usable "
                                        f"({result.status}, confidence {conf}): {detail}")
    x, y = result.center
    return Decision(ok=True, observation=result.reason,
                    action={"action": action, "x": x, "y": y,
                            "reason": reason or f"{action} {result.target}",
                            "expected_outcome": expected_outcome})


def bbox_safety_problem(bbox) -> str:
    """Why `bbox` is not safe to click the centre of, or "" if it is.

    Purely geometric, independent of confidence: a box must be valid on the
    0-1000 grid and small enough to pin down ONE element, i.e. each side at
    most config.GROUNDING_MAX_BOX_SIDE and the area at most
    config.GROUNDING_MAX_BOX_AREA of the screen.
    """
    if not _valid_bbox(bbox):
        return (f"invalid box {bbox!r}: need integers on the 0-{prompts.COORD_SCALE} grid "
                f"with x_min < x_max and y_min < y_max")
    x_min, y_min, x_max, y_max = bbox
    scale = prompts.COORD_SCALE
    width, height = (x_max - x_min) / scale, (y_max - y_min) / scale
    if width > config.GROUNDING_MAX_BOX_SIDE or height > config.GROUNDING_MAX_BOX_SIDE:
        return (f"box {list(bbox)} spans {width:.0%} x {height:.0%} of the screen; "
                f"each side may be at most {config.GROUNDING_MAX_BOX_SIDE:.0%}")
    if width * height > config.GROUNDING_MAX_BOX_AREA:
        return (f"box {list(bbox)} covers {width * height:.1%} of the screen; "
                f"at most {config.GROUNDING_MAX_BOX_AREA:.0%} is allowed")
    return ""


@dataclass(frozen=True)
class GroundedClick:
    """A PROPOSED click on a located target. Data only, never executed here.

    ok=True means `action` is a CLICK Action that already passed
    decision_to_action() -> parse_action(), in screen pixels. Executing it is
    left to the caller (only Controller.execute() performs actions).
    """
    ok: bool
    status: str                      # CLICK_PROPOSED or why it was rejected
    target: str = ""
    action: Action | None = None
    point: tuple[int, int] | None = None   # box centre on the 0-1000 grid
    bbox: tuple[int, int, int, int] | None = None
    confidence: float | None = None
    error: str = ""

    def to_dict(self) -> dict:
        """Plain JSON-friendly form for logs."""
        data = {"ok": self.ok, "status": self.status, "target": self.target,
                "bbox": list(self.bbox) if self.bbox is not None else None,
                "confidence": self.confidence,
                "point": list(self.point) if self.point is not None else None,
                "action": None}
        if self.action is not None:
            data["action"] = {"action": self.action.action, "x": self.action.x,
                              "y": self.action.y, "reason": self.action.reason}
        if self.error:
            data["error"] = self.error
        return data


def propose_click(result: GroundingResult, screen_size, reason: str = "",
                  expected_outcome: str = "",
                  min_confidence: float | None = None) -> GroundedClick:
    """Turn a GroundingResult into a validated, NOT executed, CLICK Action.

    Checks, in order: the call succeeded, the target was found, the box is
    valid, confidence >= min_confidence (default config.GROUNDING_MIN_CONFIDENCE),
    the box is small enough (bbox_safety_problem), and finally the usual
    to_click_action() -> decision_to_action() -> parse_action() validation.
    Never raises for a bad result; rejections come back as ok=False.
    """
    def reject(status, error):
        return GroundedClick(ok=False, status=status, target=result.target, bbox=result.bbox,
                             confidence=result.confidence, error=error)

    if not result.ok:
        return reject(GROUNDING_ERROR, result.error or "grounding call failed")
    if not result.found:
        return reject(GROUNDING_NOT_FOUND,
                      f"{result.target!r} not found: {result.reason or 'no details'}")
    if not _valid_bbox(result.bbox):
        return reject(CLICK_INVALID_BOX, bbox_safety_problem(result.bbox))
    # Confidence and geometry are separate checks: confidence 1.0 does not
    # make a screen-sized box safe, and a tiny box does not excuse low confidence.
    if not result.usable(min_confidence):
        conf = "?" if result.confidence is None else f"{result.confidence:.2f}"
        threshold = config.GROUNDING_MIN_CONFIDENCE if min_confidence is None else min_confidence
        return reject(GROUNDING_LOW_CONFIDENCE,
                      f"confidence {conf} is below the threshold {threshold}")
    problem = bbox_safety_problem(result.bbox)
    if problem:
        return reject(CLICK_UNSAFE_BOX, problem)

    decision = to_click_action(result, "CLICK", reason=reason,
                               expected_outcome=expected_outcome,
                               min_confidence=min_confidence)
    try:
        action = decision_to_action(decision, screen_size)
    except ActionError as e:
        return reject(CLICK_INVALID_ACTION, str(e))
    return GroundedClick(ok=True, status=CLICK_PROPOSED, target=result.target, action=action,
                         point=result.center, bbox=result.bbox, confidence=result.confidence)


def ground_click(provider, screenshot, target: str, screen_size, context: str = "",
                 reason: str = "", expected_outcome: str = "",
                 min_confidence: float | None = None) -> GroundedClick:
    """locate() + propose_click(): one grounding call, at most one PROPOSED click.

    Nothing is executed. Never raises for API trouble or a bad reply.
    """
    result = locate(provider, screenshot, target, context)
    return propose_click(result, screen_size, reason=reason or f"click {result.target}",
                         expected_outcome=expected_outcome, min_confidence=min_confidence)
