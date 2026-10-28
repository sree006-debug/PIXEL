# Security Policy

PIXEL sends screenshots of your desktop to a cloud vision-language model and
controls the real mouse and keyboard. Security reports are taken seriously.

## Supported versions

Only the latest version on the default branch receives security fixes.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Report it privately through GitHub's **private vulnerability reporting**:
open the repository's **Security** tab and choose **Report a vulnerability**.
If that option is not available, open a public issue that only asks the
maintainer for a private contact channel, without any technical details.

Please include:

* a description of the issue and its impact;
* steps to reproduce (a task text, model reply or action sequence);
* the affected file or function, if known;
* your Windows and Python versions.

Remove API keys, screenshots and personal data from anything you send.

You can expect an acknowledgement within a reasonable time. Please allow time
for a fix before any public disclosure.

## In scope

* Bypasses of the action validator (`agent/actions.py`) or the execution-time
  safety policy (`agent/safety.py`, `agent/controller.py`), e.g. getting a
  blocked hotkey or command executed.
* Any path by which model output could be executed as code.
* Actions sent after **Stop** was requested.
* Leaks of API keys or screenshots into logs, reports or the repository.

## Out of scope

* The documented limitations in the README (for example, the blocklist is a
  safety net, not a sandbox; clipboard paste content is not inspected).
* Issues in third-party dependencies; please report those upstream.

## Safe use

* Keep your `GEMINI_API_KEY` only in `.env` (ignored by Git) or in an
  environment variable. If a key is ever exposed, revoke it in Google AI Studio
  and create a new one.
* Close private windows before running live tasks: every step sends a
  screenshot of the primary screen to the model provider.
* The emergency stop is always available: move the mouse into a screen corner.
