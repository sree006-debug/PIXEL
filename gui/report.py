"""Task completion report: plain text built only from one run's recorded data.

The data dict comes from gui.agent_worker.run_report(). Nothing here guesses:
a field the run did not record is shown as "Not available", and a task counts
as Verified only when its final completion check (FINISH) passed.
"""

from __future__ import annotations

from datetime import datetime

from agent import verifier

OUTCOMES = {"success": "Completed", "failed": "Failed", "aborted": "Stopped"}
UNAVAILABLE = "Not available"


def outcome(report: dict) -> str:
    return OUTCOMES.get(report.get("status"), "Failed")


def verification_status(report: dict) -> str:
    """Verified / Failed / Not verified, from the final completion check only."""
    final = report.get("finish_verdict") or ""
    if report.get("status") == "success" and final == verifier.PASS:
        return "Verified"
    if final:                       # a completion check ran and did not pass
        return "Failed"
    return "Not verified"


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return UNAVAILABLE
    seconds = int(round(seconds))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes} min {secs:02d} s" if minutes else f"{secs} s"


def _time(stamp: float | None) -> str:
    if stamp is None:
        return UNAVAILABLE
    return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M:%S")


def _count(value) -> str:
    return UNAVAILABLE if value is None else str(value)


def verification_summary(report: dict) -> str:
    checks = report.get("verifications") or []
    if not checks:
        return "None recorded"
    passed = sum(1 for verdict, _ in checks if verdict == verifier.PASS)
    return f"{passed} passed, {len(checks) - passed} not passed ({len(checks)} total)"


def report_text(report: dict) -> str:
    started, ended = report.get("started"), report.get("ended")
    duration = ended - started if started is not None and ended is not None else None
    result = outcome(report)
    reason_label = {"Completed": "Result", "Stopped": "Cancellation"}.get(result,
                                                                         "Failure reason")
    lines = [
        "PIXEL Task Report",
        "",
        f"Instruction: {report.get('task', '')}",
        f"Outcome: {result}",
        f"Verification: {verification_status(report)}",
        f"Started: {_time(started)}",
        f"Ended: {_time(ended)}",
        f"Duration: {format_duration(duration)}",
        f"Steps: {_count(report.get('steps'))}",
        f"Actions performed: {_count(report.get('actions'))}",
        f"Retries: {_count(report.get('retries'))}",
        f"Verification checks: {verification_summary(report)}",
        f"{reason_label}: {report.get('reason') or UNAVAILABLE}",
        "",
        "Step log:",
    ]
    history = report.get("history") or []
    if history:
        lines += [f"  {i}. {line}" for i, line in enumerate(history, 1)]
    else:
        lines.append("  No steps were recorded.")
    return "\n".join(lines)
