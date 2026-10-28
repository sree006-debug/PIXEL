"""Background agent execution for the Run Task button.

AgentWorker lives on its own QThread for the whole session. The window emits a
task string; the worker builds the SAME stack main.py uses
(create_provider -> Controller -> RunLog -> Agent) and calls Agent.run(), so
every action still goes VLM -> parse_action() -> Controller -> pyautogui with
the same validation, retry limits, verification and failsafe. Progress comes
from the Agent's on_event hook and is forwarded as Qt signals, which Qt
delivers on the GUI thread. The worker never touches widgets.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Signal, Slot


def short_reason(reason: str) -> str:
    """One-line, human-friendly version of TaskState.reason for the status card."""
    if reason.startswith("MAX_RETRIES"):
        return "Maximum retries reached."
    if reason.startswith("MAX_STEPS"):
        return "Maximum steps reached."
    if "failsafe" in reason.lower():
        return "Emergency stop (mouse moved to a screen corner)."
    if reason == "stopped by user":
        return "Stopped by user."
    return reason if len(reason) <= 120 else reason[:117] + "..."


def run_report(task: str, state, reason: str, started: float, ended: float) -> dict:
    """Plain data for one run's completion report, taken only from the TaskState.

    state is None when the agent could not even be built (e.g. no API key): then
    there are no steps, actions or checks to report, and the fields say so (None).
    """
    data = {"task": task, "reason": reason, "started": started, "ended": ended,
            "status": "failed", "steps": None, "actions": None, "retries": None,
            "verifications": [], "finish_verdict": "", "history": []}
    if state is not None:
        data.update(status=state.status, steps=state.step, actions=state.actions,
                    retries=state.failures, verifications=list(state.verifications),
                    finish_verdict=state.finish_verdict, history=list(state.history))
    return data


class AgentWorker(QObject):
    """task -> Agent.run() -> succeeded/failed. One task at a time."""

    event = Signal(str, str)     # (kind, message), kind is one of agent.loop.EVENTS
    succeeded = Signal(str)      # TaskState.reason
    failed = Signal(str)         # TaskState.reason or an error description
    report = Signal(object)      # run data for the completion report (see gui.report)
    finished = Signal()          # always emitted last, success or failure

    def __init__(self) -> None:
        super().__init__()
        self._stop = threading.Event()
        self._provider: str | None = None    # None = VLM_PROVIDER from .env

    def set_provider(self, name: str | None) -> None:
        """Provider for the next task (from the GUI selector). Set BEFORE queueing it."""
        self._provider = name or None

    def prepare_task(self) -> None:
        """Clear a previous stop request. The window calls this BEFORE queueing a task,
        so a Stop pressed before the worker thread starts the task is not lost."""
        self._stop.clear()

    def request_stop(self) -> None:
        """Thread-safe: ask the running task to stop. No new action is sent after this;
        an in-flight model request or input action finishes first (see agent.loop)."""
        self._stop.set()

    @Slot(str)
    def run_task(self, task: str) -> None:
        started = time.time()
        state = None
        try:
            agent = self._build_agent(task)
            state = agent.run(task)
        except Exception as exc:  # e.g. VLM not configured; never crash the GUI
            reason = f"{type(exc).__name__}: {exc}"
            self.failed.emit(reason)
        else:
            reason = state.reason
            if state.status == "success":
                self.succeeded.emit(state.reason)
            else:
                self.failed.emit(state.reason)
        finally:
            self.report.emit(run_report(task, state, reason, started, time.time()))
            self.finished.emit()

    def _build_agent(self, task: str):
        # Imported here, not at module level: agent.controller sets DPI awareness and
        # imports pyautogui, which must happen after Qt has set up the process.
        import config
        from agent import verifier
        from agent.controller import Controller
        from agent.demos import DEMOS
        from agent.loop import Agent
        from agent.run_log import RunLog
        from agent.vlm import create_provider

        provider = create_provider(self._provider)   # VLMConfigError -> failed signal

        # A task typed exactly like a demo gets that demo's final checks, as with --demo.
        demo = next((d for d in DEMOS.values()
                     if d.task.casefold() == task.strip().casefold()), None)
        final_check = None
        if demo and demo.expect_file:
            final_check = verifier.file_contains(demo.expect_file, demo.expect_text,
                                                 min_lines=demo.expect_lines,
                                                 modified_after=time.time())

        config.SCRATCH_DIR.mkdir(exist_ok=True)
        # _stop.wait(seconds) returns early when Stop is pressed: the pause after an
        # action, the retry backoff and a WAIT action are interrupted by Stop.
        return Agent(provider, Controller(dry_run=config.DRY_RUN, wait=self._stop.wait),
                     RunLog(config.RUNS_DIR, task), final_check=final_check,
                     finish_criteria=demo.expect_screen if demo else "",
                     on_event=self.event.emit, should_stop=self._stop.is_set,
                     wait=self._stop.wait)
