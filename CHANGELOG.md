# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project has not
published a tagged release yet.

## [Unreleased]

### Added
- Execution-time safety policy (`agent/safety.py`): every action is re-checked
  immediately before input, and `TypingGuard` blocks commands split over
  several TYPE actions.
- Stop button (and Esc) with cooperative cancellation: no new action is sent
  after Stop.
- Live execution timeline, activity log and per-task completion report in the GUI
  (`gui/report.py`).
- Acknowledgement popup shown once at startup (`gui/acknowledgement.py`).
- Provider selector in the GUI.
- Offline control-logic benchmark (`eval/offline_benchmark.py`, results in
  `docs/evaluation/`) and a prepared real-desktop evaluation protocol and
  harness in `eval/` (not yet executed).
- Open-source release files: `LICENSE` (MIT), `CONTRIBUTING.md`, `SECURITY.md`,
  this changelog, and `docs/TECHNICAL_REFERENCE.md`.

### Changed
- Extended blocklists for TYPE text and hotkeys (terminal names, Ctrl+W,
  Ctrl+F4, Ctrl+Shift+W, names written with spaces); rejections name the rule.
- Redesigned PIXEL GUI.
- README rewritten for the public release; `.gitignore` extended.

### Fixed
- `format c:` was never matched by the blocklist.
- A race in the agent worker that could lose an early Stop.

## 2026-10-08
- Visual grounding: block repeated grounded click locations within a failure
  streak (`5960c0b`).
- Grounding context is added to failed-click replans (`3fbb3c4`).
- Grounded clicks require a non-empty expected outcome (`6f18060`).

## 2026-10-02
- Agent-level visual grounding for semantic clicks (`f274801`).
- PIXEL GUI integrated with voice input and agent execution (`8c80485`).

## 2026-10-01
- Local voice input with faster-whisper transcription (`4354531`).

## 2026-09-30
- Robust closed-loop computer-use agent: structured actions, validation,
  verification, failure classification, retries and replanning (`886e53a`).
