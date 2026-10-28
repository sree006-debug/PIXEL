"""Structured actions and strict validation.

Every action the agent performs (later: every action the VLM proposes) is a
plain dict such as

    {"action": "CLICK", "x": 812, "y": 431, "reason": "Click the Chrome icon"}

parse_action() checks it against the rules below and returns a frozen Action.
Anything that breaks a rule raises ActionError with a human-readable message
(later this message is sent back to the VLM so it can correct itself).
Only Action objects returned by parse_action() are ever executed.
"""

import math
import re
from dataclasses import dataclass

import config


class ActionError(ValueError):
    """Raised when an action is malformed or unsafe."""


# --- Which fields each action needs / may have --------------------------------

REQUIRED_FIELDS = {
    "CLICK":        {"x", "y"},
    "DOUBLE_CLICK": {"x", "y"},
    "MOVE":         {"x", "y"},
    "DRAG":         {"x", "y", "to_x", "to_y"},
    "TYPE":         {"text"},
    "PRESS":        {"key"},
    "HOTKEY":       {"keys"},
    "SCROLL":       {"amount"},
    "WAIT":         {"seconds"},
    "FINISH":       set(),
    "RECOVER":      set(),
}

OPTIONAL_FIELDS = {
    "MOVE":   {"duration"},
    "DRAG":   {"duration"},
    "SCROLL": {"x", "y"},
}

# Allowed on every action: free-text explanation fields.
COMMON_FIELDS = {"action", "reason", "expected_outcome"}

ACTION_TYPES = set(REQUIRED_FIELDS)

# --- Keyboard rules -----------------------------------------------------------

MODIFIER_KEYS = {"ctrl", "shift", "alt", "win"}
ALLOWED_KEYS = (
    MODIFIER_KEYS
    | {"enter", "esc", "tab", "backspace", "delete", "space",
       "up", "down", "left", "right", "home", "end", "pageup", "pagedown"}
    | {f"f{i}" for i in range(1, 13)}
    | set("abcdefghijklmnopqrstuvwxyz0123456789")
    | set("+-*/=.,;'[]\\`")
)

# Key combinations that could lock, log out, kill processes, open a command
# runner, or close the current window/tab. Compared as sets, so order doesn't matter.
# Closing tabs/documents (ctrl+w, ctrl+f4, ctrl+shift+w) is blocked everywhere: the
# controller cannot tell which window has focus, and closing the wrong window (an
# unsaved document, the PIXEL window itself) cannot be undone. No supported task needs it.
BLOCKED_HOTKEYS = [
    {"win", "l"},             # lock screen
    {"win", "r"},             # Run dialog (can launch any command)
    {"win", "x"},             # power-user menu (shutdown, terminal)
    {"ctrl", "alt", "delete"},
    {"ctrl", "shift", "esc"},  # Task Manager
    {"alt", "f4"},            # close window / shutdown dialog on the desktop
    {"ctrl", "w"},            # close tab / window (GAP-10)
    {"ctrl", "f4"},           # close tab / document
    {"ctrl", "shift", "w"},   # close browser window
]

# TYPE text that tries to start a shell, a terminal, a registry editor or a
# destructive/scripting tool, or that would expose a credential. This is a simple
# extra safety net, not a sandbox: we still never execute model output as code.
# Each category is a separate pattern so a rejection can say which rule matched.
BLOCKED_TEXT_RULES = (
    ("shell or terminal",
     r"\b(cmd(\.exe)?|powershell(_ise)?|pwsh|bash|wsl|terminal|conhost|"
     r"windows\s+terminal|command\s+prompt|windows\s+powershell)\b"),
    ("registry or system tool",
     r"\b(regedit|registry\s+editor|reg\s+(add|delete)|diskpart|bcdedit|gpedit|"
     r"mmc|msconfig|services\.msc)\b"),
    ("destructive command",
     r"\b(shutdown|taskkill|rmdir|rd\s+/s|remove-item|cipher\s+/w)\b"
     r"|\bformat\s+[a-z]:|rm\s+-rf|del\s+/|\bdel\s+\S*[*?]"),
    ("code execution",
     r"\b(invoke-expression|iex|mshta|wscript|cscript|rundll32|certutil|bitsadmin|"
     r"python(\.exe)?\s+-c|node\s+-e)\b|\|\s*(sh|bash|iex)\b"),
    ("credential", r"AIza[0-9A-Za-z_\-]{30,}|\bsk-[A-Za-z0-9_\-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY"),
)
BLOCKED_TEXT = re.compile("|".join(f"(?:{p})" for _, p in BLOCKED_TEXT_RULES), re.IGNORECASE)
_BLOCKED_TEXT_COMPILED = [(name, re.compile(p, re.IGNORECASE)) for name, p in BLOCKED_TEXT_RULES]

# A whole TYPE text that is only a launcher name: typed into Start search and
# followed by Enter it opens a terminal. Checked on the whole text so the word is
# not blocked inside ordinary sentences (e.g. "wt" in a unit table).
BLOCKED_LAUNCH_NAMES = re.compile(r"^\s*(wt|wt\.exe|cmd|regedit|pwsh)\s*$", re.IGNORECASE)

# Dangerous names written with spaces inside them ("power shell", "reg edit")
# are caught by comparing with all whitespace removed. Only long, unambiguous
# names are listed, so ordinary text cannot match by accident.
COMPACT_BLOCKED = ("powershell", "windowsterminal", "commandprompt", "registryeditor",
                   "regedit", "taskkill", "diskpart", "bcdedit", "cmd.exe")


def blocked_text_reason(text: str) -> str | None:
    """Why this TYPE text is blocked, or None. Case- and whitespace-insensitive."""
    if not isinstance(text, str):
        return None
    for name, pattern in _BLOCKED_TEXT_COMPILED:
        if pattern.search(text):
            return name
    if BLOCKED_LAUNCH_NAMES.match(text):
        return "shell or terminal"
    compact = re.sub(r"\s+", "", text).lower()
    for word in COMPACT_BLOCKED:
        if word in compact:
            return "shell or terminal (written with spaces)"
    return None


def blocked_hotkey(keys) -> bool:
    """True if this key combination is on the blocklist (order and case don't matter)."""
    try:
        keyset = {str(k).strip().lower() for k in keys}
    except TypeError:
        return False
    return any(blocked <= keyset for blocked in BLOCKED_HOTKEYS)


@dataclass(frozen=True)
class Action:
    action: str
    x: int | None = None
    y: int | None = None
    to_x: int | None = None
    to_y: int | None = None
    text: str | None = None
    key: str | None = None
    keys: tuple[str, ...] | None = None
    amount: int | None = None
    seconds: float | None = None
    duration: float | None = None
    reason: str = ""
    expected_outcome: str = ""


# --- Small checking helpers ---------------------------------------------------

def _check_int(name, value):
    # bool is a subclass of int in Python, so reject it explicitly.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ActionError(f"'{name}' must be an integer, got {value!r}")
    return value


def _check_number(name, value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ActionError(f"'{name}' must be a number, got {value!r}")
    if not low <= value <= high:
        raise ActionError(f"'{name}' must be between {low} and {high}, got {value}")
    return float(value)


def _check_point(x_name, y_name, data, screen_size):
    width, height = screen_size
    x = _check_int(x_name, data[x_name])
    y = _check_int(y_name, data[y_name])
    if not (0 <= x < width and 0 <= y < height):
        raise ActionError(
            f"point ({x}, {y}) is outside the screen (0..{width - 1}, 0..{height - 1})")
    # Screen corners trigger PyAutoGUI's emergency-stop failsafe.
    m = config.CORNER_MARGIN
    near_x_edge = x < m or x >= width - m
    near_y_edge = y < m or y >= height - m
    if near_x_edge and near_y_edge:
        raise ActionError(f"point ({x}, {y}) is too close to a screen corner (failsafe zone)")
    return x, y


def _check_key(key):
    if not isinstance(key, str):
        raise ActionError(f"key must be a string, got {key!r}")
    key = key.strip().lower()
    if key not in ALLOWED_KEYS:
        raise ActionError(f"key {key!r} is not allowed")
    return key


def _check_text_field(name, value):
    if not isinstance(value, str):
        raise ActionError(f"'{name}' must be a string, got {value!r}")
    return value


# --- Main entry point ---------------------------------------------------------

def parse_action(data, screen_size):
    """Validate a raw action dict and return an Action, or raise ActionError.

    screen_size is (width, height) in the pixel units the mouse uses.
    """
    if not isinstance(data, dict):
        raise ActionError(f"action must be a JSON object, got {type(data).__name__}")

    name = data.get("action")
    if not isinstance(name, str) or name.strip().upper() not in ACTION_TYPES:
        raise ActionError(
            f"unknown action {name!r}; allowed: {', '.join(sorted(ACTION_TYPES))}")
    name = name.strip().upper()

    required = REQUIRED_FIELDS[name]
    allowed = required | OPTIONAL_FIELDS.get(name, set()) | COMMON_FIELDS
    missing = required - data.keys()
    if missing:
        raise ActionError(f"{name} is missing required field(s): {', '.join(sorted(missing))}")
    unexpected = data.keys() - allowed
    if unexpected:
        raise ActionError(f"{name} does not accept field(s): {', '.join(sorted(unexpected))}")

    fields = {
        "action": name,
        "reason": _check_text_field("reason", data.get("reason", "")),
        "expected_outcome": _check_text_field("expected_outcome", data.get("expected_outcome", "")),
    }

    if name in ("CLICK", "DOUBLE_CLICK", "MOVE", "DRAG"):
        fields["x"], fields["y"] = _check_point("x", "y", data, screen_size)

    if name == "DRAG":
        fields["to_x"], fields["to_y"] = _check_point("to_x", "to_y", data, screen_size)

    if name in ("MOVE", "DRAG") and "duration" in data:
        fields["duration"] = _check_number("duration", data["duration"], 0.0, config.MAX_MOVE_DURATION)

    if name == "TYPE":
        text = _check_text_field("text", data["text"])
        if not text:
            raise ActionError("TYPE text must not be empty")
        if len(text) > config.MAX_TYPE_CHARS:
            raise ActionError(
                f"TYPE text is {len(text)} characters; the limit is {config.MAX_TYPE_CHARS}")
        # pyautogui can only type characters that exist on the keyboard.
        bad = [c for c in text if not (c.isascii() and (c.isprintable() or c in "\n\t"))]
        if bad:
            raise ActionError(f"TYPE text contains characters that cannot be typed: {bad[:5]}")
        rule = blocked_text_reason(text)
        if rule:
            raise ActionError(f"TYPE text contains a blocked command word ({rule})")
        fields["text"] = text

    if name == "PRESS":
        fields["key"] = _check_key(data["key"])

    if name == "HOTKEY":
        keys = data["keys"]
        if isinstance(keys, list) and len(keys) == 1:
            raise ActionError(f"HOTKEY needs 2 to 4 keys; for a single key use PRESS, "
                              f"e.g. {{\"action\": \"PRESS\", \"key\": {keys[0]!r}}}")
        if not isinstance(keys, list) or not 2 <= len(keys) <= 4:
            raise ActionError("HOTKEY 'keys' must be a list of 2 to 4 keys, e.g. [\"ctrl\", \"s\"]")
        keys = [_check_key(k) for k in keys]
        if len(set(keys)) != len(keys):
            raise ActionError(f"HOTKEY has repeated keys: {keys}")
        if not MODIFIER_KEYS & set(keys):
            raise ActionError(f"HOTKEY must include a modifier (ctrl/shift/alt/win): {keys}")
        if blocked_hotkey(keys):
            raise ActionError(f"HOTKEY {'+'.join(keys)} is blocked for safety")
        fields["keys"] = tuple(keys)

    if name == "SCROLL":
        amount = _check_int("amount", data["amount"])
        if amount == 0 or abs(amount) > config.MAX_SCROLL:
            raise ActionError(
                f"SCROLL amount must be non-zero and at most {config.MAX_SCROLL} notches, got {amount}")
        fields["amount"] = amount
        if "x" in data or "y" in data:
            if not ("x" in data and "y" in data):
                raise ActionError("SCROLL needs both x and y, or neither")
            fields["x"], fields["y"] = _check_point("x", "y", data, screen_size)

    if name == "WAIT":
        fields["seconds"] = _check_number("seconds", data["seconds"], config.MIN_WAIT, config.MAX_WAIT)

    return Action(**fields)
