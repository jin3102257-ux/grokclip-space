# GrokClip Space · Windows 安装（仅当前用户，无需管理员，不联网）
# 用法（在本目录打开 PowerShell）：
#   powershell -ExecutionPolicy Bypass -File .\install_windows.ps1            创建「开始」菜单快捷方式并启动
#   powershell -ExecutionPolicy Bypass -File .\install_windows.ps1 -Startup   额外设置开机自启
#   powershell -ExecutionPolicy Bypass -File .\install_windows.ps1 -Uninstall 移除快捷方式（不删除收集的数据）
param(
  [switch]$Startup,
  [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Programs = [Environment]::GetFolderPath('Programs')
$StartupDir = [Environment]::GetFolderPath('Startup')
$LinkName = 'GrokClip Space.lnk'
$MenuLink = Join-Path $Programs $LinkName
$StartupLink = Join-Path $StartupDir $LinkName

if ($Uninstall) {
  Remove-Item -LiteralPath $MenuLink, $StartupLink -ErrorAction SilentlyContinue
  Write-Host "✓ 已移除快捷方式。收集的数据仍在 $env:APPDATA\GrokClipSpace（需要时可手动删除）。"
  exit 0
}

# 找到 pythonw.exe（优先 py 启动器）
$probe = 'import sys, os, tkinter; assert sys.version_info >= (3, 9); print(os.path.join(os.path.dirname(sys.executable), "pythonw.exe"))'
$pythonw = $null
foreach ($cmd in @(@('py', '-3'), @('python'))) {
  try {
    $exe = $cmd[0]
    $args_ = @($cmd | Select-Object -Skip 1) + @('-c', $probe)
    $out = & $exe @args_ 2>$null
    if ($LASTEXITCODE -eq 0 -and $out -and (Test-Path -LiteralPath $out.Trim())) { $pythonw = $out.Trim(); break }
  } catch { }
}
if (-not $pythonw) {
  Write-Host '✗ 未找到可用的 Python 3.9+（需要带 Tkinter）。'
  Write-Host '  请从 Python 官网免费安装：https://www.python.org/downloads/windows/'
  Write-Host '  安装时勾选 "Add python.exe to PATH" 和 "tcl/tk and IDLE"，然后重新运行本脚本。'
  exit 1
}
Write-Host "✓ Python：$pythonw"

# 自检（生成示例空间，不启动界面）
$python = Join-Path (Split-Path -Parent $pythonw) 'python.exe'
$selftest = Join-Path $env:TEMP 'GrokSpace_selftest.html'
Push-Location $Here
try {
  & $python -m grokclip --self-test $selftest
  if ($LASTEXITCODE -ne 0) { Write-Host '✗ 自检失败'; exit 1 }
} finally { Pop-Location }

function New-GrokClipShortcut([string]$Path) {
  $shell = New-Object -ComObject WScript.Shell
  $lnk = $shell.CreateShortcut($Path)
  $lnk.TargetPath = $pythonw
  $lnk.Arguments = '-m grokclip'
  $lnk.WorkingDirectory = $Here
  $lnk.Description = 'GrokClip Space：收集 Grok Bot 对话原文与文件，一键生成离线 HTML 空间'
  $lnk.IconLocation = "$pythonw,0"
  $lnk.Save()
}

New-GrokClipShortcut $MenuLink
Write-Host "✓ 已创建「开始」菜单快捷方式：$MenuLink"
if ($Startup) {
  New-GrokClipShortcut $StartupLink
  Write-Host "✓ 已设置开机自启：$StartupLink"
}

Start-Process -FilePath $pythonw -ArgumentList '-m', 'grokclip' -WorkingDirectory $Here
Write-Host '✓ 已启动。屏幕右侧会出现紫色悬浮球：单击打开收集篮，右键打开菜单。'
