# PIXEL - Evaluation Results

Three levels, reported separately (definitions: `PIXEL_EVALUATION_PROTOCOL.md`).

## A. Automated tests

626 passed, 0 failed, 0 skipped, 1 expected failure, 1 warning, 380 subtests
(details: `PIXEL_FINAL_TEST_REPORT.md`). Mocked input, no network.

## B. Offline control-logic benchmark (executed)

Run: 2026-10-22T11:40Z, `eval/offline_benchmark.py --repetitions 5`, 25 s.
Environment: Windows-11-10.0.26200, Python 3.14.7, pyautogui 0.9.54, google-genai
2.25.0, PySide6 6.11.2, git `5960c0b` + uncommitted changes (recorded in the JSON).
Model: **scripted** (no real VLM). Input: **mocked** (no real input).
Raw records: `evaluation/offline_benchmark_records.csv` (60 rows) and
`evaluation/offline_benchmark_results.json` (records + metrics + environment).

**60 trials recorded, 60 met their expectation, 0 excluded.**

### Safety

| Measure | Result |
|---|---|
| Dangerous cases (S01-S20) | 20 |
| Safety violations (dangerous step performed) | **0** |
| Blocked rate | 100.0 % |
| Legitimate cases (L01-L10) | 10 |
| False-positive blocks | **0** |

### Cancellation (Stop)

| Phase | Trials | Max response time (s) |
|---|---|---|
| between actions (1 s pause) | 5 | 0.021 |
| retry backoff (3 s) | 5 | 0.030 |
| WAIT action (5 s) | 5 | 0.019 |
| model request (0.6 s, not interruptible) | 5 | 0.511 |
| **All** | **20** (20 stopped correctly) | median 0.019, max 0.511 |

Actions sent after Stop: **0**. The model-request phase is bounded by the request
itself: Stop takes effect when it returns, and its decision is not executed.

### Recovery (scripted scenarios)

| Measure | Result |
|---|---|
| Scenarios ended as expected | 10 / 10 |
| Task success rate (7 scenarios expected to succeed) | 100.0 % |
| Recovery success rate (6 scenarios that needed recovery) | 100.0 % |
| Retries per task (mean) | 1.3 |
| Actions per task (mean) | 1.8 |

### Failure category distribution (all 60 trials)

| Failure type | Count |
|---|---|
| ACTION_REJECTED | 30 |
| OUTCOME_NOT_CONFIRMED | 5 |
| CONFIRMATION_NEEDED | 1 |
| FINISH_NOT_VERIFIED | 1 |
| GROUNDING_FAILED | 1 |
| NO_DECISION | 1 |

(ACTION_REJECTED is high by design: most safety trials end with a blocked action.)

**Interpretation limits:** these numbers show that the control logic behaves as
specified for these fixed cases. They are not task success rates of the real
agent and say nothing about the model's accuracy.

## C. Real-desktop trials (not executed)

**No controlled real-desktop trial has been run.** Protocol v3
(`eval/EXPERIMENT_CHECKLIST.md`) is awaiting approval, and its trials require a
human experimenter (setup, stopwatch, human labels) and send desktop screenshots
to the Gemini API. No success rate, completion time, grounding accuracy or
verification accuracy is reported for the real agent.

### For context only: historical development runs (uncontrolled)

`runs/` contains 29 runs made during development (2026-09-24 to 2026-10-08), all
before the safety hardening and with several code versions: **14 success,
13 failed, 2 interrupted without a summary** (counted from `summary.json` files;
screenshots not opened). They had no fixed task list, setup or labelling, so they
are not evidence of a success rate and are not used in any metric above.
