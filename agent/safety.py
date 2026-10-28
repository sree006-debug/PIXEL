"""Execution-time safety policy: the last check right before an action is performed.

parse_action() validates ONE proposed action. This module adds two checks that
run inside Controller.execute(), immediately before any input happens, for every
caller (agent loop, GUI, tools):

* policy_problem(action): the hotkey and TYPE blocklists of agent.actions again,
  so an Action that was built or changed outside parse_action() is still refused.
* TypingGuard: a blocked word split over several TYPE actions ("power", "shell")
  is refused when the TYPE that would complete it arrives. The guard remembers
  the text typed since the last Enter (or newline): that is what a Start-menu
  search or a command box would run. A CLICK does not clear it, because clicking
  the same text box and continuing to type is a way to split a word.

Blocked actions raise PolicyViolation (an ActionError) and nothing is performed.
"""

from agent.actions import Action, ActionError, blocked_hotkey, blocked_text_reason

# Remember at most this many recently typed characters (about two full TYPE actions).
TYPED_MEMORY = 400


class PolicyViolation(ActionError):
    """The action breaks the execution-time safety policy; nothing was performed."""


def policy_problem(action: Action) -> str | None:
    """Why this single action may not be performed, or None."""
    if action.action == "HOTKEY" and action.keys and blocked_hotkey(action.keys):
        return f"HOTKEY {'+'.join(action.keys)} is blocked for safety"
    if action.action == "TYPE" and action.text is not None:
        rule = blocked_text_reason(action.text)
        if rule:
            return f"TYPE text contains a blocked command word ({rule})"
    return None


class TypingGuard:
    """Tracks recently typed text so a blocked word cannot be split across TYPE actions."""

    def __init__(self) -> None:
        self.buffer = ""

    def reset(self) -> None:
        self.buffer = ""

    def problem(self, action: Action) -> str | None:
        """Why performing this action would complete a blocked word, or None. No side effects."""
        if action.action != "TYPE" or not action.text:
            return None
        combined = self.buffer + action.text
        if blocked_text_reason(action.text):
            return None   # reported by policy_problem() with the clearer single-action message
        rule = blocked_text_reason(combined)
        if rule:
            return (f"TYPE text would complete a blocked command word together with the text "
                    f"typed before it ({rule}); split commands are not allowed")
        return None

    def record(self, action: Action) -> None:
        """Update the memory after an action was performed (or dry-run logged)."""
        if action.action == "TYPE" and action.text:
            text = self.buffer + action.text
            if "\n" in text:                      # a typed newline submits like Enter
                text = text.rsplit("\n", 1)[1]
            self.buffer = text[-TYPED_MEMORY:]
        elif action.action == "PRESS" and action.key == "enter":
            self.buffer = ""
        elif action.action == "HOTKEY" and action.keys and "enter" in action.keys:
            self.buffer = ""
