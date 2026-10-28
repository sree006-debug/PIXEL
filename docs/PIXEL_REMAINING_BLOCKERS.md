# PIXEL - Remaining Blockers and Approval Requirements

## 1. Protocol approval (PENDING HUMAN APPROVAL)

`eval/EXPERIMENT_CHECKLIST.md` (protocol v3) reads **"Status: awaiting
approval. No real-desktop trial has been run."** Approval is a decision of the
project owner / course reviewer and has not been recorded by anyone; no tool or
script changed it. The automated tests deliberately check that this line still
says "awaiting approval", so recording an approval means editing that line and
the matching test assertion as a reviewed change.

**Exact outstanding requirement:** the approver reads the protocol and either
records the approval in the status line (with date and name) or requests changes.

### Review summary

* Protocol v3 defines setup S1-S10, resets, 17 task cells (10-cell core subset of
  30 trials), outcome labels and INVALID reasons, control trials (C-DRY, C-ERR,
  C-STOP), pilot gates PA-1..PA-7 and safety gates G-1..G-5.
* Since the last review the code changed: GAP-10 is fixed and a Stop button exists
  (section 10 rows and the change list updated). Trials must use the new commit,
  which gives a new configuration fingerprint.

### Evidence checklist for the approver

- [ ] `eval/EXPERIMENT_CHECKLIST.md` read; status line still "awaiting approval"
- [ ] `docs/PIXEL_FINAL_TEST_REPORT.md`: 626 passed, 0 failed, 1 expected failure (explained)
- [ ] `docs/PIXEL_EVALUATION_RESULTS.md`: offline benchmark, 60/60 as expected, raw records in `docs/evaluation/`
- [ ] `docs/PIXEL_CHANGE_SUMMARY.md`: what changed since commit `5960c0b`
- [ ] Decision recorded in the status line by the approver

## 2. Real-desktop evaluation (BLOCKED in this session)

Not run: it controls the real desktop, sends screenshots to the Gemini API, and
the protocol requires a human experimenter for setup, stopwatch timing and
labels. Next actions, in order, after approval:

1. Commit the harness and code (checklist S1; the scratchpad phase scripts
   prepared for this are a separate, reviewed step).
2. Setup S1-S10, PA-1 (self-test) and PA-2 by hand.
3. C-DRY x3 and C-STOP1-1 (gates G-2, G-3), now also using the GUI Stop button.
4. Pilot P-CALC-1, P-CHR-1, P-NOTE-1; then the rounds (core subset first).
5. `summarize_runs.py check` / `labels`, then fill `RESULTS_TEMPLATE.md`.

## 3. Not verified in this session

* A real GUI task, real voice input and `main.py --check` (they use the real
  desktop, microphone or Gemini). The GUI start-up itself was verified offscreen.
* Real Stop latency with the real model (bounded by `VLM_TIMEOUT`, up to 90 s).

## 4. Known limitations (accepted, documented)

* The blocklist is a safety net, not a sandbox; clipboard paste is not inspected;
  the split-word memory resets at Enter; "terminal" is blocked in any TYPE text;
  Ctrl+W is blocked even when harmless.
* Stop cannot interrupt a running model request or input action; it prevents
  every later action.
* The strict xfail `test_known_gap_blocked_word_split_over_two_type_actions`
  remains by design (stateless per-action validator); the split is blocked at
  execution.
* No linter or type checker is configured in the project.

## 5. Submission

Nothing was committed or pushed. The previous archive `PIXEL_submission.zip` is
kept unchanged; the updated archive is a separate file (see the final report).
