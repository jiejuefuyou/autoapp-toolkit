# Desktop loop setup on macOS

Same script as the Windows version; different scheduling layer.

## Differences from Windows

| Concern | Windows | macOS |
|---|---|---|
| Idle detection | `GetLastInputInfo` via ctypes | `Quartz.CGEventSourceSecondsSinceLastEventType()` |
| Scheduler | Task Scheduler | launchd (`com.autoapp.loop.plist`) |
| Permissions | None needed | **Accessibility** (Settings → Privacy & Security → Accessibility → enable Terminal/Python) |
| Log path | `desktop-loop/loop.log` | same |
| Pause | `pause.lock` file | same |

The Python script is **the same `autoapp_loop.py`** — pyautogui and pyperclip both work on macOS. The idle detection block already has a Windows-only branch; add a macOS branch (one function, ~10 lines) and you're done.

## Patch needed for autoapp_loop.py (macOS support)

Replace `get_idle_seconds()` with this cross-platform version:

```python
def get_idle_seconds() -> float:
    """Seconds since the user's last mouse / keyboard input."""
    if sys.platform == "win32":
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(lii)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return 0.0
        millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
        return millis / 1000.0
    elif sys.platform == "darwin":
        # Requires `pip install pyobjc-framework-Quartz`
        try:
            from Quartz import CGEventSourceSecondsSinceLastEventType, kCGAnyInputEventType, kCGEventSourceStateHIDSystemState
            return CGEventSourceSecondsSinceLastEventType(
                kCGEventSourceStateHIDSystemState, kCGAnyInputEventType
            )
        except ImportError:
            return 0.0
    else:  # linux
        return 0.0  # X11/Wayland varies; stub for now
```

(I'll roll this into the main script in a follow-up commit if there's demand. PRs welcome.)

## Setup walkthrough

### 1. Install Python deps

```sh
python3 -m venv ~/.venv
source ~/.venv/bin/activate
pip install pyautogui pyperclip pyobjc-framework-Quartz
```

(`pyobjc-framework-Quartz` is the macOS-specific dep for idle detection.)

### 2. Grant Accessibility permission

macOS will refuse to let the script send synthetic mouse/keyboard events without explicit permission.

1. Run the script ONCE manually first:
   ```sh
   python3 ~/autoapp/desktop-loop/autoapp_loop.py
   ```
2. macOS will pop a dialog: "Terminal would like to control your computer using accessibility features."
3. Click **Open System Settings** → **Privacy & Security** → **Accessibility**
4. Toggle on **Terminal** (or whatever shell you used; iTerm, etc.)
5. **If you're running via launchd later**, also add **Python** itself: navigate to `/Users/USERNAME/.venv/bin/python` and add it.

Without Accessibility permission, the click + paste will silently fail or pop the dialog every fire.

### 3. Measure CC input box coordinates

Same as Windows. CC plugin must be visible in VSCode.

```sh
python3 -c "import pyautogui, time; print('Move mouse over CC INPUT BOX center within 5s...'); time.sleep(5); print(pyautogui.position())"
```

Move mouse to CC input box. Note the (x, y).

### 4. Fill config.json

```sh
cd ~/autoapp/desktop-loop
cp config.json.template config.json
# Edit cc_input_x, cc_input_y, etc.
```

### 5. Install launchd plist

```sh
# Copy template to ~/Library/LaunchAgents/
cp ~/autoapp/toolkit/desktop-loop/com.autoapp.loop.plist.template \
   ~/Library/LaunchAgents/com.autoapp.loop.plist

# Edit it: replace all USERNAME with your actual macOS username
sed -i '' "s/USERNAME/$(whoami)/g" ~/Library/LaunchAgents/com.autoapp.loop.plist

# Load + start
launchctl load -w ~/Library/LaunchAgents/com.autoapp.loop.plist

# Verify it's running (should show in the list)
launchctl list | grep autoapp
```

### 6. Test

```sh
# Manually trigger one execution
launchctl kickstart -k gui/$(id -u)/com.autoapp.loop

# Check log
tail -f ~/autoapp/desktop-loop/loop.log
```

## Control

### Pause (no need to stop launchd)

```sh
touch ~/autoapp/desktop-loop/pause.lock
```

### Resume

```sh
rm ~/autoapp/desktop-loop/pause.lock
```

### Permanent stop

```sh
launchctl unload -w ~/Library/LaunchAgents/com.autoapp.loop.plist
rm ~/Library/LaunchAgents/com.autoapp.loop.plist
```

### Logs

```sh
# Script's own log (decisions: SKIP / FIRE / DONE)
tail -f ~/autoapp/desktop-loop/loop.log

# launchd's log (only catches Python exceptions)
tail -f ~/autoapp/desktop-loop/launchd.stderr.log
```

## Caveats

- **Multiple monitors**: pyautogui treats them as one virtual screen, like Windows. If your CC panel is on a second display, the x coordinate might be > 1920 or even negative.
- **Window focus**: VSCode must be in the foreground. macOS doesn't make this easy to enforce from a background script. If you want to enforce focus before clicking, add `subprocess.run(["osascript", "-e", 'tell application "Visual Studio Code" to activate'])` before the click.
- **Sleep mode**: launchd jobs don't fire while the Mac is asleep. The next fire is the first scheduled time after wake. Compare with Windows where Task Scheduler can wake the system (option not enabled in our template).
- **System Integrity Protection / sandboxing**: if you installed VSCode from the App Store, sandboxing might block synthetic events even with Accessibility permission. Use the standalone `Visual Studio Code.app` from code.visualstudio.com instead.

## When to choose Windows vs Mac

This is more about which OS you're already using, but for reference:

- **Mac**: better single-monitor experience, easier permission dialogs, launchd is more transparent than Task Scheduler XML.
- **Windows**: better multi-monitor scaling, no Accessibility permission friction, Task Scheduler GUI is friendlier.

The script's behavior is identical on both.
