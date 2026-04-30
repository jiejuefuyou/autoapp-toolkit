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
    "cc_busy_check_file": "../INBOX/STATUS.md",
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
    """Seconds since the user's last mouse / keyboard input."""
    if sys.platform == "win32":
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(lii)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return 0.0
        millis_since_input = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
        return millis_since_input / 1000.0
    if sys.platform == "darwin":
        # Requires `pip install pyobjc-framework-Quartz`
        try:
            from Quartz import (
                CGEventSourceSecondsSinceLastEventType,
                kCGAnyInputEventType,
                kCGEventSourceStateHIDSystemState,
            )
            return CGEventSourceSecondsSinceLastEventType(
                kCGEventSourceStateHIDSystemState, kCGAnyInputEventType
            )
        except ImportError:
            return 0.0
    return 0.0  # linux/other: stub


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

    # === No-popup fire gate (Windows only) ===
    # User-reported issue: even with focus restore + foreground check, fires
    # still cause visible flashes because clicking into VSCode briefly
    # activates the window (the activation animation IS the popup).
    #
    # Definitive fix: fire is allowed ONLY when ALL chance of user noticing
    # is gone. Three conditions; ANY ONE allows fire:
    #   (a) Screen is locked (Win+L state) — user definitionally absent
    #   (b) VSCode is already the foreground window — clicking it doesn't
    #       cause window activation (already active)
    #   (c) idle > require_screen_locked_unless_idle_seconds (default 1800s
    #       = 30 min, well past coffee/lunch break)
    #
    # Defaults are conservative: prefer "agent fires less often" over
    # "agent flashes my screen". Set require_screen_locked=false in config
    # to disable (a)+(c) and rely only on (b)+idle threshold.
    if sys.platform == "win32":
        require_lock = cfg.get("require_screen_locked", True)
        idle_unlock_override = cfg.get("require_screen_locked_unless_idle_seconds", 1800)
        vscode_substr = cfg.get("vscode_window_substring", "Visual Studio Code")
        try:
            user32 = ctypes.windll.user32
            # Check (a): screen locked. OpenInputDesktop fails when locked
            # (winlogon owns the input desktop, non-elevated proc can't open).
            DESKTOP_READOBJECTS = 0x0001
            hdesk = user32.OpenInputDesktopW(0, False, DESKTOP_READOBJECTS) if hasattr(user32, "OpenInputDesktopW") \
                else user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
            screen_locked = (hdesk == 0)
            if hdesk:
                user32.CloseDesktop(hdesk)

            # Check (b): VSCode foreground
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            in_vscode = vscode_substr.lower() in title.lower()

            # Check (c): idle long enough
            very_idle = idle >= idle_unlock_override

            allow_fire = screen_locked or in_vscode or very_idle or not require_lock
            if not allow_fire:
                reasons = []
                if not screen_locked: reasons.append("not locked")
                if not in_vscode: reasons.append(f"fg='{title[:30]}'")
                if not very_idle: reasons.append(f"idle {idle:.0f}<{idle_unlock_override}")
                log(f"SKIP: no-popup gate ({', '.join(reasons)}) — would flash")
                return 0
            else:
                # Log which condition allowed fire (helps debugging)
                why = "locked" if screen_locked else ("vscode-fg" if in_vscode else ("very-idle" if very_idle else "gate-disabled"))
                log(f"GATE PASSED: {why} (idle={idle:.0f}s, fg='{title[:30]}')")
        except Exception as e:
            log(f"WARN: no-popup gate failed ({e}) — proceeding anyway")

    # Min interval between fires
    last_fire_path = ROOT / cfg["last_fire_marker"]
    if last_fire_path.exists():
        elapsed = time.time() - last_fire_path.stat().st_mtime
        min_seconds = cfg["min_minutes_between_fires"] * 60
        if elapsed < min_seconds:
            log(f"SKIP: too soon since last fire ({elapsed/60:.1f}min < {cfg['min_minutes_between_fires']}min)")
            return 0

    # Layer 1 busy: explicit .inflight marker. The agent prompt instructs CC to
    # `touch .inflight` at tick start and `rm .inflight` at tick end. If present,
    # CC is still mid-work — never fire. (Survives across STATUS.md being
    # appended early in the tick.)
    inflight_marker = cfg.get("inflight_marker")
    if inflight_marker:
        inflight_path = ROOT / inflight_marker
        if inflight_path.exists():
            age = (time.time() - inflight_path.stat().st_mtime) / 60
            log(f"SKIP: .inflight present (age {age:.1f}min) — CC mid-tick")
            return 0

    # Layer 2 busy: STATUS.md was modified recently. Even after the agent
    # appends its progress note, give it a grace window before allowing the
    # next fire — appending is usually one of the LAST things in a tick but
    # not literally last (commits, log inspection often follow).
    busy_check = cfg.get("cc_busy_check_file")
    grace_min = cfg.get("post_status_grace_minutes", 5)
    if busy_check:
        status_path = (ROOT / busy_check).resolve()
        if status_path.exists():
            status_age_min = (time.time() - status_path.stat().st_mtime) / 60
            if status_age_min < grace_min:
                log(f"SKIP: STATUS.md modified {status_age_min:.1f}min ago < grace {grace_min}min")
                return 0

    # Layer 3 busy: original check — if .last-fire newer than STATUS.md, the
    # agent never finished its accounting from the previous fire.
    if last_fire_path.exists() and busy_check:
        status_path = (ROOT / busy_check).resolve()
        if status_path.exists():
            last_fire_mtime = last_fire_path.stat().st_mtime
            status_mtime = status_path.stat().st_mtime
            if last_fire_mtime > status_mtime:
                gap = (last_fire_mtime - status_mtime) / 60
                log(f"SKIP: CC still busy (last fire +{gap:.1f}min ago, STATUS.md not updated since)")
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

    # Tighter pace = less visible flash. 0.2 -> 0.05 PAUSE,
    # in-fire sleeps reduced. Total fire duration ~0.3s vs ~1.2s before.
    pyautogui.PAUSE = 0.05
    pyautogui.FAILSAFE = True  # mouse to top-left corner aborts

    log(f"FIRE: idle={idle:.0f}s, prompt {len(prompt_text)} chars")

    # Save current foreground window so we can restore focus after the fire.
    # Without this, clicking the CC input box steals focus from whatever
    # the user / system was doing — visible flash even when idle threshold
    # passed (user might glance at screen mid-fire).
    saved_hwnd = None
    saved_mouse = None
    if sys.platform == "win32":
        try:
            saved_hwnd = ctypes.windll.user32.GetForegroundWindow()
            saved_mouse = pyautogui.position()
        except Exception:
            pass

    try:
        # Pre-load clipboard before stealing focus — minimizes visible time.
        pyperclip.copy(prompt_text)
        time.sleep(0.05)

        # 1. Click the CC input area to focus it
        pyautogui.click(x=cfg["cc_input_x"], y=cfg["cc_input_y"])
        time.sleep(0.1)

        # 2. Paste + submit back-to-back (no time.sleep gaps the user can see)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.1)
        pyautogui.press("enter")

        # 3. Restore prior foreground + mouse position (Windows only).
        # SetForegroundWindow has rules; best-effort, no error if it fails.
        if sys.platform == "win32":
            if saved_hwnd:
                try:
                    ctypes.windll.user32.SetForegroundWindow(saved_hwnd)
                except Exception:
                    pass
            if saved_mouse:
                try:
                    pyautogui.moveTo(saved_mouse[0], saved_mouse[1], duration=0)
                except Exception:
                    pass

        # Mark fired + in-flight. The agent removes .inflight at tick end.
        last_fire_path.touch()
        if cfg.get("inflight_marker"):
            (ROOT / cfg["inflight_marker"]).touch()
        log("DONE: prompt submitted (.inflight set, focus restored)")
    except Exception as e:
        log(f"FAIL: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
