# PIXEL - Final Test Report

Date: 2026-10-22. Machine: Windows 11 (10.0.26200), Python 3.14.7, pytest 9.1.1,
PySide6 6.11.2, pyautogui 0.9.54, google-genai 2.25.0. Code base: commit `5960c0b`
plus the uncommitted changes listed in `PIXEL_CHANGE_SUMMARY.md`.

All automated tests use a scripted model, a fake screen and a mocked `pyautogui`:
they never move the real mouse, press real keys or call a network service. GUI
tests run with `QT_QPA_PLATFORM=offscreen`.

## 1. Results

| Run | Command | Passed | Failed | Skipped | Expected failures | Warnings | Subtests |
|---|---|---|---|---|---|---|---|
| Baseline (before this work) | `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -rxX` | 571 | 0 | 0 | 7 | 1 | 272 |
| Final (after all changes) | same, with `-rxXfs` | **626** | **0** | **0** | **1** | **1** | **380** |

Final run time: 166 s. The final run is the last full run after the last source
change (the documentation written afterwards is not read by any test).

**Reconciliation:** 571 baseline passes + 6 former strict xfails now passing
(5 terminal-name cases + Ctrl+W) + 1 new split-word execution test in
`tests/test_eval_failure_paths.py` + 20 in `tests/test_safety.py` + 22 in
`tests/test_cancellation.py` + 6 in `tests/test_offline_benchmark.py` = 626.

**The 1 remaining expected failure** (strict xfail):
`test_known_gap_blocked_word_split_over_two_type_actions`. It calls the stateless
per-action validator `parse_action()` twice; by design one call cannot know what
an earlier call typed. The split is blocked one layer later, in
`Controller.execute()` (`agent/safety.TypingGuard`), verified by
`test_gap10_split_word_is_blocked_at_execution` and `tests/test_safety.py`.

**The 1 warning:** `DeprecationWarning` from the `google-genai` package
(`_UnionGenericAlias`), not from project code.

## 2. Changed tests (none weakened, none removed, none skipped)

| Test | Change | Why |
|---|---|---|
| `test_known_gap_terminal_app_names_are_not_blocked` (5 cases) | strict xfail -> normal test `test_gap10_terminal_app_names_are_blocked` | the names are now blocked; the strict xfail XPASSed, as designed |
| `test_known_gap_ctrl_w_is_not_blocked` | strict xfail -> normal test `test_gap10_ctrl_w_is_blocked` | Ctrl+W is now blocked |
| `test_known_gap_blocked_word_split_over_two_type_actions` | still strict xfail; reason text explains where the fix lives | `parse_action()` stays stateless |
| `test_no_model_call_or_input_after_should_stop` | now asserts **no** input and **no** verification after Stop (was: the in-flight step still executed) | stricter guarantee required by the Stop-button work |

## 3. New tests

* `tests/test_safety.py` (20 tests, many subtests): terminal/registry names with
  casing and whitespace variants; destructive, code-execution and credential text;
  close-tab hotkeys in any order/case; split words over 2-3 TYPE actions with WAIT,
  MOVE, SCROLL, CLICK, SPACE or BACKSPACE in between; Enter/newline boundaries;
  the controller refusing hand-built Actions that skip `parse_action()`; dry run;
  `begin_task()`; legitimate inputs that must still pass (false-positive checks).
* `tests/test_cancellation.py` (22 tests): Stop during planning, during grounding,
  between actions, during the pause after an action, during the retry backoff and
  during a WAIT action; real-thread response-time tests; the worker race (Stop
  before the worker starts); the GUI Stop button (enabled only while running,
  STOPPING -> STOPPED, controls restored, a second task can start, Esc shortcut,
  close while running); voice capture cancel.
* `tests/test_offline_benchmark.py` (6 tests): metric formulas on synthetic records,
  totals reconcile, empty input gives no invented numbers, JSON/CSV round trip, and
  small real runs of each benchmark suite.

## 4. Mutation checks (do the new tests catch regressions?)

Each fix was undone in memory only (monkeypatch, no file edits) and the new tests
were run (`scratchpad` script `mutate_hardening.py`). All 7 mutants were caught:
Ctrl+W unblocked; TypingGuard disabled; execution-time policy check disabled;
terminal names allowed; mid-step cancellation checkpoints removed; interruptible
wait removed; worker clearing the stop flag at task start (race).

## 5. Other checks

| Check | Result | Notes |
|---|---|---|
| Byte-compile all 58 project `.py` files | PASS | in memory |
| `python main.py --help` | PASS (exit 0) | CLI options and the 3 demos unchanged |
| GUI start through the real `gui.app.main()` | PASS (exit 0) | offscreen, auto-quit after 4 s: window created, Speak/Run Task/Stop present, Stop disabled when idle, both worker threads running, preview frames received (in memory), clean close |
| Linting / type checking | NOT CONFIGURED | the project has no lint/type config and no linter installed; not added |
| `python main.py --check` | NOT RUN | it captures and saves a screenshot of the real desktop; not done unattended |
| Real dry run / real task / real voice input | NOT RUN | would send desktop screenshots to Gemini or record the microphone; needs the user present (see `PIXEL_REMAINING_BLOCKERS.md`) |
| Offline benchmark | PASS | 60 of 60 records met their expectation (`PIXEL_EVALUATION_RESULTS.md`) |
