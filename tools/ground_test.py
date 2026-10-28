"""Manual visual-grounding test: where is a UI target on the screen?

    .venv\\Scripts\\python tools\\ground_test.py "Calculator"
    .venv\\Scripts\\python tools\\ground_test.py "the Save button" --context "Save the file in Notepad"
    .venv\\Scripts\\python tools\\ground_test.py "Chrome" --image runs\\check_screenshot.png

Takes ONE screenshot (or loads --image), sends ONE grounding request to the
configured VLM provider and prints the GroundingResult. Saves a copy of the
screenshot with the box drawn on it to runs/grounding/.

By default it never moves the mouse or types: it shows the CLICK that WOULD be
proposed (confidence, box-size and the normal action validation via
propose_click()) but does not execute it.

    .venv\\Scripts\\python tools\\ground_test.py "the 7 button on the Calculator keypad" --execute

With --execute (live screenshot only, not with --image) a proposal that passed
every check is executed ONCE through Controller.execute(), the same path the
agent uses (config.DRY_RUN still applies). A rejected proposal is never executed.
This file never imports pyautogui; the controller is only imported for --execute.
Sends the screenshot to the provider, so close anything private first.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from agent import screen  # noqa: E402

# Must happen before the screenshot, or it is taken in scaled (logical) pixels.
screen.enable_dpi_awareness()

import config  # noqa: E402
from agent.grounding import bbox_to_pixels, locate, propose_click  # noqa: E402
from agent.vlm import create_provider  # noqa: E402
from agent.vlm_types import VLMConfigError  # noqa: E402

BOX_COLORS = {"usable": "lime", "low_confidence": "orange"}


def save_annotated(image, result, out_dir):
    """Draw the box (if any) on a copy of the screenshot and save it."""
    from PIL import ImageDraw
    annotated = image.convert("RGB")
    if result.bbox is not None:
        draw = ImageDraw.Draw(annotated)
        box = bbox_to_pixels(result.bbox, annotated.size)
        color = BOX_COLORS.get(result.status, "red")
        draw.rectangle(box, outline=color, width=4)
        cx, cy = result.center
        px, py = screen.normalized_to_pixels(cx, cy, annotated.size)
        draw.ellipse((px - 6, py - 6, px + 6, py + 6), outline=color, width=3)
        label = f"{result.target} {result.confidence:.2f} ({result.status})"
        draw.text((box[0], max(box[1] - 14, 0)), label, fill=color)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "_", result.target.lower()).strip("_")[:40] or "target"
    path = out_dir / f"{stamp}_{slug}.png"
    annotated.save(path)
    return path


def make_controller():
    """The one place --execute gets its Controller (imported lazily, so the
    default mode never loads agent.controller or pyautogui)."""
    from agent.controller import Controller
    return Controller()


def execute_proposal(proposal, controller) -> int:
    """Execute ONE validated proposal through Controller.execute(). Returns the exit code."""
    if not proposal.ok or proposal.action is None:
        print(f"NOT executed: no valid proposal ({proposal.status})")
        return 1
    from agent.controller import EmergencyStop
    try:
        line = controller.execute(proposal.action)
    except EmergencyStop as e:
        print(f"Execution stopped: {e}")
        return 3
    print(f"Executed: {line}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Locate one UI target (no input is sent unless --execute is given)")
    parser.add_argument("target", help='e.g. "Calculator"')
    parser.add_argument("--context", default="", help="optional task context")
    parser.add_argument("--image", help="use this image instead of a live screenshot")
    parser.add_argument("--delay", type=float, default=3.0,
                        help="seconds to wait before the live screenshot (default 3)")
    parser.add_argument("--execute", action="store_true",
                        help="execute the proposed click ONCE via Controller.execute() "
                             "(only if every check passed; live screenshot only)")
    args = parser.parse_args(argv)

    if args.execute and args.image:
        # Coordinates from a saved image need not match what is on screen now.
        parser.error("--execute needs a live screenshot; it cannot be combined with --image")

    try:
        provider = create_provider()
    except VLMConfigError as e:
        print(f"VLM not configured: {e}")
        return 2

    if args.image:
        from PIL import Image
        image = Image.open(args.image)
        screen_size = image.size
    else:
        print(f"Taking a screenshot in {args.delay:.0f} s (switch to the window you want)...")
        time.sleep(args.delay)
        image = screen.take_screenshot()
        screen_size = screen.get_screen_size()

    print(f"Model:   {provider.name} / {provider.model}")
    print(f"Target:  {args.target}")
    print(f"Image:   {image.width}x{image.height}   threshold={config.GROUNDING_MIN_CONFIDENCE}")
    print(f"Mode:    {'EXECUTE (one click if valid)' if args.execute else 'propose only'}")
    t0 = time.perf_counter()
    result = locate(provider, image, args.target, args.context)
    print(f"Latency: {time.perf_counter() - t0:.1f} s")
    print(json.dumps(result.to_dict(), indent=2))

    if result.bbox is not None:
        print(f"Box in pixels: {bbox_to_pixels(result.bbox, screen_size)}")
    proposal = propose_click(result, screen_size, reason=f"click {args.target}")
    if proposal.ok:
        action = proposal.action
        executed = "" if args.execute else " (NOT executed)"
        print(f"Proposed{executed}: {action.action} at ({action.x}, {action.y})"
              f"  grid centre {proposal.point}")
    else:
        print(f"No click proposed ({proposal.status}): {proposal.error}")

    print(f"Annotated: {save_annotated(image, result, config.RUNS_DIR / 'grounding')}")

    if not proposal.ok:
        if args.execute:
            print("NOT executed: the proposal was rejected")
        return 1
    if not args.execute:
        return 0
    return execute_proposal(proposal, make_controller())


if __name__ == "__main__":
    sys.exit(main())
