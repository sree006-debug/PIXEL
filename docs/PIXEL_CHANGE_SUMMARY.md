# PIXEL - Change Summary (2026-10-22)

Base: commit `5960c0b` (nothing committed by this work). Rollback copy of the
previous state: `PIXEL_submission.zip` (kept unchanged).

## Safety hardening (GAP-10 and defense in depth)

* `agent/actions.py`: blocklists extended and made explicit by category
  (shell/terminal, registry/system tool, destructive command, code execution,
  credential); "wt" blocked when typed on its own; long names written with spaces
  caught; Ctrl+W, Ctrl+F4, Ctrl+Shift+W blocked; fixed a pre-existing bug where
  `format c:` was never matched; rejections name the rule. New helpers
  `blocked_text_reason()`, `blocked_hotkey()`.
* `agent/safety.py` (new): `policy_problem()` re-checks a single Action;
  `TypingGuard` refuses a TYPE that completes a blocked word with earlier typed
  text (until Enter/newline); `PolicyViolation`.
* `agent/controller.py`: checks the policy and the TypingGuard immediately before
  every action, also in dry run, for every caller; `begin_task()`; optional
  interruptible WAIT.
* `agent/loop.py`: a `PolicyViolation` at execution is logged as
  `ACTION_REJECTED` (nothing performed); the guard is reset per task.

## Stop button and cooperative cancellation

* `agent/loop.py`: stop checkpoints after the model decision, before execution
  and after execution (verification skipped), plus interruptible pauses
  (`wait`); `cancelled` recorded in the step log.
* `gui/agent_worker.py`: `prepare_task()` (fixes a race that could lose an early
  Stop); passes the interruptible wait to the agent and controller.
* `gui/voice_worker.py`: `prepare()`/`cancel()`/`cancelled` - Stop discards a
  voice capture.
* `gui/app.py`: Stop button (and Esc) enabled only while busy; STOPPING ->
  STOPPED states; controls restored; closing the window also stops safely.

## Evaluation

* `eval/offline_benchmark.py` (new): reproducible offline benchmark with raw
  records and tested metric calculations; results in `docs/evaluation/`.
* `eval/EXPERIMENT_CHECKLIST.md`: section 10 rows updated (GAP-10 fixed, new Stop
  behaviour, offline benchmark) and a change-list entry. **Status line unchanged.**

## Tests

* New: `tests/test_safety.py`, `tests/test_cancellation.py`,
  `tests/test_offline_benchmark.py`.
* `tests/test_eval_failure_paths.py`: 6 fixed strict xfails became normal tests,
  1 new split-word execution test, the split-word parser xfail kept with an
  updated reason, and the Stop characterisation test now asserts the stricter
  behaviour.

## Documentation

`README.md` (Stop button, safety policy, tests, evaluation status,
troubleshooting) and `docs/` (this file, test report, evaluation protocol and
results, remaining blockers).
