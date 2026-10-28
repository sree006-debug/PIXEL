# PIXEL — Technical Reference

This document contains the detailed behaviour of the agent. For installation and a
project overview, see the [README](../README.md).

## Project layout

| File | Role |
|---|---|
| `main.py` | Command-line entry point (`--check`, tasks, demos, deterministic checks) |
| `config.py` | All settings (steps, retries, timing, validation limits, grounding thresholds) |
| `agent/screen.py` | DPI awareness, screenshots, coordinate conversion |
| `agent/actions.py` | Action format + strict validation (`parse_action`) |
| `agent/controller.py` | The **only** code that moves the mouse / presses keys; re-checks the safety policy right before every action; dry-run mode |
| `agent/safety.py` | Execution-time safety policy: blocklists again + `TypingGuard` against commands split over several TYPE actions |
| `agent/vlm_types.py` | Provider-neutral types: `VLMRequest`, `Decision`, `Verification`, grounding types, errors |
| `agent/prompts.py` | Provider-neutral prompts and JSON schemas |
| `agent/vlm.py` | `VLMProvider` base class, `create_provider()`, response parsing, `decision_to_action()` |
| `agent/gemini_provider.py` | Gemini implementation (the only file that imports the Google SDK) |
| `agent/grounding.py` | Visual grounding: locate one described element, propose a validated CLICK (never executes) |
| `agent/loop.py` | Closed-loop agent: decide -> validate -> act -> observe -> verify, failure feedback, progress events |
| `agent/verifier.py` | Visual check (`vlm_check`) and deterministic file checks (`file_contains`) |
| `agent/run_log.py` | Per-run folder with `log.jsonl`, screenshots, `summary.json`; compact terminal log |
| `agent/demos.py` | Named demo tasks (calculator, chrome, notepad) and their FINISH checks |
| `gui/app.py` | PIXEL main window (PySide6): live desktop view, execution timeline, activity log |
| `gui/agent_worker.py` | Runs the same agent stack as `main.py` on a background thread for **Run Task** |
| `gui/voice_worker.py` | Records and transcribes speech on a background thread |
| `gui/screen_preview.py` | Live desktop preview |
| `gui/report.py` | Task completion report built only from one run's recorded data |
| `gui/acknowledgement.py` | Acknowledgement popup shown once at startup |
| `voice/recorder.py` | Microphone recording to WAV (16 kHz mono, `sounddevice`) |
| `voice/transcriber.py` | Local speech-to-text with `faster-whisper` |
| `tools/ground_test.py` | Manual visual-grounding check for one target (shows the proposed click; executes only with `--execute`) |
| `tools/test_gemini_direct.py` | Diagnostic for Gemini request latency (sends a real request) |
| `eval/` | Real-desktop evaluation protocol and harness (prepared, **not yet executed**) and `offline_benchmark.py` |
| `docs/` | Test report, evaluation protocol/results, change summary, remaining blockers; `docs/evaluation/` holds the raw offline benchmark records |

## Command-line usage

```powershell
python main.py --check                          # environment check, sends no VLM request
python main.py "Open Calculator" --dry-run      # 1 step: screenshot -> VLM -> validate -> log, NO input
python main.py "Open Calculator"                # real closed loop (mouse/keyboard!)
python main.py "Open Calculator" --max-steps 8 --no-verify-steps   # only FINISH is verified
python main.py "Save ... as scratch\robotics.txt" --expect-file scratch\robotics.txt --expect-text "Autonomous Robotics"
```

`python main.py --check` reports the environment, screen size/scaling (screenshot
and mouse coordinates must agree), saves a test screenshot to
`runs/check_screenshot.png`, and shows the configured VLM.

Each run writes `runs/<timestamp>_<task>/`: `log.jsonl` (one line per step with
timestamp, model, VLM latency, observation, proposed action, expected outcome,
validation, execution, verification, failure type, retries), a screenshot per
step, and `summary.json`. A real run starts after a 3-second countdown.

### Demo tasks

```powershell
python main.py --demo calculator   # "Open Calculator and calculate 25 multiplied by 16."  FINISH must show 400
python main.py --demo chrome       # "Open Chrome and search for ROS2 Humble."  FINISH must show search results
python main.py --demo notepad      # Notepad: 3 robotics technologies -> Desktop\robotics_list.txt (file-checked)
```

Demos only define the task sentence and how FINISH is checked; the VLM finds
every button/field from the screenshot (no coordinates are stored anywhere).
The same checks are available for any task: `--expect-screen "<what must be
visible>"`, `--expect-file PATH [--expect-text TEXT] [--expect-lines N]`.

## GUI behaviour

* **Run Task** (or Enter) runs the typed task with the **same** agent stack as
  `main.py` (`create_provider -> Controller -> RunLog -> Agent`), so validation,
  retry limits, verification, logging to `runs/` and the failsafe are identical.
  One task at a time; the agent runs on a background thread.
* **Execution timeline** shows the stages of the current step (observing,
  planning, validating, executing, verifying, outcome).
* **Completion report** ("Last report") is built only from the run's recorded
  data. A task counts as *Verified* only when its final FINISH check passed;
  missing fields are shown as "Not available".
* **Live Desktop View** is captured locally; the preview itself is not sent to the VLM.
* **Stop** (button or **Esc**): the agent sends **no new action** after Stop. A
  model request or a mouse/keyboard action that is already running is not
  interrupted; the stop takes effect as soon as it returns (status STOPPING ->
  STOPPED). The run log records `status: aborted`, `reason: stopped by user`.
  During voice input, Stop discards the recording/transcript.
* **Closing the window** while a task runs also requests a stop. For an
  immediate hard stop, move the mouse into a screen corner (PyAutoGUI failsafe).

## Voice input

The microphone button records **5 seconds** from the default microphone (16 kHz
mono WAV in `scratch/voice/`, ignored by Git), transcribes it locally with
`faster-whisper` (model `base`, CPU, `int8`; downloaded on first use if not
cached), and puts the text into the command box. Audio is not sent to the VLM
provider. Review the transcript before pressing **Run Task**.

## Closed-loop execution

`agent/loop.py`, per step: screenshot -> `decide_action` -> `decision_to_action`
(-> `parse_action`) -> `Controller.execute` -> wait (`ACTION_DELAY`) -> screenshot ->
`verify_state` on the action's `expected_outcome`. Only a PASS resets the retry
counter. Defaults in `config.py`: `MAX_STEPS` 25, `MAX_RETRIES` 3,
`VERIFY_MIN_CONFIDENCE` 0.5, `HISTORY_LENGTH` 8 previous steps shown to the VLM.

The model can only propose one of 11 actions: CLICK, DOUBLE_CLICK, TYPE, PRESS,
HOTKEY, SCROLL, WAIT, MOVE, DRAG, FINISH, RECOVER. It never runs code.

## Action validation and safety policy

`parse_action()` accepts only the 11 actions, with required fields and limits
from `config.py`: TYPE at most 200 characters (plain ASCII), SCROLL at most 15
notches, WAIT 0.1-10 s, MOVE/DRAG at most 2 s, no target within 5 px of a screen
corner. A rejected action is never executed.

* **Blocked hotkeys:** Win+L, Win+R, Win+X, Ctrl+Alt+Del, Ctrl+Shift+Esc, Alt+F4,
  and closing tabs/windows (Ctrl+W, Ctrl+F4, Ctrl+Shift+W). Order and case do not matter.
* **Blocked TYPE text** (case-insensitive; the rejection names the rule):
  shells and terminals (cmd, PowerShell, pwsh, bash, wsl, "terminal", "Windows
  Terminal", "Command Prompt", and "wt" typed on its own), registry/system tools
  (regedit, "Registry Editor", reg add/delete, diskpart, bcdedit, msconfig, ...),
  destructive commands (shutdown, taskkill, rmdir, rd /s, del /, del with
  wildcards, format X:, rm -rf, Remove-Item, ...), code execution
  (Invoke-Expression/iex, mshta, wscript, certutil, rundll32, `python -c`, piping
  into a shell, ...) and credential-shaped text (API keys, private keys).
* **Execution-time check (defense in depth):** `Controller.execute()` checks the
  policy again immediately before any input, and its `TypingGuard` refuses a
  TYPE that would complete a blocked word together with the text typed since the
  last Enter (e.g. "power" then "shell"). A refusal raises `PolicyViolation`,
  nothing is performed, and the step is logged as `ACTION_REJECTED`.

Limits of the blocklist approach: it is a safety net, not a sandbox; text pasted
from the clipboard is not inspected; the split-word memory resets at Enter; the
word "terminal" is blocked in any TYPE text; closing tabs with Ctrl+W is not
possible even when it would be harmless.

## Visual grounding

A CLICK may give a `target` description instead of x, y. Then
`grounding.locate()` asks the VLM for a bounding box of that ONE element on the
same screenshot, and `grounding.propose_click()` turns it into a CLICK only if
the confidence is at least `GROUNDING_MIN_CONFIDENCE` (0.6) and the box is small
enough to point at a single element (each side at most 50 % of the screen, area at
most 5 %). A location where a grounded click already failed is not reused in the
same failure streak. `grounding.py` never moves the mouse itself.

## Verification

* **Per step (visual):** the VLM answers with `reason`, `success`, `confidence`
  and `confirmation_needed`. A "yes" below `VERIFY_MIN_CONFIDENCE` counts as no.
  If the check itself fails (API error), the step is not treated as a success.
* **FINISH is only a claim.** It succeeds only if a visual check of the whole
  task passes (plus `--expect-screen` if given) **and** the deterministic check
  passes when one is configured.
* **Deterministic file check** (`--expect-file`): the file must exist
  (OneDrive-redirected Desktop is handled), must have been saved during this run,
  and must contain `--expect-text` / at least `--expect-lines` non-empty lines.

## Recovery / replanning

| Type | Meaning | Uses a retry? |
|---|---|---|
| `ACTION_REJECTED` | validator refused the action; nothing executed | yes |
| `GROUNDING_FAILED` | a CLICK target could not be located safely; nothing executed | yes |
| `NO_DECISION` | VLM error, timeout or unusable JSON | yes |
| `EXECUTION_ERROR` | the controller raised an error | yes |
| `OUTCOME_NOT_CONFIRMED` | executed, but the expected outcome isn't visible | yes |
| `CONFIRMATION_NEEDED` | e.g. "Confirm Save As - replace?" appeared | **no** |
| `FINISH_NOT_VERIFIED` | FINISH claimed, visual or file check failed | yes |

If the same action fails again in a row, the model is told to choose a different
action. More than `MAX_RETRIES` consecutive failures, or `MAX_STEPS` steps, end
the task as failed. After a failure the agent waits `VLM_RETRY_BACKOFF` x
retries seconds (at most 10 s) before the next request.

## Troubleshooting

| Problem | What to do |
|---|---|
| `VLM not configured` / `key=missing` | Copy `.env.example` to `.env`, set `GEMINI_API_KEY`, run `python main.py --check` |
| `Activate.ps1` cannot be loaded | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, or call `.\.venv\Scripts\python.exe` directly |
| Clicks land in the wrong place | `--check` must show the same screenshot and mouse size; use one monitor |
| A task ends with "Emergency stop" | The mouse touched a screen corner (failsafe) |
| A step says `ACTION_REJECTED ... blocked` | The safety policy refused the action; nothing was executed |
| Stop stays on STOPPING for a while | A model request is still running (up to `VLM_TIMEOUT`) |
| "Microphone recording failed" | Check the default input device and Windows microphone privacy settings |
| The first voice input is slow | faster-whisper downloads/loads the `base` model on first use |

## Adding a VLM provider

The rest of the app only uses `create_provider()`, `provider.decide_action()`,
`provider.verify_state()` and `decision_to_action()`.

1. Create `agent/<name>_provider.py`:
   ```python
   from agent.vlm import VLMProvider
   from agent.vlm_types import VLMAPIError, VLMTimeoutError, VLMResponseError

   class MyProvider(VLMProvider):
       name = "myprovider"
       API_KEY_ENV = "MYPROVIDER_API_KEY"
       DEFAULT_MODEL = "some-model"

       def __init__(self, api_key, model, timeout):
           super().__init__(model, timeout)
           ...  # create the SDK client here

       def _generate(self, system_prompt, user_text, image, schema) -> str:
           # send system_prompt + user_text + image (a PIL Image), ask for JSON
           # matching `schema`, return the reply text. Convert SDK exceptions to
           # VLMAPIError / VLMTimeoutError / VLMResponseError.
           ...
   ```
2. Register it in `PROVIDERS` in `agent/vlm.py`:
   `"myprovider": "agent.myprovider_provider:MyProvider",`
3. Add `MYPROVIDER_API_KEY=` to `.env.example`, install its SDK, set
   `VLM_PROVIDER=myprovider` in `.env`.

Prompts, the JSON contract, the 0-1000 coordinate grid, validation, grounding and
the controller are shared, so nothing else changes.
