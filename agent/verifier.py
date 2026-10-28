"""Verification: did the expected outcome really happen?

1. vlm_check(): provider-independent visual check via provider.verify_state().
2. Deterministic checks: tiny functions returning (passed, reason). A task can
   supply one as `final_check`; it must ALSO pass before FINISH counts as success.
   file_contains() is the first example; add more here as the project needs them.
"""

import os
import re
from pathlib import Path

import config
from agent import screen
from agent.vlm_types import VLMRequest


# Verdicts returned by vlm_check()
PASS = "PASS"          # expected outcome is visible
FAIL = "FAIL"          # it is not (reason says what is visible instead)
CONFIRM = "CONFIRM"    # not yet: a confirmation dialog must be answered first
UNKNOWN = "UNKNOWN"    # the check itself could not run (API error, bad JSON)


def vlm_check(provider, task, expected_outcome, screenshot, recent_steps):
    """Returns (verdict, reason), verdict being PASS, FAIL, CONFIRM or UNKNOWN.

    Only PASS means the outcome was confirmed; a low-confidence yes is a FAIL.
    """
    v = provider.verify_state(
        VLMRequest(task=task, screenshot=screenshot, history=list(recent_steps)),
        expected_outcome)
    if not v.ok:
        return UNKNOWN, f"verification unavailable ({v.error})"
    conf = "?" if v.confidence is None else f"{v.confidence:.2f}"
    if v.success and v.confidence is not None and v.confidence < config.VERIFY_MIN_CONFIDENCE:
        return FAIL, f"model said yes but confidence {conf} is too low: {v.reason}"
    if v.success:
        return PASS, f"{v.reason} (confidence {conf})"
    if v.confirmation_needed:
        return CONFIRM, f"confirmation dialog open: {v.reason}"
    return FAIL, f"{v.reason} (confidence {conf})"


# --- Deterministic checks -------------------------------------------------------

def expand_path(path):
    """Expand $env:VAR (PowerShell), %VAR% / $VAR and ~ in a path."""
    text = re.sub(r"\$env:(\w+)", lambda m: os.environ.get(m.group(1), m.group(0)),
                  str(path), flags=re.IGNORECASE)
    return Path(os.path.expanduser(os.path.expandvars(text)))


def resolve_path(path, desktop=None, home=None):
    r"""Turn a user-supplied path into the real file location.

    A path inside the default %USERPROFILE%\Desktop is moved to the real
    Desktop, which Windows may have redirected (e.g. to OneDrive\Desktop).
    Every other path is only expanded, never moved.
    desktop/home are only passed by tests; by default they are looked up.
    """
    p = expand_path(path)
    default_desktop = Path(home or os.path.expanduser("~")) / "Desktop"
    n = len(default_desktop.parts)
    # Windows paths are case-insensitive: compare part by part with normcase.
    if [os.path.normcase(x) for x in p.parts[:n]] != \
            [os.path.normcase(x) for x in default_desktop.parts]:
        return p
    real_desktop = Path(desktop or screen.get_desktop_path())
    return real_desktop.joinpath(*p.parts[n:])


def file_contains(path, expected_text="", min_lines=0, modified_after=None):
    r"""Check factory: passes if the file exists and contains expected_text.

    min_lines: the file must also have at least this many non-empty lines.
    modified_after: a time.time() value; the file must have been saved after
    it (e.g. the run's start), so a stale file from an earlier run can't pass.

    The path goes through resolve_path(), so %USERPROFILE%\Desktop\x.txt finds
    x.txt on a OneDrive-redirected Desktop. If the file is not there but does
    exist at the path exactly as given, that copy is used instead.
    """
    given = expand_path(path)
    resolved = resolve_path(path)

    def check():
        path = resolved
        if not path.is_file() and given.is_file():
            path = given
        if not path.is_file():
            return False, f"file {path} does not exist"
        # 1 s tolerance for file-system timestamp rounding.
        if modified_after is not None and path.stat().st_mtime < modified_after - 1:
            return False, f"file {path} exists but was not saved during this run"
        content = path.read_text(encoding="utf-8", errors="replace")
        if expected_text not in content:
            return False, f"file {path} exists but does not contain {expected_text!r}"
        lines = [line for line in content.splitlines() if line.strip()]
        if len(lines) < min_lines:
            return False, (f"file {path} has {len(lines)} non-empty line(s), "
                           f"expected at least {min_lines}")
        found = f"contains {expected_text!r}" if expected_text else "exists"
        if min_lines:
            found += f" with {len(lines)} non-empty lines"
        return True, f"file {path} {found}"

    check.path = resolved  # shown to the user by main.py
    return check
