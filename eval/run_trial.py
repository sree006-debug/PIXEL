"""Run ONE evaluation trial and write its trial record.

    .venv\\Scripts\\python.exe eval\\run_trial.py --trial-id R1-CALC-1 --cell CALC-1 @W -- main.py --demo calculator

Everything after `--` is passed to the venv's Python as the child process
(main.py ... or -m gui.app). This script:

  - refuses to start if agent/, gui/, main.py or config.py differ from the commit,
    or if a record for this trial id already exists (nothing is ever replaced);
  - refuses a SELFTEST-* id whose --meta-dir is inside eval/results (self-test
    records must never mix with evaluation data; use a scratch folder);
  - checks the child's --expect-file before launching (see preflight): it must be
    the trial's own eval/Desktop file `pixel_eval_<trial id>.txt` inside the REAL
    Desktop folder (OneDrive redirection included), and `--demo notepad` needs an
    explicit --expect-file (the demo's fixed robotics_list.txt is never used);
    whether that file already existed (stat only; never opened) goes in the record;
  - never deletes, moves or edits any file other than its own record;
  - contains the child in a kill-on-close Windows Job Object (eval/job_object.py):
    the child is created already inside the job (PROC_THREAD_ATTRIBUTE_JOB_LIST) and
    suspended, its membership is verified, and only then is it resumed. So it never
    runs uncontained, there is no instant at which it exists outside the job, and if
    this script is force-killed Windows kills the child and its descendants, running
    or suspended. If containment cannot be set up the trial does not start. The
    wall-clock limit ends the whole tree. After a normal exit, any descendant still
    running is counted (containment.descendants_terminated_at_exit) and terminated:
    the job is armed for its whole life, so nothing the child started outlives the trial.
    Limits: a trial record cannot be written once the harness is force-killed (no
    code runs after TerminateProcess; the record is only written after the child
    ends), and the job-list attribute needs Windows 10 or newer;
  - waits --delay seconds (minimise the terminal now), then launches the child;
    Ctrl+C / Ctrl+Break during the wait launches nothing and writes a record with
    termination "not_started_interrupted" (the trial id is then used up: label it
    INVALID in the human sheet and run the -REP1 repeat);
  - kills the child at the wall-clock limit (--limit-min, default 15);
  - beeps twice when the child has ended;
  - counts Ctrl+C / Ctrl+Break instead of dying from them, from before the start
    wait until the record is on disk: the child (same console) gets them and this
    script still records the child's real exit code. Presses after the child has
    ended are kept in post_exit_signals. The recorded times are when this script's
    handler ran: they can lag the key press (0.5 s poll; Ctrl+Break does not wake it)
    and presses that arrive together are listed in signal-number order, not press order. The record is written to a temporary
    file and renamed, so a half-written record is never left behind. This does
    NOT survive a forced termination (TerminateProcess, closing the console
    window, power loss) or a press that arrives after the record was written;
  - writes eval/results/meta/<trial_id>.json: run_dir, exit code, how the
    process ended, a version/configuration record and its fingerprint,
    sha256 of every file in the run folder, and a before/after listing of
    each --watch folder (top level only).

It never imports the agent, pyautogui or the SDK, never writes into runs/,
and never records the API key (only where it came from).
"""

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import job_object  # same folder: Windows Job Object containment of the child
import labels  # same folder: label vocabulary and repeat rules

PROJECT_DIR = Path(__file__).resolve().parent.parent
RUNS_DIR = PROJECT_DIR / "runs"
RESULTS_DIR = PROJECT_DIR / "eval" / "results"
PROTECTED_PATHS = ["agent", "gui", "main.py", "config.py"]
CONFIG_ENV = ["VLM_PROVIDER", "VLM_MODEL", "GEMINI_THINKING_LEVEL", "VLM_TIMEOUT"]
PACKAGES = ["google-genai", "pyautogui", "pillow", "python-dotenv"]
DEFAULT_LIMIT_MIN = 15

# Trial ids, repeat rules and the label vocabulary live in labels.py (one source of truth).
from labels import INVALID_REASONS, MAX_REPEATS, REPEAT_SUFFIX, TRIAL_ID  # noqa: E402,F401


EVAL_FILE_PREFIX = "pixel_eval_"


class TrialError(Exception):
    """The trial must not start (nothing was launched)."""


def kind_of(trial_id):
    prefix = trial_id.split("-", 1)[0]
    if prefix.startswith("R"):
        return "main"
    if prefix.startswith("P"):
        return "pilot"
    return {"C": "control", "SELFTEST": "selftest"}[prefix]


# --- command-line preflight --------------------------------------------------------

def desktop_dir():
    """The real Desktop folder (a redirect such as OneDrive\\Desktop included), or None."""
    try:
        import winreg
    except ImportError:                          # not Windows
        return None
    for sub in ("Shell Folders", "User Shell Folders"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                rf"Software\Microsoft\Windows\CurrentVersion\Explorer\{sub}") as key:
                value = os.path.expandvars(winreg.QueryValueEx(key, "Desktop")[0])
        except OSError:
            continue
        if value:
            return value
    return None


def _option(command, name):
    """Value of `--name VALUE` or `--name=VALUE` in a child command line, else None."""
    for i, arg in enumerate(command):
        if arg == name and i + 1 < len(command):
            return command[i + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


def _canonical(path):
    """Absolute, case-folded path with 8.3 short names, junctions and symlinks resolved."""
    return os.path.normcase(os.path.realpath(str(path)))


def _same_path(a, b):
    return _canonical(a) == _canonical(b)


def _inside(path, root):
    path, root = _canonical(path), _canonical(root)
    return path == root or path.startswith(root + os.sep)


def preflight(trial_id, command, desktop_fn=None):
    """Checks on the child's command line that must hold before anything is launched.

    {} if there is no --expect-file. Otherwise the file must be named exactly
    pixel_eval_<trial id>.txt and sit directly in the real Desktop, so a check can
    never point at (or a cleanup at) a file the experimenter already owned. Only
    os.stat is used on it: its content is never read.
    """
    expect = _option(command, "--expect-file")
    if expect is None:
        if _option(command, "--demo") == "notepad" and "--dry-run" not in command:
            raise TrialError("--demo notepad checks a fixed robotics_list.txt; pass an explicit "
                             f"--expect-file for {EVAL_FILE_PREFIX}{trial_id}.txt (see NOTEARGS)")
        return {}
    desktop = (desktop_fn or desktop_dir)()
    if not desktop:
        raise TrialError("cannot determine the real Desktop folder, so --expect-file cannot be checked")
    path = Path(os.path.expandvars(expect))
    wanted = f"{EVAL_FILE_PREFIX}{trial_id}.txt"
    if path.name.lower() != wanted.lower():
        raise TrialError(f"--expect-file must be named {wanted} (this trial's own file), not {path.name!r}")
    if not _same_path(path.parent, desktop):
        raise TrialError(f"--expect-file must be directly inside the real Desktop {desktop}, "
                         f"not {path.parent}")
    existed = path.is_file()
    return {"expect_file": str(path), "desktop": str(desktop), "inside_desktop": True,
            "existed_before": existed, "size_before": path.stat().st_size if existed else None}


# --- version / configuration record ---------------------------------------------

def _git(*args):
    out = subprocess.run(["git", *args], cwd=PROJECT_DIR, capture_output=True, text=True)
    if out.returncode != 0:
        raise TrialError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def _package_version(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return "MISSING"


def _env_setting(name, dotenv):
    if os.environ.get(name):
        return {"value": os.environ[name], "source": "env"}
    if dotenv.get(name):
        return {"value": dotenv[name], "source": ".env"}
    return {"value": "", "source": "unset (code default)"}


def _screen():
    try:
        import ctypes
        user32 = ctypes.windll.user32
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)   # this process only
        except Exception:
            user32.SetProcessDPIAware()
        dpi = user32.GetDpiForSystem()
        return {"width": user32.GetSystemMetrics(0), "height": user32.GetSystemMetrics(1),
                "monitors": user32.GetSystemMetrics(80), "scale_percent": round(dpi * 100 / 96)}
    except Exception as e:                       # not Windows, or an old API
        return {"error": str(e)}


def collect_config():
    """What could change the agent's behaviour between trials. No secrets."""
    dirty = _git("status", "--porcelain", "--", *PROTECTED_PATHS)
    if dirty:
        raise TrialError("agent code differs from the commit; commit or revert first:\n" + dirty)
    from dotenv import dotenv_values
    dotenv = dotenv_values(PROJECT_DIR / ".env") if (PROJECT_DIR / ".env").exists() else {}
    key = _env_setting("GEMINI_API_KEY", dotenv)
    return {
        "commit": _git("rev-parse", "HEAD"),
        "protected_paths_clean": True,
        "settings": {name: _env_setting(name, dotenv) for name in CONFIG_ENV},
        "api_key_source": key["source"],
        "packages": {name: _package_version(name) for name in PACKAGES},
        "python": platform.python_version(),
        "windows": platform.platform(),
        "screen": _screen(),
    }


def fingerprint(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]


# --- file evidence ----------------------------------------------------------------

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def manifest(run_dir):
    return {p.relative_to(run_dir).as_posix(): sha256(p)
            for p in sorted(Path(run_dir).rglob("*")) if p.is_file()}


def list_dir(folder):
    """Top-level listing: name -> [size, mtime]. Read-only."""
    folder = Path(folder)
    if not folder.is_dir():
        return None
    return {p.name: [p.stat().st_size if p.is_file() else None, round(p.stat().st_mtime, 3)]
            for p in folder.iterdir()}


def diff_listing(before, after):
    if before is None or after is None:
        return {"error": "folder missing"}
    return {"created": sorted(after.keys() - before.keys()),
            "deleted": sorted(before.keys() - after.keys()),
            "modified": sorted(n for n in before.keys() & after.keys() if before[n] != after[n])}


def run_dirs(runs_dir):
    runs_dir = Path(runs_dir)
    return {p.name for p in runs_dir.iterdir() if p.is_dir()} if runs_dir.is_dir() else set()


# --- repeats ----------------------------------------------------------------------

def check_repeat(trial_id, repeat_of, meta_dir, human_csv):
    """A repeat is allowed only for a recorded trial that a human labelled INVALID. Nothing is
    ever rewritten: the original record and its sheet row stay exactly as they are."""
    match = REPEAT_SUFFIX.match(trial_id)
    if repeat_of is None:
        if match:
            raise TrialError(f"{trial_id} looks like a repeat; pass --repeat-of")
        return
    bad = labels.repeat_id_problem(trial_id, repeat_of)
    if bad:
        raise TrialError(f"{trial_id}: {bad}")
    record_path = Path(meta_dir) / f"{repeat_of}.json"
    if not record_path.exists():
        raise TrialError(f"no trial record for {repeat_of}")
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("trial_id") != repeat_of:
            raise ValueError("it is not the record of that trial")
    except ValueError as e:
        raise TrialError(f"the trial record of {repeat_of} is unreadable ({e}); refusing to repeat it")
    rows, problems = labels.read_human_sheet(
        human_csv, required=("trial_id", "outcome_label", "invalid_reason"))
    if problems:
        raise TrialError(f"cannot read the human sheet: {'; '.join(problems)}")
    matching = [r for r in rows if r.get("trial_id") == repeat_of]
    if len(matching) != 1:
        raise TrialError(f"{repeat_of} needs exactly one labelled row in {human_csv}, found {len(matching)}")
    bad = labels.repeatable_problem(matching[0])
    if bad:
        raise TrialError(f"{repeat_of} {bad}")


# --- the child process ------------------------------------------------------------

class ConsoleGuard:
    """Count Ctrl+C / Ctrl+Break instead of dying from them (the previous handlers
    come back on exit). Python runs the handler between bytecodes, so it never
    raises into whatever this script is doing at the time."""

    SIGNALS = [signal.SIGINT] + ([signal.SIGBREAK] if hasattr(signal, "SIGBREAK") else [])

    def __init__(self):
        self.presses = []                        # [(name, time.time())]
        self._previous = {}

    def _on_signal(self, signum, frame):
        self.presses.append((signal.Signals(signum).name, time.time()))

    def __enter__(self):
        self._previous = {s: signal.signal(s, self._on_signal) for s in self.SIGNALS}
        return self

    def __exit__(self, *exc):
        for s, handler in self._previous.items():
            signal.signal(s, handler)

    def between(self, start, end, origin=None):
        """Presses with start <= time <= end, as record entries (after_s counts from origin)."""
        origin = start if origin is None else origin
        return [{"signal": name, "at": _iso(t), "after_s": round(t - origin, 1)}
                for name, t in self.presses if start <= t <= end]


def run_child(cmd, limit_s, cwd=PROJECT_DIR, poll_s=0.5, guard=None):
    """Run cmd until it exits or limit_s passes (then kill it). Ctrl+C/Ctrl+Break
    are counted, not acted on: the child shares the console and receives them.
    A guard from the caller keeps counting after this returns."""
    if guard is None:
        with ConsoleGuard() as own:
            return run_child(cmd, limit_s, cwd, poll_s, own)
    proc, job = launch_contained(cmd, cwd)
    terminated_at_exit, containment_error = None, None
    try:
        started = time.time()
        termination = "exited"
        while proc.poll() is None:
            if time.time() - started >= limit_s:
                kill_tree(proc, job)
                termination = "wall_clock_kill"
                break
            time.sleep(poll_s)
        ended = time.time()
        if job is not None and termination == "exited":
            # The job is armed for life: whatever the child left running is terminated here, before
            # the run folder is hashed and the record written. wait_empty returns once the job
            # reports no active process; the OS finishes tearing them down within tens of
            # milliseconds (measured: at most 44 ms). If counting or waiting fails, the close
            # below still kills them and the finished trial's record is still written (error noted).
            try:
                terminated_at_exit = job.active_processes()
                if terminated_at_exit:
                    job.terminate()
                    if not job.wait_empty(5):
                        containment_error = (f"{job.active_processes()} process(es) still in the job "
                                             f"5 s after terminate")
            except OSError as e:
                containment_error = str(e)
    finally:
        if job is not None:
            job.close()            # always armed: kills anything still in the job (any path)
            proc.close()
    return {"launched_at": _iso(started), "ended_at": _iso(ended),
            "wall_s": round(ended - started, 1), "limit_s": limit_s,
            "termination": termination, "exit_code": proc.returncode,
            "console_signals": guard.between(started, ended),
            "containment": {"method": "job_object" if job is not None else "none (not Windows)",
                            "descendants_terminated_at_exit": terminated_at_exit,
                            **({"error": containment_error} if containment_error else {})},
            "_started": started, "_ended": ended}


def launch_contained(cmd, cwd):
    """(proc, job). On Windows the child is created already inside a kill-on-close Job
    Object and suspended; its membership is verified before it is resumed, so it never
    runs uncontained and there is no moment at which it exists outside the job. It dies
    with the harness. If the child cannot be created inside the job nothing exists; if
    verification or resume fails it is killed before it ran. Either way the trial is
    refused (fails closed)."""
    if sys.platform != "win32":
        return subprocess.Popen(cmd, cwd=cwd), None
    try:
        job = job_object.JobObject()
    except OSError as e:
        raise TrialError(f"containment unavailable ({e}); the child was not started")
    try:
        proc = job.create_process(cmd, cwd)
    except OSError as e:
        job.close()
        raise TrialError(f"could not start the child inside the job: {e}")
    try:
        if not job.contains(proc):
            raise OSError("the new process is not a member of the job")
        proc.resume()
    except BaseException as e:               # whatever went wrong, no suspended member may linger
        proc.kill()
        proc.wait()
        proc.close()
        job.close()
        if isinstance(e, OSError):
            raise TrialError(f"containment failed ({e}); the child was killed before it ran")
        raise
    return proc, job


def kill_tree(proc, job):
    """End the child and everything it started (TerminateProcess alone would miss them)."""
    try:
        if job is not None:
            job.terminate()
            job.wait_empty(5)
        else:
            proc.kill()
    except OSError:
        proc.kill()
    proc.wait()


def interruptible_sleep(seconds, guard, tick=0.1):
    """Sleep, but return True as soon as the guard has counted a press."""
    end = time.time() + seconds
    while not guard.presses:
        remaining = end - time.time()
        if remaining <= 0:
            return False
        time.sleep(min(tick, remaining))
    return True


def write_record(meta_path, record):
    """Write via a temporary file and rename, so the record is whole or absent."""
    meta_path = Path(meta_path)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = meta_path.with_name(meta_path.name + ".tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(tmp, meta_path)


def beep():
    """Audible end-of-run signal, so the experimenter need not touch the desktop to check."""
    try:
        import winsound
        for _ in range(2):
            winsound.Beep(880, 250)
    except Exception:
        pass


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp).isoformat(timespec="milliseconds")


def run_trial(args, config_fn=None):
    trial_id = args.trial_id
    if not TRIAL_ID.match(trial_id):
        raise TrialError(f"bad trial id {trial_id!r} (e.g. R1-CALC-1, P-NOTE-1, C-STOP1-2)")
    bad = (labels.slot_problem(trial_id, labels.load_deviations(args.deviations))
           or labels.cell_problem(trial_id, args.cell))
    if bad:
        raise TrialError(bad)
    meta_dir = Path(args.meta_dir)
    meta_path = meta_dir / f"{trial_id}.json"
    if meta_path.exists():
        raise TrialError(f"{meta_path} already exists; a trial record is never replaced")
    if kind_of(trial_id) == "selftest" and _inside(meta_dir, RESULTS_DIR):
        raise TrialError(f"{trial_id} is a self-test: its --meta-dir must not be inside "
                         f"{RESULTS_DIR} (use e.g. scratch\\selftest\\meta)")
    check_repeat(trial_id, args.repeat_of, meta_dir, args.human_csv)
    if not args.command:
        raise TrialError("no command given after --")
    checked = preflight(trial_id, args.command)

    with ConsoleGuard() as guard:
        began = time.time()
        config = (config_fn or collect_config)()
        watch_before = {str(w): list_dir(w) for w in args.watch}
        runs_before = run_dirs(args.runs_dir)

        print(f"Trial {trial_id} ({args.cell}). Config {fingerprint(config)}. "
              f"Limit {args.limit_s / 60:g} min.")
        print(f"Starting in {args.delay:g} s: minimise this window now.")
        if interruptible_sleep(args.delay, guard):
            now = time.time()
            record = {
                "record_version": 1,
                "trial_id": trial_id, "cell_id": args.cell, "kind": kind_of(trial_id),
                "repeat_of": args.repeat_of,
                "command": args.command,
                "launched_at": None, "ended_at": _iso(now), "wall_s": 0.0,
                "limit_s": args.limit_s, "termination": "not_started_interrupted",
                "exit_code": None, "console_signals": guard.between(began, now),
                "post_exit_signals": [], "preflight": checked,
                "run_dirs": [], "run_dir": None, "manifest": {},
                "watch": {w: diff_listing(before, list_dir(w))
                          for w, before in watch_before.items()},
                "config": config, "config_fingerprint": fingerprint(config),
            }
            write_record(meta_path, record)
            print("\nNOT STARTED: interrupted during the start delay; nothing was launched.")
            print(f"  record:  {meta_path}")
            return record

        result = run_child([sys.executable, *args.command], args.limit_s, guard=guard)
        started, ended = result.pop("_started"), result.pop("_ended")
        beep()

        new_dirs = sorted(run_dirs(args.runs_dir) - runs_before)
        record = {
            "record_version": 1,
            "trial_id": trial_id, "cell_id": args.cell, "kind": kind_of(trial_id),
            "repeat_of": args.repeat_of,
            "command": args.command,
            **result,
            "preflight": checked,
            "run_dirs": new_dirs,
            "run_dir": new_dirs[0] if len(new_dirs) == 1 else None,
            "manifest": {d: manifest(Path(args.runs_dir) / d) for d in new_dirs},
            "watch": {w: diff_listing(before, list_dir(w)) for w, before in watch_before.items()},
            "config": config,
            "config_fingerprint": fingerprint(config),
        }
        # Presses between the child's end and now (beep, hashing): the child never saw them.
        record["post_exit_signals"] = guard.between(
            ended + 1e-6, time.time(), origin=started)
        write_record(meta_path, record)

    print(f"\nTRIAL {trial_id}: exit_code={result['exit_code']} "
          f"termination={result['termination']} wall_s={result['wall_s']}")
    print(f"  run_dir: {record['run_dir'] or new_dirs or '(none)'}")
    print(f"  record:  {meta_path}")
    return record


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--cell", required=True,
                        help="the cell the trial id encodes, e.g. CALC-H1 for R1-CALC-H1, C-STOP1 for C-STOP1-2, "
                             "SELFTEST for SELFTEST-*")
    parser.add_argument("--repeat-of", help="the INVALID trial this one repeats")
    parser.add_argument("--limit-min", type=float, default=DEFAULT_LIMIT_MIN)
    parser.add_argument("--delay", type=float, default=5.0, help="seconds before launch")
    parser.add_argument("--watch", action="append", default=[],
                        help="folder to list before/after (repeatable)")
    parser.add_argument("--meta-dir", default=str(RESULTS_DIR / "meta"))
    parser.add_argument("--human-csv", default=str(RESULTS_DIR / "trials.csv"))
    parser.add_argument("--deviations",
                        help="the deviations log; only eval/results/deviations.md (the default) is read, any other "
                             "file authorizes nothing. A P2- pilot re-run needs one APPROVED P2-AUTH entry for its "
                             "exact id in it")
    parser.add_argument("--runs-dir", default=str(RUNS_DIR))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    args.limit_s = args.limit_min * 60
    return args


def main(argv=None):
    try:
        record = run_trial(parse_args(argv))
    except TrialError as e:
        print(f"NOT STARTED: {e}")
        return 3
    return 3 if record["termination"] == "not_started_interrupted" else 0


if __name__ == "__main__":
    sys.exit(main())
