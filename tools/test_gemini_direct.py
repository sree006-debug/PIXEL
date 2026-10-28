"""TEMPORARY diagnostic: find which request feature makes Gemini slow.

    .venv\\Scripts\\python tools\\test_gemini_direct.py          # 1280px, like the agent
    .venv\\Scripts\\python tools\\test_gemini_direct.py --full   # send full 1920x1080

Takes ONE screenshot, then sends it in four isolated requests, one after another:
    A  baseline: tiny prompt, no system instruction, no schema, minimal thinking
    B  real DECIDE system prompt + real Step 2 text, no schema, minimal thinking
    C  B + DECISION_SCHEMA (exactly as agent/gemini_provider.py sends it)
    D  C with thinking level "low"
All four tests share ONE client (like the agent's GeminiProvider), with about
2 s between requests, and call client.interactions.create() the same way
agent/gemini_provider.py does, with the same 90 s VLM_TIMEOUT.
Bypasses the agent entirely (no loop, no Controller, no PyAutoGUI).
Sends the screenshot, so close anything private. Delete once diagnosed.
"""

import base64
import io
import os
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from dotenv import load_dotenv

load_dotenv(PROJECT_DIR / ".env")

import config  # noqa: E402  (after load_dotenv, reads VLM_TIMEOUT)
from agent import prompts, screen  # noqa: E402
from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

MODEL = "gemini-3.5-flash-lite"
BASELINE_PROMPT = 'Look at this screenshot and return only JSON: {"action":"wait"}'
PAUSE_BETWEEN_REQUESTS = 2.0  # seconds
TASK = "Open Calculator and calculate 25 multiplied by 16."
STEP1_HISTORY = ("CLICK x=628 y=971 -> OK: The Windows Calculator app is clearly open and "
                 "visible in the center of the screen with the title 'Calculator' and "
                 "Standard mode selected. (confidence 1.00)")
# Same builder the agent uses, so the text is exactly what Step 2 sent.
STEP2_TEXT = prompts.build_decide_text(TASK, [STEP1_HISTORY])

TESTS = [
    # name, system instruction, user text, schema, thinking level
    ("A baseline", None, BASELINE_PROMPT, None, "minimal"),
    ("B decide prompt", prompts.DECIDE_SYSTEM_PROMPT, STEP2_TEXT, None, "minimal"),
    ("C prompt+schema", prompts.DECIDE_SYSTEM_PROMPT, STEP2_TEXT, prompts.DECISION_SCHEMA, "minimal"),
    ("D C+thinking low", prompts.DECIDE_SYSTEM_PROMPT, STEP2_TEXT, prompts.DECISION_SCHEMA, "low"),
]


def now():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def run_test(client, image_b64, name, system, text, schema, thinking):
    print(f"\n=== TEST {name}  (system={'yes' if system else 'no'}, "
          f"schema={'yes' if schema else 'no'}, thinking={thinking}) ===")
    kwargs = dict(
        model=MODEL,
        store=False,
        input=[
            {"type": "text", "text": text},
            {"type": "image", "data": image_b64, "mime_type": "image/png"},
        ],
        generation_config={"thinking_level": thinking},
    )
    if system:
        kwargs["system_instruction"] = system
    if schema:
        kwargs["response_format"] = {"type": "text", "mime_type": "application/json",
                                     "schema": schema}

    start = now()
    t0 = time.perf_counter()
    interaction, error = None, None
    try:
        interaction = client.interactions.create(**kwargs)
    except Exception as e:  # report everything, including timeouts
        error = e
    elapsed = time.perf_counter() - t0
    end = now()

    result = {"name": name, "start": start, "end": end, "latency": elapsed}
    print(f"start     : {start}")
    print(f"end       : {end}")
    print(f"latency   : {elapsed:.2f} s")
    if error is not None:
        timed_out = "timeout" in type(error).__name__.lower()
        result["outcome"] = "TIMEOUT" if timed_out else "ERROR"
        result["error"] = f"{type(error).__module__}.{type(error).__name__}: {str(error)[:300]}"
        print(f"outcome   : {result['outcome']}")
        print(f"exception : {result['error']}")
        return result

    result["outcome"] = "completed"
    result["status"] = getattr(interaction, "status", "unknown")
    result["usage"] = getattr(interaction, "usage", None)
    result["output"] = getattr(interaction, "output_text", None)
    print(f"outcome   : completed (status {result['status']})")
    print(f"usage     : {result['usage']}")
    print(f"output    : {result['output']!r}")
    return result


def main():
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("GEMINI_API_KEY is not set in .env")
        return 1
    timeout = config.VLM_TIMEOUT
    full = "--full" in sys.argv

    import google.genai as genai_pkg
    print(f"google-genai version : {getattr(genai_pkg, '__version__', 'unknown')}")
    print(f"model                : {MODEL}")
    print(f"timeout              : {timeout:.0f} s")
    print(f"api key              : set ({len(api_key)} chars, not shown)")

    # ONE client for every request, as the agent does.
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=int(timeout * 1000)),  # milliseconds
    )

    screen.enable_dpi_awareness()
    image = screen.take_screenshot()
    print(f"screenshot captured  : {image.width}x{image.height}")
    if not full:
        image = screen.resize_for_model(image, config.VLM_IMAGE_MAX_WIDTH)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    png = buffer.getvalue()
    image_b64 = base64.b64encode(png).decode("ascii")
    print(f"image sent           : {image.width}x{image.height} PNG, {len(png) / 1024:.0f} KB")
    print(f"\nStep 2 text:\n{STEP2_TEXT}")

    total_t0 = time.perf_counter()
    results = []
    for i, test in enumerate(TESTS):
        if i:
            time.sleep(PAUSE_BETWEEN_REQUESTS)
        results.append(run_test(client, image_b64, *test))
    total = time.perf_counter() - total_t0

    print("\n=== SUMMARY ===")
    for r in results:
        print(f"{r['name']:<18} {r['latency']:7.2f} s  {r['outcome']}")
    print(f"{'total (with pauses)':<18} {total:7.2f} s  (one shared client)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
