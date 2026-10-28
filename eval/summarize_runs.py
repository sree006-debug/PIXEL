"""Read-only: turn trial records + agent run folders into the AUTOMATIC data files.

    .venv\\Scripts\\python.exe eval\\summarize_runs.py trials  --out eval\\results\\auto_trials.csv
    .venv\\Scripts\\python.exe eval\\summarize_runs.py steps   --out eval\\results\\auto_steps.csv
    .venv\\Scripts\\python.exe eval\\summarize_runs.py calls   --out eval\\results\\auto_calls.csv
    .venv\\Scripts\\python.exe eval\\summarize_runs.py latency --kind main
    .venv\\Scripts\\python.exe eval\\summarize_runs.py check

Input: eval/results/meta/<trial_id>.json (written by eval/run_trial.py) and the
run folder each one names. --run-dir DIR adds a folder without a record
(trial_id stays empty; for ad-hoc inspection only, never for results).

Three things are kept apart (protocol 6.6): HOW the process ended (`termination_cause`),
whether the HARNESS evidence is intact (`integrity_status`: hashes, one run folder,
configuration, expected-file precondition) and whether the raw logs are COMPLETE
(`data_status`). The task outcome is a human label and is never derived here. An
incomplete log does not by itself make a trial invalid.

Incomplete data is never hidden: a torn or malformed line in log.jsonl (e.g. the
agent was killed mid-write) or an unreadable summary.json is skipped, NOT counted,
and reported as a WARNING on stderr. The row gets data_status=incomplete (steps and
calls rows: log_status=incomplete) and trials/steps/calls/latency exit with status 2
(output is still written). `check` exits 0 (all fine), 1 (INTEGRITY problem: that is
what the protocol treats as HARNESS_OR_CONFIG) or 2 (integrity fine, but some logs
are incomplete: the trials are kept and flagged, never excluded or repeated
automatically). It never prints a bare OK while anything is incomplete. No log at
all is not "incomplete". Records of kind `selftest` are left out unless
--kind selftest is given.

The run folder of a trial is only ever the one its record names. data_status says why a row has
none: no_run_folder (the record lists zero), ambiguous_run_folders (it lists several: none is
picked, run_dirs shows all of them), run_folder_missing (named but absent from disk),
run_folder_unresolved (the record is inconsistent), not_started (nothing was launched).

`labels` validates the human sheet (outcome labels, INVALID reasons, repeat relationships) against
the trial records and the automatic evidence; it reports, never rewrites. A malformed trial record
file is reported (check: integrity problem, exit 1; the CSV commands: skipped with a warning, exit 2).

Only reads; never writes into runs/ or into the human-entered sheets. These
files are regenerated, never edited by hand. Anything the logs do not contain
is left EMPTY (never estimated). Every count is the AGENT's view; ground truth
is in the human sheets (eval/results/trials.csv, steps.csv).

Definitions that differ from a naive count:
  executed_actions   input was really sent: excludes dry-run lines, ERROR
                     (outcome unknown), ABORTED and not-executed steps
  recovery           a failure streak ends at the next non-confirmation step
                     without a failure. It counts as recovered_verified only if
                     that step's check was PASS; a "done (not verified)" step is
                     recovered_unverified, a dry-run step recovered_dry_run, an
                     aborted step or the end of the run unrecovered
  latency            one row per model call (auto_calls.csv); percentiles are
                     pooled over calls, never over per-run medians or totals
"""

import argparse
import csv
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_trial import manifest  # noqa: E402  (same folder; hashes must match exactly)
import labels  # noqa: E402  (same folder: label vocabulary, repeat rules, sheet validation)

PROJECT_DIR = Path(__file__).resolve().parent.parent
META_DIR = PROJECT_DIR / "eval" / "results" / "meta"
RUNS_DIR = PROJECT_DIR / "runs"
HUMAN_CSV = PROJECT_DIR / "eval" / "results" / "trials.csv"

TRIAL_COLUMNS = [
    # from the trial record (run_trial.py)
    "trial_id", "cell_id", "kind", "repeat_of", "run_dir", "run_dir_count", "run_dirs",
    "exit_code", "termination", "termination_cause", "console_signals", "wall_s", "launched_at", "ended_at",
    "config_fingerprint", "commit", "files_created", "files_modified", "files_deleted",
    "raw_logs_match_record", "integrity_status", "integrity_notes", "post_exit_signals",
    # from summary.json / log.jsonl
    "data_status", "data_warnings",
    "summary_present", "task", "model", "dry_run", "agent_status", "agent_reason", "steps",
    "t_start", "t_end", "duration_s",
    "decide_calls", "grounding_calls", "verify_calls",
    "executed_actions", "dry_run_actions", "execution_errors", "aborted_actions",
    "not_executed_steps", "rejected_proposals",
    "failed_steps", "no_decision", "action_rejected", "grounding_failed",
    "execution_error", "outcome_not_confirmed", "finish_not_verified", "confirmations",
    "verified_pass_steps", "unverified_steps", "unknown_verifications",
    "failure_streaks", "streaks_recovered_verified", "streaks_recovered_unverified",
    "streaks_recovered_dry_run", "streaks_unrecovered",
    "max_retries_reached", "repeated_failed_actions", "repeated_location_rejections",
    "deterministic_check_final",
    "decide_latency_sum_s", "grounding_latency_sum_s", "verify_latency_sum_s",
]

STEP_COLUMNS = [
    "trial_id", "run_dir", "step", "action_text", "grounded", "grounding_status",
    "grounding_confidence", "validation", "execution_class", "execution", "expected_outcome",
    "verification_class", "verification", "deterministic_check", "failure_type",
    "repeated_failures", "retries_after", "decision_screenshot", "step_screenshot",
    "vlm_latency_s", "grounding_latency_s", "verify_latency_s", "log_status",
]

CALL_COLUMNS = ["trial_id", "kind", "cell_id", "run_dir", "step", "call_type", "purpose",
                "call_ok", "latency_s", "log_status"]
LATENCY_COLUMNS = ["call_type", "scope", "n_calls", "n_trials", "n_incomplete_trials",
                   "p50_s", "p90_s", "min_s", "max_s"]

REOBSERVING_FAILURES = {"NO_DECISION", "ACTION_REJECTED", "GROUNDING_FAILED", "EXECUTION_ERROR"}


# --- reading -------------------------------------------------------------------------

REQUIRED_RECORD_KEYS = ("trial_id", "cell_id", "kind", "termination", "exit_code", "wall_s",
                        "launched_at", "ended_at", "config_fingerprint", "config")


def record_problem(record, path):
    """What makes this parsed trial record unusable, or None."""
    if not isinstance(record, dict):
        return "not a JSON object"
    missing = [k for k in REQUIRED_RECORD_KEYS if k not in record]
    if missing:
        return f"missing key(s) {', '.join(missing)}"
    try:
        if not isinstance(record["config"]["commit"], str):
            raise TypeError
        record["config"]["settings"]["VLM_MODEL"]["value"]
    except (KeyError, TypeError):
        return "config.commit or config.settings.VLM_MODEL.value is missing"
    if record["trial_id"] != Path(path).stem:
        return f"file is named {Path(path).stem} but the record says trial_id {record['trial_id']!r}"
    return None


def load_trials(meta_dir=META_DIR, runs_dir=RUNS_DIR, extra_run_dirs=(), bad=None):
    """[(record or None, run_dir Path or None)] sorted by trial id.

    A record file that cannot be parsed or lacks required keys is never guessed at: with `bad` (a
    list) it is appended there as (file name, reason) and skipped; without it ValueError is raised.
    """
    items = []
    meta_dir = Path(meta_dir)
    if meta_dir.is_dir():
        for path in sorted(meta_dir.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                reason = record_problem(record, path)
            except ValueError as e:                     # JSONDecodeError and UnicodeDecodeError
                record, reason = None, f"{type(e).__name__}: {e}"
            if reason:
                if bad is None:
                    raise ValueError(f"malformed trial record {path.name}: {reason}")
                bad.append((path.name, reason))
                continue
            run_dir = Path(runs_dir) / record["run_dir"] if record.get("run_dir") else None
            items.append((record, run_dir))
    items += [(None, Path(d)) for d in extra_run_dirs]
    return items


def run_folder_status(record):
    """Why a record has no single run folder to read (its run_dir is null). Never picks one."""
    if record.get("termination") == "not_started_interrupted":
        return "not_started"
    n = len(record.get("run_dirs", []))
    return "no_run_folder" if n == 0 else "ambiguous_run_folders" if n > 1 else "run_folder_unresolved"


def read_log(run_dir):
    """(entries, problems) from run_dir/log.jsonl.

    A line that is not a JSON object is skipped and reported, never counted: the
    last one is usually a record torn by a kill, an earlier one is plain corruption.
    """
    log_path = Path(run_dir) / "log.jsonl" if run_dir else None
    if not log_path or not log_path.exists():
        return [], []
    text = log_path.read_bytes().decode("utf-8", errors="replace")
    lines = [(n, line) for n, line in enumerate(text.split("\n"), 1) if line.strip()]
    entries, problems = [], []
    for position, (n, line) in enumerate(lines):
        try:
            entry = json.loads(line)
            if not isinstance(entry, dict):
                raise ValueError("not a JSON object")
        except ValueError as e:                   # JSONDecodeError is a ValueError
            is_last = position == len(lines) - 1
            what = ("truncated final record" if is_last and not text.endswith("\n")
                    else "malformed final record" if is_last else "malformed record")
            problems.append(f"log.jsonl line {n}: {what} ({e}); not counted")
            continue
        entries.append(entry)
    return entries, problems


def read_entries(run_dir):
    return read_log(run_dir)[0]


def load_summary(run_dir):
    """(summary or None, problems). An unreadable summary.json is a problem, not absence."""
    path = Path(run_dir) / "summary.json" if run_dir else None
    if not path or not path.exists():
        return None, []
    try:
        summary = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(summary, dict):
            raise ValueError("not a JSON object")
        return summary, []
    except ValueError as e:
        return None, [f"summary.json: malformed ({e}); not used"]


def read_summary(run_dir):
    return load_summary(run_dir)[0]


def data_problems(run_dir):
    """Everything wrong with the raw files of one run folder ([] = complete)."""
    return read_log(run_dir)[1] + load_summary(run_dir)[1]


# --- classification ------------------------------------------------------------------

def execution_class(entry):
    x = str(entry.get("execution", ""))
    if not x:
        return "missing"
    if x.startswith("ABORTED"):
        return "aborted"
    if x.startswith("ERROR"):
        return "error"
    if x.startswith("[DRY RUN]"):
        return "dry_run"
    if x.startswith(("not reached", "not executed", "none (FINISH")):
        return "not_executed"
    return "executed"


def verification_class(entry):
    v = str(entry.get("verification", ""))
    if not v:
        return "none"
    if v.startswith("PASS"):
        return "pass"
    if v.startswith("skipped (dry run"):
        return "skipped_dry_run"
    if v.startswith("skipped"):
        return "skipped_unverified"
    if v.startswith("UNKNOWN") or v.startswith("NOT VERIFIED: verification unavailable"):
        return "unknown"
    if v.startswith("CONFIRM"):
        return "confirm"
    return "fail"


def streaks(entries):
    counts = dict(failure_streaks=0, streaks_recovered_verified=0,
                  streaks_recovered_unverified=0, streaks_recovered_dry_run=0,
                  streaks_unrecovered=0)
    in_streak = False
    for e in entries:
        kind = e.get("failure_type", "")
        if kind == "CONFIRMATION_NEEDED":         # neither starts nor ends a streak
            continue
        if kind:
            if not in_streak:
                counts["failure_streaks"] += 1
                in_streak = True
            continue
        if not in_streak:
            continue
        in_streak = False
        if execution_class(e) == "aborted":
            counts["streaks_unrecovered"] += 1
        else:
            outcome = {"pass": "verified", "skipped_dry_run": "dry_run"}.get(
                verification_class(e), "unverified")
            counts[f"streaks_recovered_{outcome}"] += 1
    if in_streak:
        counts["streaks_unrecovered"] += 1
    return counts


def calls(entries):
    """One dict per model call logged in the entries."""
    out = []
    for e in entries:
        is_finish = str(e.get("execution", "")).startswith("none (FINISH")
        if isinstance(e.get("vlm_latency_s"), (int, float)):
            out.append(dict(step=e.get("step"), call_type="decide", purpose="decide",
                            call_ok=e.get("failure_type") != "NO_DECISION",
                            latency_s=e["vlm_latency_s"]))
        if isinstance(e.get("grounding_latency_s"), (int, float)):
            status = (e.get("grounding") or {}).get("status")
            out.append(dict(step=e.get("step"), call_type="grounding", purpose="click_target",
                            call_ok=status != "error", latency_s=e["grounding_latency_s"]))
        if isinstance(e.get("verify_latency_s"), (int, float)):
            out.append(dict(step=e.get("step"), call_type="verify",
                            purpose="finish" if is_finish else "step",
                            call_ok=verification_class(e) != "unknown",
                            latency_s=e["verify_latency_s"]))
    return out


def decision_screenshots(entries):
    """Which screenshot each step's decision saw, or '' if it was never saved (GAP-5)."""
    shots, previous = [], None
    for e in entries:
        if previous is None:
            shot = "step_00_start.png"
        elif previous.get("failure_type") in REOBSERVING_FAILURES:
            shot = ""                             # re-observed after a failure: not saved
        elif previous.get("screenshot"):
            shot = previous["screenshot"]
        else:                                     # FINISH claim: the same screenshot again
            shot = shots[-1]
        shots.append(shot)
        previous = e
    return shots


# --- rows ----------------------------------------------------------------------------

def _iso(timestamp):
    return datetime.fromtimestamp(timestamp).isoformat(timespec="milliseconds")


def manifest_problems(record, runs_dir=RUNS_DIR):
    """[] if every run folder still matches the hashes taken at the end of the trial."""
    problems = []
    for name, recorded in (record.get("manifest") or {}).items():
        folder = Path(runs_dir) / name
        if not folder.is_dir():
            problems.append(f"{name}: run folder missing")
            continue
        current = manifest(folder)
        for f in sorted(recorded.keys() - current.keys()):
            problems.append(f"{name}/{f}: missing")
        for f in sorted(current.keys() - recorded.keys()):
            problems.append(f"{name}/{f}: added after the trial")
        for f in sorted(recorded.keys() & current.keys()):
            if recorded[f] != current[f]:
                problems.append(f"{name}/{f}: changed after the trial")
    return problems


def termination_cause(record):
    """How the process ended, kept apart from success (exit codes say nothing about that)."""
    how = record.get("termination")
    if how == "not_started_interrupted":
        return how
    if how == "wall_clock_kill":
        return "timeout"
    if record.get("console_signals"):
        return "console_interrupt"
    return "exited" if how == "exited" else str(how)


def trial_integrity(record, run_dir, runs_dir=RUNS_DIR):
    """Harness-evidence problems of ONE trial (incomplete logs are not among them)."""
    tid, problems = record["trial_id"], []
    if labels.TRIAL_ID.match(tid) and labels.is_planned(tid):            # planned id: its cell is fixed
        wrong_cell = labels.cell_problem(tid, record.get("cell_id"))
        if wrong_cell:
            problems.append(f"{tid}: {wrong_cell}; the record cannot be attributed to a cell")
    if record.get("termination") != "not_started_interrupted" and len(record.get("run_dirs", [])) != 1:
        problems.append(f"{tid}: {len(record.get('run_dirs', []))} run folders "
                        f"(expected 1): {record.get('run_dirs')}")
    problems += [f"{tid}: {p}" for p in manifest_problems(record, runs_dir)]
    pre = record.get("preflight") or {}
    if pre.get("expect_file"):
        should_exist = record.get("cell_id") == "NOTE-H1"      # its setup creates the placeholder
        if bool(pre.get("existed_before")) != should_exist:
            problems.append(f"{tid}: expected file {pre['expect_file']} "
                            f"{'did not exist' if should_exist else 'already existed'} before "
                            f"the trial (precondition of cell {record.get('cell_id')})")
    summary = read_summary(run_dir)
    configured = record["config"]["settings"]["VLM_MODEL"]["value"]
    if summary and configured and summary.get("model") != configured:
        problems.append(f"{tid}: summary model {summary.get('model')!r} != configured "
                        f"{configured!r}")
    return problems


def trial_row(record, run_dir, runs_dir=RUNS_DIR):
    row = {c: "" for c in TRIAL_COLUMNS}
    if record:
        watch = [w for w in (record.get("watch") or {}).values() if "error" not in w]
        row.update(
            trial_id=record["trial_id"], cell_id=record["cell_id"], kind=record["kind"],
            repeat_of=record.get("repeat_of") or "", run_dir=record.get("run_dir") or "",
            run_dir_count=len(record.get("run_dirs", [])), run_dirs=";".join(record.get("run_dirs", [])),
            exit_code=record["exit_code"],
            termination=record["termination"], termination_cause=termination_cause(record),
            console_signals=";".join(f"{s['signal']}@{s['after_s']}s"
                                     for s in record.get("console_signals", [])),
            wall_s=record["wall_s"], launched_at=record["launched_at"],
            ended_at=record["ended_at"], config_fingerprint=record["config_fingerprint"],
            commit=record["config"]["commit"][:12],
            files_created=";".join(n for w in watch for n in w["created"]),
            files_modified=";".join(n for w in watch for n in w["modified"]),
            files_deleted=";".join(n for w in watch for n in w["deleted"]),
            raw_logs_match_record=not manifest_problems(record, runs_dir),
            integrity_status="ok", post_exit_signals=";".join(f"{s['signal']}@{s['after_s']}s"
                                       for s in record.get("post_exit_signals", [])))
        found = trial_integrity(record, run_dir, runs_dir)
        row["integrity_status"] = "problems" if found else "ok"
        row["integrity_notes"] = " | ".join(found)
    elif run_dir:
        row["run_dir"] = Path(run_dir).name
    if run_dir is None:
        row["data_status"] = run_folder_status(record) if record else ""
        return row
    if record and not Path(run_dir).is_dir():
        row["data_status"] = "run_folder_missing"
        return row

    summary, summary_problems = load_summary(run_dir)
    entries, log_problems = read_log(run_dir)
    problems = log_problems + summary_problems
    row["data_status"] = "incomplete" if problems else "complete" if entries else "no_log"
    row["data_warnings"] = " | ".join(problems)
    row["summary_present"] = summary is not None
    if summary:
        row.update(task=summary.get("task", ""), model=summary.get("model", ""),
                   dry_run=summary.get("dry_run", ""), agent_status=summary.get("status", ""),
                   agent_reason=summary.get("reason", ""), steps=summary.get("steps", ""))
        row["t_end"] = _iso((Path(run_dir) / "summary.json").stat().st_mtime)
    start_shot = Path(run_dir) / "step_00_start.png"
    if start_shot.exists():
        row["t_start"] = _iso(start_shot.stat().st_mtime)
        if summary:
            row["duration_s"] = round((Path(run_dir) / "summary.json").stat().st_mtime
                                      - start_shot.stat().st_mtime, 1)

    kinds = [e.get("failure_type", "") for e in entries]
    exec_classes = [execution_class(e) for e in entries]
    ver_classes = [verification_class(e) for e in entries]
    model_calls = calls(entries)
    row.update(
        decide_calls=sum(c["call_type"] == "decide" for c in model_calls),
        grounding_calls=sum(c["call_type"] == "grounding" for c in model_calls),
        verify_calls=sum(c["call_type"] == "verify" for c in model_calls),
        executed_actions=exec_classes.count("executed"),
        dry_run_actions=exec_classes.count("dry_run"),
        execution_errors=exec_classes.count("error"),
        aborted_actions=exec_classes.count("aborted"),
        not_executed_steps=exec_classes.count("not_executed"),
        no_decision=kinds.count("NO_DECISION"),
        action_rejected=kinds.count("ACTION_REJECTED"),
        grounding_failed=kinds.count("GROUNDING_FAILED"),
        execution_error=kinds.count("EXECUTION_ERROR"),
        outcome_not_confirmed=kinds.count("OUTCOME_NOT_CONFIRMED"),
        finish_not_verified=kinds.count("FINISH_NOT_VERIFIED"),
        confirmations=kinds.count("CONFIRMATION_NEEDED"),
        failed_steps=sum(1 for k in kinds if k and k != "CONFIRMATION_NEEDED"),
        verified_pass_steps=ver_classes.count("pass"),
        unverified_steps=ver_classes.count("skipped_unverified"),
        unknown_verifications=ver_classes.count("unknown"),
        max_retries_reached=str(row["agent_reason"]).startswith("MAX_RETRIES"),
        repeated_failed_actions=sum(1 for e in entries if e.get("repeated_failures")),
        repeated_location_rejections=sum(
            1 for e in entries if isinstance(e.get("grounding"), dict)
            and e["grounding"].get("status") == "repeated_location"),
        **streaks(entries),
    )
    row["rejected_proposals"] = row["action_rejected"] + row["grounding_failed"]
    checks = [e["deterministic_check"] for e in entries if e.get("deterministic_check")]
    row["deterministic_check_final"] = checks[-1] if checks else ""
    for call_type in ("decide", "grounding", "verify"):
        values = [c["latency_s"] for c in model_calls if c["call_type"] == call_type]
        row[f"{call_type}_latency_sum_s"] = round(sum(values), 1) if values else ""
    return row


def step_rows(record, run_dir):
    entries, problems = read_log(run_dir)
    log_status = "incomplete" if problems else "complete"
    trial_id = record["trial_id"] if record else ""
    rows = []
    for e, shot in zip(entries, decision_screenshots(entries)):
        g = e.get("grounding") if isinstance(e.get("grounding"), dict) else {}
        rows.append({
            "trial_id": trial_id, "run_dir": Path(run_dir).name, "step": e.get("step"),
            "action_text": e.get("action_text", ""), "grounded": bool(g),
            "grounding_status": g.get("status", ""), "grounding_confidence": g.get("confidence", ""),
            "validation": e.get("validation", ""), "execution_class": execution_class(e),
            "execution": e.get("execution", ""), "expected_outcome": e.get("expected_outcome", ""),
            "verification_class": verification_class(e), "verification": e.get("verification", ""),
            "deterministic_check": e.get("deterministic_check", ""),
            "failure_type": e.get("failure_type", ""),
            "repeated_failures": e.get("repeated_failures", ""),
            "retries_after": e.get("retries_after", ""), "decision_screenshot": shot,
            "step_screenshot": e.get("screenshot", ""),
            "vlm_latency_s": e.get("vlm_latency_s", ""),
            "grounding_latency_s": e.get("grounding_latency_s", ""),
            "verify_latency_s": e.get("verify_latency_s", ""), "log_status": log_status,
        })
    return rows


def call_rows(record, run_dir):
    entries, problems = read_log(run_dir)
    base = {"log_status": "incomplete" if problems else "complete",
            "trial_id": record["trial_id"] if record else "",
            "kind": record["kind"] if record else "", "cell_id": record["cell_id"] if record else "",
            "run_dir": Path(run_dir).name}
    return [{**base, **c} for c in calls(entries)]


# --- pooled latency ------------------------------------------------------------------

def percentile(values, q):
    """Linear interpolation between closest ranks (statistics 'inclusive' method)."""
    values = sorted(values)
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    return round(statistics.quantiles(values, n=100, method="inclusive")[q - 1], 2)


def latency_table(rows):
    out = []
    for call_type in ("decide", "grounding", "verify"):
        for scope in ("all calls", "successful calls only"):
            values = [float(r["latency_s"]) for r in rows if r["call_type"] == call_type
                      and (scope == "all calls" or str(r["call_ok"]) == "True")]
            out.append({"call_type": call_type, "scope": scope, "n_calls": len(values),
                        "n_trials": len({r["trial_id"] or r["run_dir"] for r in rows
                                         if r["call_type"] == call_type}),
                        "n_incomplete_trials": len({r["trial_id"] or r["run_dir"] for r in rows
                                                    if r["call_type"] == call_type
                                                    and r.get("log_status") == "incomplete"}),
                        "p50_s": percentile(values, 50), "p90_s": percentile(values, 90),
                        "min_s": min(values) if values else "",
                        "max_s": max(values) if values else ""})
    return out


# --- consistency check ---------------------------------------------------------------

def check(items, runs_dir=RUNS_DIR):
    """INTEGRITY problems that would make a dataset unreliable. [] means intact.

    Incomplete logs are reported separately (incomplete_trials): they are evidence
    to keep and flag, not a reason to discard a trial.
    """
    problems, by_kind = [], {}
    for record, run_dir in items:
        if record is None:
            continue
        problems += trial_integrity(record, run_dir, runs_dir)
        by_kind.setdefault(record["kind"], {}).setdefault(
            record["config_fingerprint"], []).append(record["trial_id"])
    for kind in ("main", "pilot"):
        groups = by_kind.get(kind, {})
        if len(groups) > 1:
            detail = "; ".join(f"{fp}: {', '.join(ids)}" for fp, ids in sorted(groups.items()))
            problems.append(f"{kind} trials mix {len(groups)} configurations (never pool them): "
                            f"{detail}")
    return problems


def incomplete_trials(items):
    """[(label, [problem, ...])] for run folders whose log or summary is unreadable."""
    return [(r["trial_id"] if r else Path(d).name, found)
            for r, d in items if d for found in [data_problems(d)] if found]


# --- CLI -----------------------------------------------------------------------------

def _write(rows, columns, out):
    stream = open(out, "w", newline="", encoding="utf-8") if out else sys.stdout
    try:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if out:
            stream.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["trials", "steps", "calls", "latency", "check", "labels"])
    parser.add_argument("--meta-dir", default=str(META_DIR))
    parser.add_argument("--runs-dir", default=str(RUNS_DIR))
    parser.add_argument("--run-dir", action="append", default=[],
                        help="extra run folder without a trial record (ad hoc only)")
    parser.add_argument("--kind", choices=["main", "pilot", "control", "selftest"],
                        help="only trials of this kind")
    parser.add_argument("--out", help="write CSV here instead of stdout")
    parser.add_argument("--human-csv", default=str(HUMAN_CSV), help="the human sheet, for `labels`")
    parser.add_argument("--deviations",
                        help="the deviations log, for `labels`; only eval/results/deviations.md (the default) is "
                             "read, any other file authorizes nothing. A P2- pilot re-run needs one APPROVED "
                             "P2-AUTH entry for its exact id in it")
    parser.add_argument("--require-complete", action="store_true",
                        help="`labels`: an unlabelled trial is a problem, not just pending")
    args = parser.parse_args(argv)

    bad_records = []
    all_items = load_trials(args.meta_dir, args.runs_dir, args.run_dir, bad_records)
    for name, why in bad_records:
        print(f"WARNING: malformed trial record {name}: {why} (skipped; the file is untouched)",
              file=sys.stderr)
    if args.command == "labels":
        return _labels_command(args, all_items, bad_records)
    items, left_out = all_items, []
    if args.kind:
        items = [(r, d) for r, d in items if r and r["kind"] == args.kind]
    else:                                         # self-tests never reach evaluation output
        left_out = [r["trial_id"] for r, _ in items if r and r["kind"] == "selftest"]
        items = [(r, d) for r, d in items if not (r and r["kind"] == "selftest")]
        if left_out:
            print(f"NOTE: {len(left_out)} selftest record(s) left out (use --kind selftest): "
                  f"{', '.join(left_out)}", file=sys.stderr)

    incomplete = incomplete_trials(items)
    for label, found in incomplete:
        for problem in found:
            print(f"WARNING: {label}: {problem}", file=sys.stderr)

    if args.command in ("steps", "calls", "latency"):
        no_folder = [f"{r['trial_id']} ({run_folder_status(r)}"
                     + (f": {';'.join(r['run_dirs'])}" if len(r.get("run_dirs", [])) > 1 else "") + ")"
                     for r, d in items if r and d is None and r.get("termination") != "not_started_interrupted"]
        if no_folder:
            print(f"NOTE: {len(no_folder)} trial(s) contribute no {args.command} rows because they have no "
                  f"unique run folder: {', '.join(no_folder)}", file=sys.stderr)

    if args.command == "check":
        problems = [f"{name}: malformed trial record: {why}" for name, why in bad_records]
        problems += check(items, args.runs_dir)
        lines = list(problems) + [f"INCOMPLETE: {label}: {p}" for label, found in incomplete
                                  for p in found]
        if problems:
            lines.append(f"NOT OK: {len(problems)} integrity problem(s)"
                         + (f"; {len(incomplete)} trial(s) with incomplete data" if incomplete else ""))
        elif incomplete:
            lines.append(f"NOT OK (incomplete data only): {len(incomplete)} trial(s). Integrity is "
                         f"intact; the trials are kept and flagged, not invalid by themselves "
                         f"(protocol 6.6).")
        elif not items:
            lines.append("NOTHING TO CHECK: 0 trial records matched"
                         + (f" ({len(left_out)} selftest record(s) were left out; use --kind selftest)"
                            if left_out else ""))
        else:
            lines.append(f"OK: {len(items)} trial(s) checked")
        print("\n".join(lines))
        return 1 if problems else 2 if incomplete else 0
    if args.command == "trials":
        _write([trial_row(r, d, args.runs_dir) for r, d in items], TRIAL_COLUMNS, args.out)
    elif args.command == "steps":
        _write([row for r, d in items if d for row in step_rows(r, d)], STEP_COLUMNS, args.out)
    elif args.command == "calls":
        _write([row for r, d in items if d for row in call_rows(r, d)], CALL_COLUMNS, args.out)
    else:
        rows = [row for r, d in items if d for row in call_rows(r, d)]
        _write(latency_table(rows), LATENCY_COLUMNS, args.out)
    if bad_records:
        print(f"INCOMPLETE DATA: {len(bad_records)} malformed trial record file(s) were skipped, so "
              f"those trials are missing from this output.", file=sys.stderr)
    if incomplete:
        print(f"INCOMPLETE DATA: {len(incomplete)} run folder(s) have unreadable records; "
              f"affected rows are marked incomplete and those records are NOT counted.",
              file=sys.stderr)
    return 2 if (incomplete or bad_records) else 0


def _agent_status(record, run_dir):
    """'success' / 'failed' / 'aborted', 'NO_SUMMARY' if the run wrote none, None if unknowable."""
    if run_dir is None:
        return "NO_SUMMARY" if len(record.get("run_dirs", [])) == 0 else None   # several folders: unknown
    summary, problems = load_summary(run_dir)
    if summary is not None:
        return summary.get("status")
    return None if problems else "NO_SUMMARY"


def _labels_command(args, all_items, bad_records):
    rows, problems = labels.read_human_sheet(args.human_csv)
    records = {r["trial_id"]: r for r, _ in all_items if r}
    statuses = {r["trial_id"]: _agent_status(r, d) for r, d in all_items if r}
    deviations = labels.load_deviations(args.deviations)
    found, pending = labels.validate_sheet(rows, records, statuses, args.require_complete, deviations)
    problems = problems + found + [f"{name}: malformed trial record: {why}" for name, why in bad_records]
    lines = list(problems)
    if pending:
        lines.append(f"PENDING: {len(pending)} trial(s) not labelled yet: {', '.join(pending)}")
    lines.append(f"NOT OK: {len(problems)} problem(s) in the human sheet" if problems
                 else f"OK: {len(rows)} labelled row(s) valid against {len(records)} trial record(s)")
    print("\n".join(lines))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
