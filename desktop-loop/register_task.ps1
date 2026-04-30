# register_task.ps1 — 一键注册 AutoAppLoop Task Scheduler 任务
#
# 用法（PowerShell，不需要管理员）：
#     powershell -ExecutionPolicy Bypass -File register_task.ps1
#
# 这个脚本会：
# 1. 检测 venv 路径（自动找 pythonw.exe，必须用 pythonw 不能用 python！）
# 2. unregister 旧的 AutoAppLoop（如果存在）
# 3. 用 pythonw.exe + Hidden=True 注册新的（不会闪 console 窗口）
# 4. 验证注册成功
#
# 修复：用 python.exe 注册会每分钟闪一个黑色 console 窗口（用户报告"弹窗一会一弹"）
# 详见 SETUP.md "必须用 pythonw.exe，不是 python.exe" 段

$ErrorActionPreference = "Stop"

$LoopDir = $PSScriptRoot
$ScriptPath = Join-Path $LoopDir "autoapp_loop.py"

Write-Host "=== AutoAppLoop Task Scheduler 注册脚本 ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Loop dir: $LoopDir"
Write-Host "Script: $ScriptPath"
Write-Host ""

# ===== Step 1: 找 pythonw.exe =====

$PythonwCandidates = @(
    # 默认尝试本项目下的 venv
    (Join-Path (Split-Path $LoopDir -Parent) "scrapers\.venv\Scripts\pythonw.exe"),
    (Join-Path (Split-Path $LoopDir -Parent) ".venv\Scripts\pythonw.exe"),
    (Join-Path $LoopDir ".venv\Scripts\pythonw.exe"),
    # 系统 Python
    "$env:LOCALAPPDATA\Programs\Python\Python312\pythonw.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\pythonw.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\pythonw.exe"
)

$PythonwPath = $null
foreach ($candidate in $PythonwCandidates) {
    if (Test-Path $candidate) {
        $PythonwPath = $candidate
        Write-Host "✅ Found pythonw.exe at: $PythonwPath" -ForegroundColor Green
        break
    }
}

if (-not $PythonwPath) {
    Write-Host "❌ 找不到 pythonw.exe" -ForegroundColor Red
    Write-Host ""
    Write-Host "尝试过的位置：" -ForegroundColor Yellow
    foreach ($candidate in $PythonwCandidates) {
        Write-Host "  - $candidate"
    }
    Write-Host ""
    Write-Host "请创建 Python venv 或修改本脚本中 \$PythonwCandidates 数组添加你的 pythonw.exe 路径。" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "venv 创建方法：" -ForegroundColor Yellow
    Write-Host "    cd ../scrapers"
    Write-Host "    python -m venv .venv"
    Write-Host "    .venv/Scripts/pip install pyautogui pyperclip"
    exit 1
}

# ===== Step 2: 检测脚本依赖 =====

if (-not (Test-Path $ScriptPath)) {
    Write-Host "❌ 找不到 autoapp_loop.py at $ScriptPath" -ForegroundColor Red
    exit 1
}

# 测一下 pythonw 能 import 必要模块
Write-Host ""
Write-Host "测试 pythonw 依赖..." -NoNewline
$testResult = & $PythonwPath -c "import pyautogui, pyperclip; print('OK')" 2>&1
# pythonw 没 stdout，所以 $testResult 永远空。改测 exit code
if ($LASTEXITCODE -ne 0) {
    Write-Host " ❌"
    Write-Host "pythonw 缺依赖。请：" -ForegroundColor Red
    $PipPath = $PythonwPath -replace 'pythonw\.exe$', 'pip.exe'
    Write-Host "    & '$PipPath' install pyautogui pyperclip"
    exit 1
} else {
    Write-Host " ✅"
}

# ===== Step 3: Unregister 旧 task（如果存在）=====

$existing = Get-ScheduledTask -TaskName "AutoAppLoop" -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host ""
    Write-Host "Unregister 旧 AutoAppLoop（state=$($existing.State)）..." -NoNewline
    Unregister-ScheduledTask -TaskName "AutoAppLoop" -Confirm:$false | Out-Null
    Write-Host " ✅"
}

# ===== Step 4: Register 新 task =====

Write-Host ""
Write-Host "注册 AutoAppLoop（pythonw + Hidden）..." -NoNewline

$action = New-ScheduledTaskAction `
    -Execute $PythonwPath `
    -Argument $ScriptPath `
    -WorkingDirectory $LoopDir

$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 365)

$settings = New-ScheduledTaskSettingsSet `
    -Hidden `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$desc = "Desktop loop with pythonw.exe (no console) + Hidden=True. Idempotent — uses internal gates to decide if FIRE."

Register-ScheduledTask `
    -TaskName "AutoAppLoop" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description $desc | Out-Null

Write-Host " ✅"

# ===== Step 5: 验证 =====

Write-Host ""
$task = Get-ScheduledTask -TaskName "AutoAppLoop"
Write-Host "=== 验证 ===" -ForegroundColor Cyan
Write-Host "TaskName: $($task.TaskName)"
Write-Host "State: $($task.State)"
Write-Host "Action.Execute: $($task.Actions[0].Execute)"
Write-Host "  → 应该是 pythonw.exe 路径，不是 python.exe ✅" -ForegroundColor Green
Write-Host "Action.Arguments: $($task.Actions[0].Arguments)"
Write-Host "Settings.Hidden: $($task.Settings.Hidden)"
Write-Host "  → 应该是 True ✅" -ForegroundColor Green
Write-Host ""

# ===== Step 6: 用户提示 =====

Write-Host "=== 完成 ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "下一分钟 task 会首次 fire（但脚本会 SKIP 因为 idle 太短）"
Write-Host "看 log: Get-Content '$LoopDir\loop.log' -Tail 5 -Wait"
Write-Host ""
Write-Host "停止/恢复："
Write-Host "  暂停: New-Item '$LoopDir\pause.lock' -ItemType File -Force"
Write-Host "  恢复: Remove-Item '$LoopDir\pause.lock'"
Write-Host ""
Write-Host "完全 disable: Disable-ScheduledTask -TaskName AutoAppLoop"
Write-Host "彻底删除: Unregister-ScheduledTask -TaskName AutoAppLoop -Confirm:`$false"
