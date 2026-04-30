"""
test_no_popup_gate.py — 用户可手动跑的 popup gate 验证脚本

用法：
    python test_no_popup_gate.py

在不锁屏 + 非 VSCode foreground + 短 idle 状态下跑：
    应该看到 SKIP: no-popup gate 而不是 FIRE

锁屏后跑 (Win+L → 等 1-2 min → 解锁):
    应该看到 FIRE 记录在 loop.log（在锁屏期间发生的）

如果两个测试都符合预期 → popup fix 完整生效。
"""
from __future__ import annotations
import ctypes
import json
import pathlib
import sys
import time

if sys.platform == "win32":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except: pass

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"


def get_idle_seconds() -> float:
    if sys.platform != "win32":
        return 0.0
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(lii)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0.0
    return (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 1000.0


def get_foreground_window_title() -> str:
    if sys.platform != "win32":
        return "(non-windows)"
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def is_screen_locked() -> bool:
    if sys.platform != "win32":
        return False
    user32 = ctypes.windll.user32
    DESKTOP_READOBJECTS = 0x0001
    try:
        hdesk = user32.OpenInputDesktopW(0, False, DESKTOP_READOBJECTS) if hasattr(user32, "OpenInputDesktopW") else user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    except Exception as e:
        print(f"[ERROR] OpenInputDesktop failed: {e}")
        return False
    if hdesk == 0:
        return True
    user32.CloseDesktop(hdesk)
    return False


def main():
    print("=" * 60)
    print("Popup gate 状态检查 — 验证 desktop-loop 配置生效")
    print("=" * 60)
    print()

    if not CONFIG_PATH.exists():
        print(f"[FAIL] config.json 不存在 at {CONFIG_PATH}")
        return 1

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    require_lock = cfg.get("require_screen_locked", True)
    idle_unlock_override = cfg.get("require_screen_locked_unless_idle_seconds", 1800)
    vscode_substr = cfg.get("vscode_window_substring", "Visual Studio Code")
    idle_threshold = cfg.get("idle_threshold_seconds", 180)
    print(f"配置:")
    print(f"  require_screen_locked: {require_lock}")
    print(f"  idle_unlock_override: {idle_unlock_override}s")
    print(f"  vscode_window_substring: '{vscode_substr}'")
    print(f"  idle_threshold_seconds: {idle_threshold}")
    print()

    # 当前状态
    idle = get_idle_seconds()
    title = get_foreground_window_title()
    locked = is_screen_locked()
    in_vscode = vscode_substr.lower() in title.lower()
    very_idle = idle >= idle_unlock_override

    print(f"当前状态:")
    print(f"  Foreground window: '{title}'")
    print(f"  In VSCode: {in_vscode}")
    print(f"  Screen locked: {locked}")
    print(f"  Idle seconds: {idle:.0f}")
    print(f"  Very idle (≥{idle_unlock_override}s): {very_idle}")
    print()

    # 评估
    print("Gate evaluation:")
    if idle < idle_threshold:
        print(f"  [SKIP] idle {idle:.0f}s < threshold {idle_threshold}s — would skip on idle gate")
        print()
        print("→ 期待行为: SKIP (用户活跃)")
        return 0

    allow = locked or in_vscode or very_idle or not require_lock
    if not allow:
        reasons = []
        if not locked: reasons.append("not locked")
        if not in_vscode: reasons.append(f"fg='{title[:30]}'")
        if not very_idle: reasons.append(f"idle {idle:.0f}<{idle_unlock_override}")
        print(f"  [SKIP] no-popup gate ({', '.join(reasons)}) — would NOT fire")
        print()
        print("→ 期待行为: SKIP (无 popup 风险)")
        print()
        print("✅ Popup gate 正在保护你免受 fire popup 干扰")
        return 0
    else:
        why = "locked" if locked else ("vscode-fg" if in_vscode else ("very-idle" if very_idle else "gate-disabled"))
        print(f"  [PASS] gate would allow fire — reason: {why}")
        print()
        if locked:
            print("→ 期待行为: FIRE (屏幕锁定，用户绝对看不到)")
        elif in_vscode:
            print("→ 期待行为: FIRE (VSCode 已 foreground，无 window swap)")
        elif very_idle:
            print(f"→ 期待行为: FIRE (idle 已超 {idle_unlock_override}s，用户应该不在)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
