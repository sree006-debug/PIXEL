"""The ONLY module that moves the mouse or presses keys.

Controller.execute() takes an Action that already passed parse_action().
Immediately before performing it, the execution-time safety policy
(agent.safety) is checked again, including a blocked word split over several
TYPE actions; a violation raises PolicyViolation and nothing is performed.
In dry-run mode it only logs what it would do (the policy is still enforced).
"""

import logging
import time

from agent import screen
from agent.actions import Action
from agent.safety import PolicyViolation, TypingGuard, policy_problem

# pyautogui records the screen corners (failsafe points) when it is imported,
# so DPI awareness must be enabled first or the corners are computed wrong.
screen.enable_dpi_awareness()
import pyautogui  # noqa: E402

import config  # noqa: E402

log = logging.getLogger("agent.controller")

pyautogui.FAILSAFE = True    # mouse into a screen corner = emergency stop
pyautogui.PAUSE = 0.1        # small pause after every pyautogui call

WHEEL_DELTA = 120            # Windows: one mouse-wheel notch = 120 units


class EmergencyStop(Exception):
    """The user triggered the PyAutoGUI failsafe (mouse in a screen corner)."""


def describe(action: Action) -> str:
    """One-line human-readable description, used for logs."""
    a = action
    if a.action in ("CLICK", "DOUBLE_CLICK", "MOVE"):
        return f"{a.action} at ({a.x}, {a.y})"
    if a.action == "DRAG":
        return f"DRAG from ({a.x}, {a.y}) to ({a.to_x}, {a.to_y})"
    if a.action == "TYPE":
        return f"TYPE {a.text!r}"
    if a.action == "PRESS":
        return f"PRESS {a.key}"
    if a.action == "HOTKEY":
        return f"HOTKEY {'+'.join(a.keys)}"
    if a.action == "SCROLL":
        where = f" at ({a.x}, {a.y})" if a.x is not None else ""
        return f"SCROLL {a.amount:+d}{where}"
    if a.action == "WAIT":
        return f"WAIT {a.seconds}s"
    return a.action  # FINISH, RECOVER


class Controller:
    def __init__(self, dry_run: bool = config.DRY_RUN, wait=None):
        """wait: optional (seconds) -> bool used for the WAIT action instead of
        time.sleep; it returns True when a stop was requested (e.g.
        threading.Event.wait), so a WAIT ends early after Stop."""
        self.dry_run = dry_run
        self.wait = wait
        self.typing_guard = TypingGuard()

    def begin_task(self) -> None:
        """Forget text typed during a previous task (called by Agent.run)."""
        self.typing_guard.reset()

    def get_mouse_position(self):
        return tuple(pyautogui.position())

    def execute(self, action: Action) -> str:
        """Perform one validated action. Returns the log line.

        Raises PolicyViolation (nothing performed) if the action breaks the
        execution-time safety policy, and EmergencyStop on the failsafe.
        """
        if not isinstance(action, Action):
            raise TypeError("Controller only executes Action objects from parse_action()")
        problem = policy_problem(action) or self.typing_guard.problem(action)
        if problem:
            log.warning("BLOCKED before execution: %s (%s)", describe(action), problem)
            raise PolicyViolation(problem)

        line = describe(action)
        if action.reason:
            line += f"  # {action.reason}"

        if self.dry_run:
            line = "[DRY RUN] " + line
            log.info(line)
            self.typing_guard.record(action)
            return line

        log.info(line)
        try:
            self._perform(action)
        except pyautogui.FailSafeException as e:
            # Re-raised under our own name so callers never need to import pyautogui.
            raise EmergencyStop("PyAutoGUI failsafe triggered (mouse moved to a screen corner)") from e
        self.typing_guard.record(action)
        return line

    def _perform(self, a: Action):
        if a.action == "CLICK":
            pyautogui.click(a.x, a.y)
        elif a.action == "DOUBLE_CLICK":
            pyautogui.doubleClick(a.x, a.y)
        elif a.action == "MOVE":
            pyautogui.moveTo(a.x, a.y, duration=self._duration(a))
        elif a.action == "DRAG":
            pyautogui.moveTo(a.x, a.y)
            pyautogui.dragTo(a.to_x, a.to_y, duration=self._duration(a), button="left")
        elif a.action == "TYPE":
            pyautogui.write(a.text, interval=config.TYPE_INTERVAL)
        elif a.action == "PRESS":
            pyautogui.press(a.key)
        elif a.action == "HOTKEY":
            pyautogui.hotkey(*a.keys)
        elif a.action == "SCROLL":
            pyautogui.scroll(a.amount * WHEEL_DELTA, x=a.x, y=a.y)
        elif a.action == "WAIT":
            if self.wait is not None:
                self.wait(a.seconds)       # returns early when a stop is requested
            else:
                time.sleep(a.seconds)
        elif a.action == "RECOVER":
            # Close any open menu/dialog; the agent loop then re-observes the screen.
            pyautogui.press("esc")
        elif a.action == "FINISH":
            pass  # nothing to do on screen; the agent loop stops
        else:
            raise ValueError(f"Unhandled action {a.action}")

    @staticmethod
    def _duration(a: Action) -> float:
        return a.duration if a.duration is not None else config.MOVE_DURATION
