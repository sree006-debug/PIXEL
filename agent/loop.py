"""The closed-loop agent.

    observe -> decide (VLM) -> validate -> act (Controller) -> wait
            -> observe -> verify (VLM) -> decide ... -> verified FINISH or controlled failure

Model output only ever reaches the screen through
decision_to_action() -> parse_action() -> Controller.execute().

A CLICK may name a `target` instead of x, y ("the 7 button on the Calculator
keypad"). Only then is visual grounding used: grounding.locate() finds the
element on the same screenshot and grounding.propose_click() turns it into a
validated CLICK Action (or refuses it). That Action then takes the normal
execute -> observe -> verify path. Every other action is unaffected.

Every step that does not go as planned is classified (see prompts.FAILURE_ADVICE)
and described to the VLM on the next step, so it can choose a different action:
    ACTION_REJECTED        validator refused the action (nothing executed)
    GROUNDING_FAILED       a CLICK target could not be located safely, or was located
                           where a grounded CLICK already failed this streak (nothing executed)
    NO_DECISION            VLM error / timeout / unusable JSON
    EXECUTION_ERROR        the controller raised while performing the action
    OUTCOME_NOT_CONFIRMED  executed, but the expected outcome is not visible
    CONFIRMATION_NEEDED    a confirmation dialog appeared (not counted as a failure)
    FINISH_NOT_VERIFIED    FINISH claimed, but visual or deterministic check failed

Optional progress hook for front-ends (e.g. the GUI): on_event(kind, message) is
called with one of EVENTS. It only reports; it cannot change what the agent does.

Cooperative cancellation: should_stop() is checked before each step, after the
model's decision (nothing is validated or executed), right before execution, and
right after an action was performed (verification is skipped). True ends the task
as "aborted" with reason "stopped by user"; the step log records where
("cancelled"). An optional wait(seconds) -> bool (e.g. threading.Event.wait) makes
the pause after an action and the retry backoff end early when Stop is requested.
A model request or an input action that is already running is not interrupted:
the stop takes effect as soon as it returns, and no further action is sent.
"""

import logging
import time
from dataclasses import dataclass, field

import config
from agent import grounding, prompts, screen, verifier
from agent.actions import ActionError
from agent.controller import EmergencyStop, describe
from agent.safety import PolicyViolation
from agent.vlm import decision_to_action
from agent.vlm_types import VLMRequest

CONFIRMATION = "CONFIRMATION_NEEDED"
EVENTS = ("TASK_STARTED", "OBSERVING", "DECIDING", "VALIDATING", "ACTION", "VERIFYING",
          "VERIFICATION_PASS", "VERIFICATION_FAIL", "RETRYING", "CONFIRMATION_NEEDED",
          "TASK_COMPLETED", "TASK_FAILED", "TASK_ABORTED")

log = logging.getLogger("agent.loop")
SAME_ACTION_TOLERANCE = 25   # 0-1000 grid units: clicks this close count as "the same"
GROUNDED_CLICK_FIELDS = {"action", "target", "reason", "expected_outcome"}


@dataclass
class TaskState:
    """Small structured memory for one task. No long-term memory on purpose."""
    task: str
    step: int = 0
    retries: int = 0              # consecutive failed steps
    previous_action: str = ""
    previous_result: str = ""
    last_failure: str = ""        # sent to the VLM on the next step
    status: str = "running"       # running | success | failed | aborted
    reason: str = ""              # why the task ended
    history: list = field(default_factory=list)   # one line per step, for the VLM
    failed_actions: list = field(default_factory=list)  # raw actions of the current failure streak
    failed_groundings: list = field(default_factory=list)  # GroundedClicks that ran, not confirmed (streak)
    file_check_passed: bool = False  # a step was accepted via the deterministic file check
    # Run totals for reports (the GUI's completion report); they never steer the agent.
    actions: int = 0              # actions the controller actually performed
    failures: int = 0             # failed steps that used up a retry, over the whole run
    verifications: list = field(default_factory=list)  # (verdict, reason) of every check made
    finish_verdict: str = ""      # verdict of the last FINISH (task completion) check


def describe_raw(action: dict | None) -> str:
    """Short text for a model action in ITS coordinate space (0-1000 grid)."""
    if not action:
        return "(no action)"
    parts = [str(action.get("action", "?"))]
    for key in ("x", "y", "to_x", "to_y", "target", "text", "key", "keys", "amount", "seconds"):
        if action.get(key) is not None:
            parts.append(f"{key}={action[key]!r}")
    return " ".join(parts)


def same_action(a: dict | None, b: dict | None, tolerance=SAME_ACTION_TOLERANCE) -> bool:
    """True if two raw model actions do the same thing (points may differ slightly)."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    if str(a.get("action", "")).upper() != str(b.get("action", "")).upper():
        return False
    for key in ("x", "y", "to_x", "to_y"):
        va, vb = a.get(key), b.get(key)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            if abs(va - vb) > tolerance:
                return False
        elif va != vb:
            return False
    return all(a.get(k) == b.get(k) for k in ("target", "text", "key", "keys", "amount", "seconds"))


def near_point(a, b, tolerance=SAME_ACTION_TOLERANCE) -> bool:
    """True if two 0-1000 grid points are within `tolerance` on each axis (as in same_action)."""
    return abs(a[0] - b[0]) <= tolerance and abs(a[1] - b[1]) <= tolerance


def is_grounded_click(action: dict | None) -> bool:
    """True if the model asked for a CLICK on a described target instead of x, y."""
    return (isinstance(action, dict) and action.get("target") is not None
            and str(action.get("action", "")).strip().upper() == "CLICK")


class Agent:
    def __init__(self, provider, controller, run_log, observe=screen.take_screenshot,
                 screen_size=None, final_check=None, max_steps=config.MAX_STEPS,
                 max_retries=config.MAX_RETRIES, verify_steps=config.VERIFY_EACH_STEP,
                 sleep=time.sleep, finish_criteria="", on_event=None, should_stop=None,
                 wait=None):
        self.provider = provider
        self.controller = controller
        self.log = run_log
        self.observe = observe
        self.screen_size = screen_size or screen.get_screen_size()
        self.final_check = final_check      # optional deterministic check for FINISH
        self.finish_criteria = finish_criteria  # optional: what the final screen must show
        self.max_steps = max_steps
        self.max_retries = max_retries
        self.verify_steps = verify_steps
        self.sleep = sleep
        self.on_event = on_event            # optional progress callback(kind, message)
        self.should_stop = should_stop      # optional: () -> True to stop (cooperative)
        self.wait = wait                    # optional: (seconds) -> bool, ends early on stop

    # ------------------------------------------------------------------ run
    def run(self, task: str) -> TaskState:
        state = TaskState(task=task)
        begin_task = getattr(self.controller, "begin_task", None)
        if callable(begin_task):
            begin_task()                    # forget text typed during an earlier task
        self._emit("TASK_STARTED", task)
        self._emit("OBSERVING", "Observing screen")
        shot = self.observe()
        self.log.save_screenshot(0, shot, "start")

        while state.status == "running":
            if self._stopped():
                self._end(state, "aborted", "stopped by user")
                break
            if state.step >= self.max_steps:
                self._end(state, "failed", f"MAX_STEPS ({self.max_steps}) reached without verified completion")
                break
            state.step += 1
            entry = {"step": state.step, "retries": state.retries,
                     "max_retries": self.max_retries, "model": self.provider.model}
            try:
                shot = self._step(state, shot, entry)
            finally:
                entry["retries_after"] = state.retries
                self.log.step(entry)

        self.log.summary({"task": task, "status": state.status, "reason": state.reason,
                          "steps": state.step, "model": self.provider.model,
                          "dry_run": self.controller.dry_run, "history": state.history})
        self._emit({"success": "TASK_COMPLETED", "aborted": "TASK_ABORTED"}.get(
            state.status, "TASK_FAILED"), state.reason)
        return state

    # ----------------------------------------------------------------- step
    def _step(self, state, shot, entry):
        """One decide -> validate -> act -> verify cycle. Returns the latest screenshot."""
        # 1. DECIDE
        self._emit("DECIDING", f"Step {state.step}: deciding next action")
        request = VLMRequest(task=state.task, screenshot=shot,
                             history=state.history[-config.HISTORY_LENGTH:],
                             note=state.last_failure)
        t0 = time.perf_counter()
        decision = self.provider.decide_action(request)
        entry["vlm_latency_s"] = round(time.perf_counter() - t0, 1)
        entry["observation"] = decision.observation
        entry["proposed_action"] = decision.action

        if self._stopped():                       # Stop while the model was answering
            entry["validation"] = entry["execution"] = "not reached (stop requested)"
            return self._abort(state, entry, "after the model decision", shot)

        if not decision.ok:                       # API error, timeout, bad JSON ...
            entry["validation"] = entry["execution"] = "not reached"
            self._fail(state, entry, "NO_DECISION", None,
                       f"the VLM gave no usable decision: {decision.error}")
            return self._reobserve(state)

        raw = decision.action
        entry["action_text"] = describe_raw(raw)
        entry["expected_outcome"] = raw.get("expected_outcome", "")

        # 2. VALIDATE (the only path from model output to an Action)
        self._emit("VALIDATING", describe_raw(raw))
        located = ""                              # where a grounded CLICK target was found
        proposal = None                           # the GroundedClick, for grounded CLICKs only
        if is_grounded_click(raw):
            proposal = self._ground_click(state, shot, raw, entry)
            if proposal is None:                  # refused: nothing is executed
                return self._reobserve(state)
            action = proposal.action
            located = f" (located at x={proposal.point[0]} y={proposal.point[1]})"
        else:
            try:
                action = decision_to_action(decision, self.screen_size)
            except ActionError as e:
                entry["validation"] = f"REJECTED: {e}"
                entry["execution"] = "not executed"
                self._fail(state, entry, "ACTION_REJECTED", raw,
                           f"your proposed action ({describe_raw(raw)}) was rejected: {e}")
                return self._reobserve(state)
            entry["validation"] = "ok"

        if self._stopped():                       # Stop during validation / grounding
            entry["execution"] = "not executed (stop requested)"
            return self._abort(state, entry, "before execution", shot)

        # 3a. FINISH is a claim that must be verified, not an action
        if action.action == "FINISH":
            entry["execution"] = "none (FINISH is only a claim)"
            self._emit("VERIFYING", "Verifying task completion")
            verdict, reason = self._verify_finish(state, shot, entry)
            state.verifications.append((verdict, reason))
            state.finish_verdict = verdict
            entry["verification"] = f"{'PASS' if verdict == verifier.PASS else 'NOT VERIFIED'}: {reason}"
            if verdict != verifier.CONFIRM:
                self._emit("VERIFICATION_PASS" if verdict == verifier.PASS
                           else "VERIFICATION_FAIL", reason)
            if verdict == verifier.PASS:
                state.history.append(f"{describe_raw(raw)} -> VERIFIED: {reason}")
                self._end(state, "success", f"task verified complete: {reason}")
            else:
                kind = CONFIRMATION if verdict == verifier.CONFIRM else "FINISH_NOT_VERIFIED"
                self._fail(state, entry, kind, raw, reason)
            return shot

        # 3b. ACT
        self._emit("ACTION", describe(action))
        try:
            result = self.controller.execute(action)
        except EmergencyStop as e:
            entry["execution"] = f"ABORTED: {e}"
            self._end(state, "aborted", str(e))
            return shot
        except PolicyViolation as e:              # execution-time safety check: nothing performed
            entry["validation"] = f"REJECTED at execution: {e}"
            entry["execution"] = "not executed (blocked by the safety policy)"
            self._fail(state, entry, "ACTION_REJECTED", raw,
                       f"your proposed action ({describe_raw(raw)}) was blocked: {e}")
            return self._reobserve(state)
        except Exception as e:                    # e.g. a Windows input error
            entry["execution"] = f"ERROR: {e}"
            self._fail(state, entry, "EXECUTION_ERROR", raw,
                       f"executing {describe_raw(raw)} raised an error: {e}")
            return self._reobserve(state)
        entry["execution"] = result
        state.actions += 1
        state.previous_action, state.previous_result = describe_raw(raw), result

        if self._stopped():                       # the action WAS performed; don't verify it
            entry["verification"] = "skipped (stop requested after the action was performed)"
            return self._abort(state, entry, "after execution", shot)

        # 4. OBSERVE the result
        if action.action != "WAIT":               # WAIT already waited
            self._pause(config.ACTION_DELAY)
        if self._stopped():
            entry["verification"] = "skipped (stop requested after the action was performed)"
            return self._abort(state, entry, "after execution", shot)
        self._emit("OBSERVING", "Observing screen")
        shot = self.observe()
        entry["screenshot"] = self.log.save_screenshot(state.step, shot)

        # 5. VERIFY the step
        text = describe_raw(raw) + located
        if self.controller.dry_run:
            entry["verification"] = "skipped (dry run: nothing was executed)"
            self._succeed(state, f"{text} -> [DRY RUN, not executed]")
        elif not self.verify_steps or not action.expected_outcome:
            entry["verification"] = "skipped (no expected outcome / disabled)"
            self._succeed(state, f"{text} -> done (not verified)")
        else:
            self._emit("VERIFYING", action.expected_outcome)
            t0 = time.perf_counter()
            verdict, reason = verifier.vlm_check(
                self.provider, state.task, action.expected_outcome,
                shot, [f"{text} (expected: {action.expected_outcome})"])
            entry["verify_latency_s"] = round(time.perf_counter() - t0, 1)
            entry["verification"] = f"{verdict}: {reason}"
            if verdict in (verifier.FAIL, verifier.UNKNOWN):
                verdict, reason = self._file_check_override(state, entry, verdict, reason)
            state.verifications.append((verdict, reason))
            if verdict != verifier.CONFIRM:
                self._emit("VERIFICATION_PASS" if verdict == verifier.PASS
                           else "VERIFICATION_FAIL", reason)
            if verdict == verifier.PASS:
                self._succeed(state, f"{text} -> OK: {reason}")
            elif verdict == verifier.CONFIRM:
                self._fail(state, entry, CONFIRMATION, raw, reason)
            elif proposal is not None:            # say where the grounded CLICK went
                state.failed_groundings.append(proposal)   # don't click here again this streak
                self._fail(state, entry, "OUTCOME_NOT_CONFIRMED", raw,
                           f"The CLICK on {proposal.target!r} landed at x={proposal.point[0]} "
                           f"y={proposal.point[1]} with grounding box {list(proposal.bbox)}, "
                           f"but the expected outcome was not confirmed: {reason}")
            else:
                self._fail(state, entry, "OUTCOME_NOT_CONFIRMED", raw,
                           f"the expected outcome was not confirmed: {reason}")
        return shot

    # -------------------------------------------------------------- helpers
    def _ground_click(self, state, shot, raw, entry):
        """CLICK on a described target: locate it on `shot` and propose a validated click.

        Returns the GroundedClick (its .action is a validated CLICK Action), or
        None after recording the failure; then nothing is executed. All grounding
        safety checks (confidence, box validity and size, parse_action) live in
        grounding.propose_click(); this only routes its answer. One grounding
        call per step: retrying is left to the normal retry/replan loop.
        """
        extra = {k for k, v in raw.items() if v is not None} - GROUNDED_CLICK_FIELDS
        if extra:
            problem = (f"CLICK with target does not accept field(s): {', '.join(sorted(extra))}; "
                       f"give either target or x, y, never both")
            entry["validation"] = f"REJECTED: {problem}"
            entry["execution"] = "not executed"
            self._fail(state, entry, "ACTION_REJECTED", raw,
                       f"your proposed action ({describe_raw(raw)}) was rejected: {problem}")
            return None

        expected = raw.get("expected_outcome")
        if expected is None or (isinstance(expected, str) and not expected.strip()):
            problem = ("CLICK with target needs a non-empty expected_outcome "
                       "(what the screen should show after the click)")
            entry["validation"] = f"REJECTED: {problem}"
            entry["execution"] = "not executed"
            self._fail(state, entry, "ACTION_REJECTED", raw,
                       f"your proposed action ({describe_raw(raw)}) was rejected: {problem}")
            return None

        t0 = time.perf_counter()
        result = grounding.locate(self.provider, shot, raw["target"], context=state.task)
        entry["grounding_latency_s"] = round(time.perf_counter() - t0, 1)
        proposal = grounding.propose_click(result, self.screen_size,
                                           reason=raw.get("reason") or "",
                                           expected_outcome=raw.get("expected_outcome") or "")
        entry["grounding"] = {**proposal.to_dict(), "model_reason": result.reason}
        if not proposal.ok:
            entry["validation"] = f"REJECTED (grounding {proposal.status}): {proposal.error}"
            entry["execution"] = "not executed"
            self._fail(state, entry, "GROUNDING_FAILED", raw,
                       f"could not locate {proposal.target!r} for a CLICK "
                       f"({proposal.status}): {proposal.error}. Nothing was clicked.")
            return None
        entry["grounding"]["bbox_px"] = list(grounding.bbox_to_pixels(proposal.bbox,
                                                                       self.screen_size))
        tried = next((old for old in state.failed_groundings
                      if near_point(proposal.point, old.point)), None)
        if tried is not None:                     # this spot already ran and was not confirmed
            entry["grounding"]["status"] = "repeated_location"
            entry["validation"] = "REJECTED (grounding: repeated_location)"
            entry["execution"] = "not executed"
            self._fail(state, entry, "GROUNDING_FAILED", raw,
                       f"The grounded location for {proposal.target!r} at x={proposal.point[0]} "
                       f"y={proposal.point[1]} is within {SAME_ACTION_TOLERANCE} grid units of "
                       f"x={tried.point[0]} y={tried.point[1]}, where a previous CLICK already "
                       f"ran and its expected outcome was not confirmed. Nothing was clicked. "
                       f"Do not click that same location again; choose a different approach.")
            return None
        entry["validation"] = (f"ok (grounded {proposal.target!r}: confidence "
                               f"{proposal.confidence:.2f})")
        return proposal

    def _verify_finish(self, state, shot, entry):
        """FINISH counts only if the VLM confirms AND the optional deterministic check passes.

        The deterministic check (e.g. file exists with the right content) is
        authoritative for files: it can veto the VLM, and it runs even when the
        VLM already said no, so the next decision knows the file's real state.
        """
        expected = f"The whole task is complete: {state.task}"
        if self.finish_criteria:
            expected += f" In particular, the screen must show: {self.finish_criteria}"
        t0 = time.perf_counter()
        verdict, reason = verifier.vlm_check(self.provider, state.task, expected,
                                             shot, state.history[-2:])
        entry["verify_latency_s"] = round(time.perf_counter() - t0, 1)
        if self.final_check is None:
            return verdict, reason

        ok, check_reason = self.final_check()
        entry["deterministic_check"] = f"{'PASS' if ok else 'FAIL'}: {check_reason}"
        if verdict != verifier.PASS:
            return verdict, f"{reason}; deterministic check: {check_reason}"
        if not ok:
            return verifier.FAIL, f"VLM agreed, but deterministic check failed: {check_reason}"
        return verifier.PASS, f"{reason}; {check_reason}"

    def _file_check_override(self, state, entry, verdict, reason):
        """A step the VLM did not confirm still counts if the deterministic file check passes.

        E.g. PRESS enter in Save As: the dialog closes because the file WAS saved,
        but the VLM may say "not saved". Only tasks with a final_check (--expect-file)
        get this; the check itself rejects files not saved during this run. It can
        rescue one step per run, so it can't hide later, unrelated failures.
        """
        if self.final_check is None or state.file_check_passed:
            return verdict, reason
        ok, check_reason = self.final_check()
        entry["deterministic_check"] = f"{'PASS' if ok else 'FAIL'}: {check_reason}"
        if not ok:
            return verdict, reason
        state.file_check_passed = True
        entry["verification"] = f"PASS (deterministic file check overrides VLM {verdict}): " \
                                f"{check_reason}; VLM said: {reason}"
        return verifier.PASS, f"{check_reason} (deterministic check; the VLM said: {reason})"

    def _succeed(self, state, history_line):
        state.retries = 0
        state.last_failure = ""
        state.failed_actions.clear()
        state.failed_groundings.clear()
        state.history.append(history_line)

    def _fail(self, state, entry, kind, raw, observed):
        """Record an unsuccessful step and prepare feedback for the next decision.

        CONFIRMATION_NEEDED is progress, not a failure: it does not use up a retry.
        """
        text = describe_raw(raw) if raw else "(no valid decision)"
        repeats = 1 + sum(same_action(raw, old) for old in state.failed_actions) if raw else 1
        if kind != CONFIRMATION:
            state.retries += 1
            state.failures += 1
            state.failed_actions.append(raw)
        expected = raw.get("expected_outcome", "") if raw else ""
        state.last_failure = prompts.build_failure_note(
            kind, text, expected, observed, state.retries, self.max_retries,
            repeats if kind != CONFIRMATION else 1)
        state.previous_result = observed
        label = "CONFIRMATION NEEDED" if kind == CONFIRMATION else f"FAILED ({kind})"
        state.history.append(f"{text} -> {label}: {observed[:200]}")
        entry["failure_type"] = kind
        entry["failure"] = observed
        if kind != CONFIRMATION and repeats > 1:
            entry["repeated_failures"] = repeats
        if state.retries > self.max_retries:
            self._end(state, "failed",
                      f"MAX_RETRIES ({self.max_retries}) exceeded; last failure ({kind}): {observed}")
        elif kind == CONFIRMATION:
            self._emit(CONFIRMATION, observed)
        else:
            self._emit("RETRYING", f"{kind} (retry {state.retries}/{self.max_retries})")

    def _emit(self, kind, message):
        """Report progress to the optional callback. A broken callback never stops the agent."""
        if self.on_event is None:
            return
        try:
            self.on_event(kind, str(message))
        except Exception:
            log.exception("on_event callback failed for %s", kind)

    def _reobserve(self, state):
        """After a failure: short backoff (don't spam the API), then a fresh screenshot."""
        if state.status == "running" and not self._stopped():
            self._pause(min(config.VLM_RETRY_BACKOFF * state.retries, 10))
        self._emit("OBSERVING", "Observing screen")
        return self.observe()

    def _stopped(self) -> bool:
        """True once the front-end asked to stop (cooperative cancellation)."""
        return self.should_stop is not None and bool(self.should_stop())

    def _pause(self, seconds):
        """Sleep, but end early when a stop is requested and `wait` was given."""
        if self.wait is not None:
            self.wait(seconds)
        else:
            self.sleep(seconds)

    def _abort(self, state, entry, phase, shot):
        """End the task because Stop was requested; record where it happened."""
        entry["cancelled"] = f"stop requested ({phase})"
        self._end(state, "aborted", "stopped by user")
        return shot

    @staticmethod
    def _end(state, status, reason):
        state.status, state.reason = status, reason
