"""Entry point.

    python main.py --check                      verify the environment
    python main.py "Open Calculator" --dry-run  decide + validate, no input
    python main.py "Open Calculator"            run the closed-loop agent for real
    python main.py --demo calculator            run a predefined demo (calculator/chrome/notepad)
"""

import argparse
import sys
from importlib.metadata import version

from agent import screen

# Must happen before anything touches the screen or mouse.
screen.enable_dpi_awareness()

import config  # noqa: E402  (imported after DPI setup on purpose)
from agent.demos import DEMOS  # noqa: E402


def run_check():
    ok = True

    def report(label, value, good=True):
        nonlocal ok
        ok = ok and good
        print(f"  [{'OK' if good else '!!'}] {label:22s} {value}")

    print("Environment check")
    in_venv = sys.prefix != sys.base_prefix
    report("Python", f"{sys.version.split()[0]}  ({sys.executable})")
    report("Virtual env", sys.prefix if in_venv else "NOT ACTIVE", in_venv)

    for pkg in ["pyautogui", "pillow", "python-dotenv", "google-genai"]:
        try:
            report(pkg, version(pkg))
        except Exception:
            report(pkg, "MISSING", False)

    print("\nScreen")
    report("DPI awareness", screen.enable_dpi_awareness())
    report("Display scaling", f"{screen.get_scale_percent()}%")
    mouse_size = tuple(screen.get_screen_size())
    shot = screen.take_screenshot()
    report("Mouse coordinate space", f"{mouse_size[0]}x{mouse_size[1]}")
    report("Screenshot size", f"{shot.width}x{shot.height}",
           shot.size == mouse_size)
    if shot.size != mouse_size:
        print("       Screenshot and mouse sizes differ -> clicks would miss!")

    print("\nPaths")
    desktop = screen.get_desktop_path()
    report("Desktop folder", desktop)
    config.RUNS_DIR.mkdir(exist_ok=True)
    shot.save(config.RUNS_DIR / "check_screenshot.png")
    report("Test screenshot", config.RUNS_DIR / "check_screenshot.png")

    print("\nVLM (no request is sent here)")
    from agent.vlm import create_provider
    from agent.vlm_types import VLMConfigError
    try:
        provider = create_provider()
        report("Provider", f"{provider.name}  model={provider.model}  key=set")
    except VLMConfigError as e:
        report("Provider", f"not configured: {e}", False)

    print("\nAll checks passed." if ok else "\nSome checks FAILED.")
    return ok


def run_task(args):
    import time

    from agent import verifier
    from agent.controller import Controller
    from agent.loop import Agent
    from agent.run_log import RunLog
    from agent.vlm import create_provider
    from agent.vlm_types import VLMConfigError

    try:
        provider = create_provider()
    except VLMConfigError as e:
        print(f"VLM not configured: {e}")
        return 2

    dry_run = args.dry_run or config.DRY_RUN
    # In dry run the screen never changes, so more steps would only repeat the same call.
    max_steps = args.max_steps or (1 if dry_run else config.MAX_STEPS)
    final_check = None
    if args.expect_file:
        # The file must be saved during this run, so a leftover copy can't pass.
        final_check = verifier.file_contains(args.expect_file, args.expect_text or "",
                                             min_lines=args.expect_lines or 0,
                                             modified_after=time.time())

    config.SCRATCH_DIR.mkdir(exist_ok=True)
    run_log = RunLog(config.RUNS_DIR, args.task)
    agent = Agent(provider, Controller(dry_run=dry_run), run_log, final_check=final_check,
                  max_steps=max_steps, verify_steps=not args.no_verify_steps,
                  finish_criteria=args.expect_screen or "")

    print(f"Task:     {args.task}")
    print(f"Model:    {provider.name} / {provider.model}")
    if args.expect_screen:
        print(f"Screen:   FINISH must show {args.expect_screen}")
    if final_check:
        extra = f", >= {args.expect_lines} lines" if args.expect_lines else ""
        text = f", contains {args.expect_text!r}" if args.expect_text else ""
        print(f"File:     {final_check.path} (saved during this run{text}{extra})")
    print(f"Mode:     {'DRY RUN (no mouse/keyboard input)' if dry_run else 'REAL INPUT'}"
          f"   max_steps={max_steps}  max_retries={config.MAX_RETRIES}")
    print("Emergency stop: move the mouse into a screen corner, or press Ctrl+C here.")
    print("Starting in 3 seconds...")
    time.sleep(3)

    try:
        state = agent.run(args.task)
    except KeyboardInterrupt:
        print("\nStopped by Ctrl+C.")
        return 130
    return 0 if state.status == "success" else 1


def main():
    parser = argparse.ArgumentParser(description="Self-operating computer agent")
    parser.add_argument("task", nargs="?", help='e.g. "Open Calculator"')
    parser.add_argument("--check", action="store_true", help="verify the environment")
    parser.add_argument("--dry-run", action="store_true",
                        help="get and validate VLM decisions but never move the mouse or type")
    parser.add_argument("--max-steps", type=int, help=f"default {config.MAX_STEPS} (1 in dry run)")
    parser.add_argument("--no-verify-steps", action="store_true",
                        help="only verify FINISH, not every step (halves VLM calls)")
    parser.add_argument("--expect-file", help="deterministic check for FINISH: this file must exist")
    parser.add_argument("--expect-text", help="...and contain this text")
    parser.add_argument("--expect-lines", type=int,
                        help="...and have at least this many non-empty lines")
    parser.add_argument("--expect-screen",
                        help="what the final screenshot must show for FINISH to count")
    parser.add_argument("--demo", choices=sorted(DEMOS),
                        help="run a predefined demo task with its checks (see agent/demos.py)")
    args = parser.parse_args()

    if args.demo:
        demo = DEMOS[args.demo]
        # Explicit command-line values win over the demo's defaults.
        args.task = args.task or demo.task
        args.expect_screen = args.expect_screen or demo.expect_screen or None
        args.expect_file = args.expect_file or demo.expect_file or None
        args.expect_text = args.expect_text or demo.expect_text or None
        args.expect_lines = args.expect_lines or demo.expect_lines or None

    if args.check:
        sys.exit(0 if run_check() else 1)
    if args.task:
        sys.exit(run_task(args))
    parser.print_help()


if __name__ == "__main__":
    main()
