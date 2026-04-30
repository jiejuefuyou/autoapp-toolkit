# Desktop loop setup — 让 CC 插件 24/7 自主跑

模拟你手动操作 VSCode + Claude Code 插件，每 30 分钟塞一次 LOOP.md 任务给它。
**完全跑你的订阅 token，不消耗 API tokens**。

## 工作原理

- Windows Task Scheduler 每 N 分钟跑 `autoapp_loop.py`
- 脚本检测：你 ≥ 3 分钟没动鼠标/键盘 → 视为离线 → 模拟操作
- 模拟流程：click CC 输入框坐标 → 粘贴 cron prompt → 按 Enter
- 你回来时（动鼠标）脚本自动跳过本次 fire，不打扰

## 一次性配置（5-10 分钟）

### 步骤 1：测出 CC 输入框坐标

**为什么需要这一步**：每个人 VSCode 窗口大小、CC 插件 panel 位置、屏幕分辨率都不同。我没法事先知道你的输入框在屏幕哪个像素位置。

操作：
1. 打开 VSCode + Claude Code 插件 panel（确保 input 区域可见）
2. 在 PowerShell 里跑：

```powershell
cd C:\Users\sh199\Desktop\autoapp\desktop-loop
C:\Users\sh199\Desktop\autoapp\scrapers\.venv\Scripts\python -c "import pyautogui, time; print('Move mouse over CC INPUT BOX center within 5s...'); time.sleep(5); print('coord:', pyautogui.position())"
```

3. 5 秒内把鼠标移到 CC 输入框中心
4. 终端会打印类似 `coord: Point(x=1234, y=900)`
5. 把这两个数字记下来

### 步骤 2：填配置

跑一次脚本生成默认 config：
```powershell
cd C:\Users\sh199\Desktop\autoapp\desktop-loop
C:\Users\sh199\Desktop\autoapp\scrapers\.venv\Scripts\python autoapp_loop.py
```

会创建 `config.json`。打开它，把 `cc_input_x` 和 `cc_input_y` 填上面记下的坐标：

```json
{
  "cc_input_x": 1234,
  "cc_input_y": 900,
  "idle_threshold_seconds": 180,
  "min_minutes_between_fires": 30,
  "prompt_file": "../orchestrator/cron-prompt.txt",
  "last_fire_marker": ".last-fire"
}
```

字段说明：
- `idle_threshold_seconds` — 你必须连续 N 秒没动鼠标键盘才会 fire（默认 3 分钟，防打断）
- `min_minutes_between_fires` — 两次 fire 最小间隔（默认 30 分钟，避免 CC 还在跑长任务时塞新 prompt）
- `prompt_file` — 模拟输入的内容文件（默认是 `orchestrator/cron-prompt.txt`，已写好）

### 步骤 3：手动测一次

在 PowerShell 跑：
```powershell
cd C:\Users\sh199\Desktop\autoapp\desktop-loop
C:\Users\sh199\Desktop\autoapp\scrapers\.venv\Scripts\python autoapp_loop.py
```

预期：
- 如果你刚动鼠标 < 3 分钟 → 输出 "SKIP: user active"（正常）
- 如果你 3 分钟没动 → 鼠标会自动移到 CC 输入框 → 粘贴 prompt → 按 Enter

测试时建议：先把 `idle_threshold_seconds` 改成 1，然后等 1 秒后跑脚本验证。验证完改回 180。

### 步骤 4：配 Windows Task Scheduler

```powershell
# 在 PowerShell（管理员模式）跑这条命令一次
$action = New-ScheduledTaskAction -Execute "C:\Users\sh199\Desktop\autoapp\scrapers\.venv\Scripts\python.exe" -Argument "C:\Users\sh199\Desktop\autoapp\desktop-loop\autoapp_loop.py" -WorkingDirectory "C:\Users\sh199\Desktop\autoapp\desktop-loop"

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 30)

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

Register-ScheduledTask -TaskName "AutoAppLoop" -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited
```

之后每 30 分钟自动 fire。

## 控制开关

### 暂停（不停止 task scheduler）
```powershell
New-Item -Path "C:\Users\sh199\Desktop\autoapp\desktop-loop\pause.lock" -ItemType File
```
脚本下次 fire 时检测到 lock 文件 → silent skip。

### 恢复
```powershell
Remove-Item "C:\Users\sh199\Desktop\autoapp\desktop-loop\pause.lock"
```

### 永久停止
```powershell
Unregister-ScheduledTask -TaskName "AutoAppLoop" -Confirm:$false
```

### 看日志
```powershell
Get-Content C:\Users\sh199\Desktop\autoapp\desktop-loop\loop.log -Tail 20
```

## 故障应对

**模拟 click 后 prompt 没出现在 CC 输入框**
→ 坐标错了。重做步骤 1。可能 VSCode 窗口大小变了。

**Prompt 出现在了别的窗口（比如 chrome）**
→ VSCode 不在 foreground。脚本目前不强制把 VSCode 拉到前台。要修这个：脚本加 `pywinauto` 调 `Application().connect(title='Visual Studio Code').top_window().set_focus()`。Backlog 项。

**循环里 CC 报错 / 限量**
→ CC 把 prompt queue 起来或显示 error。脚本不会重试，下个 fire (30 min 后) 再试。如果 limit 到了等订阅 reset 自然恢复。

**我离开电脑很久回来发现脚本疯狂操作**
→ `idle_threshold_seconds` 默认 180 阻止了大多数干扰。如果担心，touch `pause.lock` 文件。

## 安全 note

- 脚本不读你输入的密码 / 屏幕内容；它只 click + paste + Enter
- pause.lock 文件给你 1 秒级控制权
- FAILSAFE: pyautogui 默认配置下，把鼠标快速甩到屏幕**左上角**会立即 abort 当前操作

## 局限性

- **VSCode 必须在前台 + CC panel input 必须可见**
- **窗口大小 / 屏幕分辨率变了 → 坐标失效需重配**
- **多显示器场景没处理**（默认主屏）
- **CC 插件 UI 改动 → 可能要重测坐标**

这是 trade-off：用 30-60 分钟一次性配置 + 偶尔重测，换来 24/7 不消耗 API token 的循环。

## 下一步

配完后告诉 CC "desktop loop 已配 + task scheduler 已开启"。CC 可以删除当前 session-only cron。
