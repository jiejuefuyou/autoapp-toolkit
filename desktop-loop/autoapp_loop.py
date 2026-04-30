"""
autoapp_loop.py — Desktop GUI automation that mimics a human poking the
Claude Code panel in VSCode every N minutes to keep the autonomous loop alive.

Why this exists: CronCreate jobs in Claude Code are session-only. When the
VSCode CC plugin is paused / closed, the cron dies. This script lives outside
Claude Code entirely; it uses Windows Task Scheduler + PyAutoGUI to simulate
clicks + paste + Enter, indistinguishable from a human operator.

Hard constraints baked in:
- Only fires if the user has been INACTIVE (no mouse/keyboard) for >= IDLE_THRESHOLD seconds.
  Default 180s. Prevents stomping on the user's actual work.
- Skipped silently if a `pause.lock` file exists in this directory. Touch the
  file to pause the loop without disabling the Task Scheduler entry.
- Logs every action / skip to `loop.log` so you can audit what happened.

Setup: see SETUP.md in this directory.
"""
from __future__ import annotations
import ctypes
import datetime
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
PAUSE_LOCK = ROOT / "pause.lock"
LOG_PATH = ROOT / "loop.log"

DEFAULT_CONFIG = {
    "cc_input_x": 0,
    "cc_input_y": 0,
    "idle_threshold_seconds": 180,
    "min_minutes_between_fires": 30,
    "prompt_file": "../orchestrator/cron-prompt.txt",
    "last_fire_marker": ".last-fire",
}


def log(msg: str) -> None:
    line = f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# Windows: GetLastInputInfo to detect idle time.
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def get_idle_seconds() -> float:
    """Seconds since the user's last mouse / keyboard input (Windows-only)."""
    if sys.platform != "win32":
        return 0.0
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(lii)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0.0
    millis_since_input = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
    return millis_since_input / 1000.0


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
        log(f"created default config at {CONFIG_PATH} — edit cc_input_x/y before running")
        return DEFAULT_CONFIG
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"config parse error: {e}; using defaults")
        return DEFAULT_CONFIG


def main() -> int:
    cfg = load_config()

    # Pause lock — silent skip
    if PAUSE_LOCK.exists():
        log("SKIP: pause.lock present")
        return 0

    # Coordinate not configured
    if cfg["cc_input_x"] == 0 and cfg["cc_input_y"] == 0:
        log("SKIP: cc_input_x/y not configured in config.json — see SETUP.md")
        return 0

    # User active — silent skip
    idle = get_idle_seconds()
    if idle < cfg["idle_threshold_seconds"]:
        log(f"SKIP: user active (idle={idle:.0f}s < threshold {cfg['idle_threshold_seconds']}s)")
        return 0

    # Min interval between fires
    last_fire_path = ROOT / cfg["last_fire_marker"]
    if last_fire_path.exists():
        elapsed = time.time() - last_fire_path.stat().st_mtime
        min_seconds = cfg["min_minutes_between_fires"] * 60
        if elapsed < min_seconds:
            log(f"SKIP: too soon since last fire ({elapsed/60:.1f}min < {cfg['min_minutes_between_fires']}min)")
            return 0

    # Prompt file
    prompt_path = (ROOT / cfg["prompt_file"]).resolve()
    if not prompt_path.exists():
        log(f"SKIP: prompt file missing: {prompt_path}")
        return 0
    prompt_text = prompt_path.read_text(encoding="utf-8")
    if not prompt_text.strip():
        log("SKIP: prompt file empty")
        return 0

    # Import lazily so the script doesn't crash when run on a system without
    # display (e.g., during install verification)
    try:
        import pyautogui
        import pyperclip
    except ImportError as e:
        log(f"FAIL: missing dep {e}; pip install pyautogui pyperclip")
        return 1

    pyautogui.PAUSE = 0.2
    pyautogui.FAILSAFE = True  # mouse to top-left corner aborts

    log(f"FIRE: idle={idle:.0f}s, prompt {len(prompt_text)} chars")

    try:
        # 1. Click the CC input area to focus it
        pyautogui.click(x=cfg["cc_input_x"], y=cfg["cc_input_y"])
        time.sleep(0.4)

        # 2. Copy prompt to clipboard
        pyperclip.copy(prompt_text)
        time.sleep(0.2)

        # 3. Paste with Ctrl+V
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.4)

        # 4. Submit with Enter
        pyautogui.press("enter")

        # Mark fired
        last_fire_path.touch()
        log("DONE: prompt submitted")
    except Exception as e:
        log(f"FAIL: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
