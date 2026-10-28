"""Per-run logging: runs/<timestamp>_<task>/ with log.jsonl, screenshots, summary.json.

Only plain task/step data is written. API keys never pass through here.
"""

import json
import re
from datetime import datetime
from pathlib import Path


class RunLog:
    def __init__(self, runs_dir: Path, task: str, echo: bool = True):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = re.sub(r"[^a-z0-9]+", "_", task.lower()).strip("_")[:40] or "task"
        self.dir = Path(runs_dir) / f"{stamp}_{slug}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.echo = echo

    def save_screenshot(self, step: int, image, label: str = ""):
        name = f"step_{step:02d}{'_' + label if label else ''}.png"
        image.save(self.dir / name)
        return name

    def step(self, entry: dict):
        entry = {"timestamp": datetime.now().isoformat(timespec="seconds"), **entry}
        with open(self.dir / "log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        if self.echo:
            self._print(entry)

    def summary(self, data: dict):
        (self.dir / "summary.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        if self.echo:
            print(f"\n=== {data['status'].upper()}: {data['reason']}")
            print(f"    steps: {data['steps']}   log: {self.dir}")

    @staticmethod
    def _print(e):
        """Compact terminal view; the full entry is in log.jsonl."""
        def short(value, limit=220):
            text = str(value)
            return text if len(text) <= limit else text[:limit] + "..."

        print(f"\n[step {e['step']}] {e['timestamp']}  model={e.get('model', '')}  "
              f"VLM {e.get('vlm_latency_s', '-')}s")
        if e.get("observation"):
            print(f"  observation:  {short(e['observation'], 160)}")
        print(f"  proposed:     {e.get('action_text') or short(e.get('proposed_action'))}")
        if e.get("expected_outcome"):
            print(f"  expected:     {short(e['expected_outcome'], 160)}")
        if e.get("grounding"):
            g = e["grounding"]
            click = g.get("action") or {}
            at = f" -> click ({click['x']}, {click['y']})" if click else ""
            print(f"  grounding:    {g.get('status')} {g.get('target')!r} bbox={g.get('bbox')} "
                  f"confidence={g.get('confidence')}{at}")
        print(f"  validation:   {short(e.get('validation'))}")
        print(f"  execution:    {short(e.get('execution'))}")
        print(f"  verification: {short(e.get('verification'))}")
        if e.get("deterministic_check"):
            print(f"  file check:   {short(e['deterministic_check'])}")
        if e.get("failure_type"):
            repeated = (f", same action failed {e['repeated_failures']}x in a row"
                        if e.get("repeated_failures") else "")
            print(f"  -> {e['failure_type']}{repeated}: {short(e.get('failure'))}")
        print(f"  retries:      {e.get('retries')} -> {e.get('retries_after')}"
              f" (max {e.get('max_retries', '?')})")
