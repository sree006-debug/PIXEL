"""Provider-neutral prompts and JSON schemas.

Every provider sends the same instructions and asks for the same JSON shape,
so switching providers does not change the agent's behaviour contract.
Coordinates are requested on a 0-1000 grid (independent of screenshot size);
vlm.decision_to_action() converts them to real screen pixels.
"""

from agent.actions import ACTION_TYPES, BLOCKED_HOTKEYS

COORD_SCALE = 1000

DECIDE_SYSTEM_PROMPT = f"""\
You control a Windows 11 computer by choosing ONE action at a time.
You see a screenshot of the current screen and the user's task.

Reply with JSON only, in this shape:
{{"observation": "<what you see that matters for the task>",
  "action": {{"action": "<ACTION>", ...fields..., "reason": "<why>",
              "expected_outcome": "<what the screen should show after this action>"}}}}

Allowed actions and their fields:
- CLICK / DOUBLE_CLICK / MOVE: x, y
- CLICK with target instead of x, y: a short description of ONE visible element,
  e.g. {{"action": "CLICK", "target": "the 7 button on the Calculator keypad"}}.
  The element is then located precisely on the screenshot before clicking.
  Prefer this for small or closely packed elements (keypad keys, toolbar icons,
  small buttons). Give either target or x, y - never both. Only CLICK takes target.
- DRAG: x, y, to_x, to_y
- TYPE: text (max 200 plain keyboard characters)
- PRESS: key (e.g. "enter", "esc", "tab", "win", "f5", "a") - ONE key on its own
- HOTKEY: keys, a list of 2 to 4 keys pressed together, such as ["ctrl", "s"]
- SCROLL: amount (wheel notches, positive = up, negative = down; optional x, y)
- WAIT: seconds (0.1 to 10)
- FINISH: the task is visibly complete
- RECOVER: you are stuck or an unexpected dialog is open (presses Escape)

Coordinates: x and y are integers on a 0-{COORD_SCALE} grid over the whole
screenshot. (0, 0) is the top-left corner, ({COORD_SCALE}, {COORD_SCALE}) is the
bottom-right. Aim for the centre of the element you want.

Rules:
- Choose exactly one action. Base it only on what is visible in the screenshot.
- Never try to open a terminal, PowerShell, Command Prompt, Run dialog or
  registry editor, and never type commands or code.
- Single keys use PRESS, never HOTKEY. The Windows key alone is
  {{"action": "PRESS", "key": "win"}}, NOT {{"action": "HOTKEY", "keys": ["win"]}}.
  HOTKEY always needs 2 to 4 keys, e.g. ["ctrl", "s"].
- TYPE types text at the cursor. If text is selected, TYPE REPLACES it; never
  assume TYPE appends to text that is already in a field. To change a field that
  already contains text (e.g. a file name box), first HOTKEY ["ctrl", "a"], then
  TYPE the complete new value (e.g. "robotics.txt", not just ".txt").
- Blocked hotkeys: {", ".join("+".join(sorted(k)) for k in BLOCKED_HOTKEYS)}.
- To open an app, pressing "win", typing its name and pressing "enter" is usually
  more reliable than hunting for an icon.
- To type several lines, put "\n" between them in one TYPE text.
- In a web browser, to search: click the address/search bar (or HOTKEY
  ["ctrl", "l"]), TYPE the search words, then PRESS "enter".
- If a dialog asks for confirmation (e.g. "Confirm Save As ... replace it?"),
  that is normal: read it and answer it (e.g. Yes) if it fits the task.
- The terminal window running this agent may be visible; ignore it and never
  type into it.
- Use FINISH only when the screenshot shows the task is done. FINISH is checked
  independently; if the check fails you must continue working.

Application identity:
- When the task names an application (e.g. Windows Notepad, Calculator, Chrome),
  check the title bar / app icon to confirm THAT application is the window in
  front before typing into it. Do not assume any text editor is Notepad
  (VS Code, WordPad, Word, a browser page or a terminal are NOT Notepad).
  If the wrong application is in front, open the right one again (win, type its
  name, enter).
- Windows 11 Notepad has tabs, may reopen tabs with old text from an earlier
  session, and may show a small formatting toolbar - it is still Notepad.
  If the visible Notepad tab already contains unrelated text, open a new empty
  tab with HOTKEY ["ctrl", "n"] before typing.

After a failed step:
- You will be told the failed action, what was expected, what was observed
  and the retry count. Look at the new screenshot and choose a DIFFERENT way
  (e.g. click a visible menu item or button instead of a shortcut, first click
  the correct window to focus it, or answer an open dialog). Repeating the
  exact same failed action is almost never right.
"""

VERIFY_SYSTEM_PROMPT = """\
You check whether a computer action had the intended effect.
You see a screenshot taken AFTER the action and a description of the expected outcome.
Reply with JSON only, writing the reason FIRST:
{"reason": "<what in the screenshot supports your answer; if the outcome did NOT
             happen, say what is visibly there instead, e.g. which window is in
             front or which dialog is open>",
 "success": true or false,
 "confidence": <number from 0 to 1>,
 "confirmation_needed": true or false}
Rules:
- Judge only from what is visible. success must agree with your reason.
- Answer true when the main intended effect is visible, even if unimportant
  details differ (window position or size, extra tabs, cursor position).
  Answer false when the key evidence is missing or contradicts the outcome.
- confirmation_needed = true only if a confirmation/permission dialog is now
  open (e.g. "Confirm Save As ... Do you want to replace it?") that must be
  answered before the expected outcome can happen. Otherwise false.
- If the task names an application, make sure the window you judge really is
  that application (check the title bar). Windows 11 Notepad has tabs and may
  show a small formatting toolbar - it is still Notepad. The terminal window
  running this agent may be visible; ignore it.
"""


GROUND_SYSTEM_PROMPT = f"""\
You locate ONE user-interface element on a screenshot of a Windows 11 computer.
You are given a short description of the target (e.g. "Calculator", "the Save
button", "the address bar"). You do not act; you only report where it is.

Reply with JSON only, writing the reason FIRST:
{{"reason": "<what you see that identifies the target, or why it is not there>",
 "found": true or false,
 "x_min": <int>, "y_min": <int>, "x_max": <int>, "y_max": <int>,
 "confidence": <number from 0 to 1>}}

Coordinates: integers on a 0-{COORD_SCALE} grid over the whole screenshot.
(0, 0) is the top-left corner, ({COORD_SCALE}, {COORD_SCALE}) is the bottom-right.
x is horizontal, y is vertical. The box must tightly enclose the clickable
element: x_min < x_max and y_min < y_max.

Rules:
- Judge only from what is visible. Never guess a location for something you cannot see.
- If the target is not visible, answer found = false and set all four coordinates to 0.
- If several elements match, choose the one that best fits the context and say
  so in the reason; lower the confidence if it is ambiguous.
- confidence is how sure you are that the box is on the right element.
- The terminal window running this agent may be visible; ignore it.
"""


def build_decide_text(task, history, note=""):
    lines = [f"Task: {task}"]
    if history:
        lines.append("Previous steps (coordinates on the 0-1000 grid):")
        lines += [f"  {i}. {h}" for i, h in enumerate(history, 1)]
    else:
        lines.append("Previous steps: none (this is the first step).")
    if note:
        lines.append(f"IMPORTANT - the last step did not go as planned:\n{note}")
        lines.append("Look at the screen again. Do not repeat an action that already failed; "
                     "try a different way if needed.")
    lines.append("Choose the next action.")
    return "\n".join(lines)


# --- Feedback about the previous step ------------------------------------------
# The loop classifies every unsuccessful step; each kind gets its own advice so
# the model knows whether to fix its JSON, try another way, or answer a dialog.

FAILURE_ADVICE = {
    "ACTION_REJECTED": "The action was REJECTED by the safety/format validator and was NOT "
                       "executed. Fix the action so it follows the rules.",
    "NO_DECISION": "Your reply could not be used. Reply with valid JSON containing exactly "
                   "one action.",
    "GROUNDING_FAILED": "The CLICK target could not be located safely, so NOTHING was "
                        "clicked. Check that the element is visible, then describe it more "
                        "precisely, give x and y instead, or reach it another way (e.g. "
                        "the keyboard).",
    "EXECUTION_ERROR": "The action could not be performed on the computer.",
    "OUTCOME_NOT_CONFIRMED": "The action was executed but the screen did not change as "
                             "expected. Check which window is in front / has focus, and try a "
                             "different way (e.g. a visible menu item or button instead of a "
                             "shortcut).",
    "CONFIRMATION_NEEDED": "This is NOT an error: a confirmation dialog is open. Read it and "
                           "answer it (e.g. Yes / OK / Replace) if that fits the task.",
    "FINISH_NOT_VERIFIED": "You chose FINISH but the task is NOT verified complete. Do the "
                           "missing part before choosing FINISH again.",
}


def build_failure_note(kind, action, expected, observed, retries, max_retries, repeats=1):
    """Structured feedback about the previous step, shown in the next decide request."""
    lines = [f"- What happened: {kind} - {FAILURE_ADVICE[kind]}",
             f"- Previous action: {action or '(none)'}"]
    if expected:
        lines.append(f"- Expected outcome: {expected}")
    lines.append(f"- Observed: {observed}")
    if kind == "CONFIRMATION_NEEDED":
        lines.append(f"- Retry count: {retries} of {max_retries} (not increased by this step)")
    else:
        lines.append(f"- Retry count: {retries} of {max_retries} consecutive failures allowed")
    if repeats > 1:
        lines.append(f"- This same action has now failed {repeats} times in a row. "
                     f"Do NOT propose it again; choose a different action.")
    return "\n".join(lines)


def build_verify_text(task, expected_outcome, recent_steps=()):
    lines = [f"Task: {task}"]
    if recent_steps:
        lines.append("Most recent step(s): " + " | ".join(recent_steps))
    lines.append(f"Expected outcome: {expected_outcome}")
    lines.append("Does the screenshot show that the expected outcome happened?")
    return "\n".join(lines)


def build_ground_text(target, context=""):
    lines = [f"Target: {target}"]
    if context:
        lines.append(f"Context: {context}")
    lines.append("Where is the target on the screenshot?")
    return "\n".join(lines)


# --- JSON schemas for providers that support structured output ----------------
# Kept deliberately loose: the real, strict validation is actions.parse_action().

_coord = {"type": "integer", "minimum": 0, "maximum": COORD_SCALE}

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "observation": {"type": "string"},
        "action": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": sorted(ACTION_TYPES)},
                "x": _coord, "y": _coord, "to_x": _coord, "to_y": _coord,
                "target": {"type": "string"},   # grounded CLICK (instead of x, y)
                "text": {"type": "string"},
                "key": {"type": "string"},
                "keys": {"type": "array", "items": {"type": "string"}},
                "amount": {"type": "integer"},
                "seconds": {"type": "number"},
                "reason": {"type": "string"},
                "expected_outcome": {"type": "string"},
            },
            "required": ["action", "reason", "expected_outcome"],
        },
    },
    "required": ["observation", "action"],
}

# "reason" comes first so the model explains before it commits to true/false.
VERIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "success": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "confirmation_needed": {"type": "boolean"},
    },
    "required": ["reason", "success", "confidence"],
}

# All four coordinates are required so a structured reply can never carry a
# partial box (found=false sends zeros). vlm.parse_grounding() still validates.
GROUNDING_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "found": {"type": "boolean"},
        "x_min": _coord, "y_min": _coord, "x_max": _coord, "y_max": _coord,
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["reason", "found", "x_min", "y_min", "x_max", "y_max", "confidence"],
}
