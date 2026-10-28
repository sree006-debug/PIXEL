# Contributing to PIXEL

Thank you for your interest in improving PIXEL. This project controls a real mouse
and keyboard, so changes are held to a high bar for safety and test coverage.

## Development setup (Windows)

```powershell
git clone <your-fork-url>
cd <repository-folder>
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy .env.example .env      # add your own GEMINI_API_KEY; never commit .env
python main.py --check
```

## Checking changes

The automated test suite is not included in this public repository. Before
opening a pull request, run the offline benchmark, which uses a scripted model
and mocked input only:

```powershell
.\.venv\Scripts\python.exe eval\offline_benchmark.py --out docs\evaluation
```

The scripts in `tools/` use the real screen and send a real VLM request. Run
them only on purpose, and close private windows first.

## Coding guidelines

* Keep modules small and focused, and match the style of the surrounding code.
* Put tunable values in `config.py`, not inside modules.
* **Only `agent/controller.py` may send mouse or keyboard input.** Other modules
  propose actions; they never execute them.
* **Never execute model output as code.** The model may only choose one of the
  validated actions in `agent/actions.py`.
* Every new action, parameter or safety rule must go through `parse_action()`
  and the execution-time policy in `agent/safety.py`, with tests for both
  allowed and blocked cases.
* Provider SDKs are imported only in their own `agent/<name>_provider.py` file
  (see `docs/TECHNICAL_REFERENCE.md`, *Adding a VLM provider*).
* Do not weaken the PyAutoGUI failsafe or the Stop behaviour.
* Never commit secrets, `.env`, or anything from `runs/` or `scratch/`; those
  contain API keys or raw screenshots of your desktop.

## Pull requests

1. Create a branch from the default branch with a short descriptive name.
2. Keep each pull request focused on one change.
3. Add or update tests for any behaviour change.
4. Run the full test suite and include the result summary in the PR description.
5. Update `README.md`, `docs/TECHNICAL_REFERENCE.md` and `CHANGELOG.md` when
   behaviour, setup or safety rules change.
6. Describe how you tested the change, and say clearly whether you ran it on a
   real desktop or only with the mocked tests.

## Reporting bugs and security issues

Open a GitHub issue for ordinary bugs. Include your Windows version, Python
version, display scaling, the command you ran and the relevant lines from the
run log. **Remove screenshots, file paths and personal data before posting.**

For security problems, such as a way to bypass the safety policy, follow
[SECURITY.md](SECURITY.md) instead of opening a public issue.
