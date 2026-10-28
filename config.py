"""Central settings for the agent. Edit values here instead of inside modules."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")

# --- Agent loop (agent/loop.py) ---
MAX_STEPS = 25         # hard cap on steps (VLM decisions) per task
MAX_RETRIES = 3         # consecutive failed steps allowed; one more failure ends the task
VERIFY_EACH_STEP = True # ask the VLM to verify every action's expected_outcome
VERIFY_MIN_CONFIDENCE = 0.5  # a "success" below this confidence counts as a failure
HISTORY_LENGTH = 8      # how many previous steps the VLM is shown
VLM_RETRY_BACKOFF = 2.0 # seconds x consecutive failures to wait before retrying (max 10 s)

# --- Timing (seconds) ---
ACTION_DELAY = 1.0      # wait after each action before taking a new screenshot
MOVE_DURATION = 0.3     # default mouse travel time for MOVE / DRAG
TYPE_INTERVAL = 0.02    # delay between typed characters

# --- Safety ---
# The PyAutoGUI failsafe (mouse into a screen corner = abort) is always on;
# see agent/controller.py. It cannot be disabled from here on purpose.
DRY_RUN = False         # True = validate and log actions but never move/type

# --- Action validation limits (agent/actions.py) ---
MAX_TYPE_CHARS = 200    # longest text a single TYPE may enter
MAX_SCROLL = 15         # max wheel notches per SCROLL
MIN_WAIT = 0.1          # WAIT seconds range
MAX_WAIT = 10.0
MAX_MOVE_DURATION = 2.0 # longest allowed MOVE/DRAG duration
CORNER_MARGIN = 5       # pixels around each corner that actions may not target

# --- VLM ---
# Which provider/model/key to use is read from .env by agent/vlm.py:
#   VLM_PROVIDER=gemini   VLM_MODEL=...   GEMINI_API_KEY=...
VLM_TIMEOUT = float(os.getenv("VLM_TIMEOUT", "90"))  # seconds per request
VLM_IMAGE_MAX_WIDTH = 1280  # screenshots are downscaled to this width before sending

# --- Visual grounding (agent/grounding.py) ---
GROUNDING_MIN_CONFIDENCE = 0.6  # a located target below this confidence is not used for actions
# A grounded click needs a box that pins down ONE element. Larger boxes are
# refused whatever the confidence, because their centre may be anything.
GROUNDING_MAX_BOX_SIDE = 0.5    # box width/height: at most this fraction of the screen's
GROUNDING_MAX_BOX_AREA = 0.05   # box area: at most this fraction of the screen's

# --- Output ---
RUNS_DIR = PROJECT_DIR / "runs"        # one folder per agent run: log.jsonl + screenshots
SCRATCH_DIR = PROJECT_DIR / "scratch"  # safe place for demo files (never personal folders)
