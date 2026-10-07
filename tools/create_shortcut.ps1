# 创建桌面快捷方式（带 icon.ico 多分辨率图标）。
# 背景：v0.31.10 的 icon.ico 只在仓库内，桌面 .lnk 是本机状态——换机/重建快捷
# 方式后图标会丢，本脚本让它可复现。默认目标=语言校准室（lang-calib）。
# 用法：  powershell -ExecutionPolicy Bypass -File tools\create_shortcut.ps1
#         powershell ... -Name "校准室" -Target "其他启动脚本.bat"

param(
    [string]$Name = "human-vs-ai",
    [string]$Target = ""
)

$repo = Split-Path -Parent $PSScriptRoot
if (-not $Target) { $Target = Join-Path $repo "lang-calib\start_windows.bat" }
if (-not (Test-Path $Target)) { throw "启动目标不存在: $Target" }
$icon = Join-Path $repo "vscode-extension\icon.ico"
if (-not (Test-Path $icon)) { throw "图标不存在: $icon" }

$wsh = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = $wsh.CreateShortcut("$desktop\$Name.lnk")
$lnk.TargetPath = $Target
$lnk.WorkingDirectory = (Split-Path -Parent $Target)
$lnk.IconLocation = "$icon,0"
$lnk.Description = "human-vs-ai"
$lnk.Save()
Write-Host "已创建桌面快捷方式: $desktop\$Name.lnk (icon.ico, 16-256px 多分辨率)"
