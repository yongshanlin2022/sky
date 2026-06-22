# create_shortcut.ps1
# Run this once in PowerShell to create a Desktop shortcut for the analyzer.
#
# How to run:
#   Right-click create_shortcut.ps1 → "Run with PowerShell"
# OR in PowerShell:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\create_shortcut.ps1

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$LaunchBat  = Join-Path $ScriptDir "launch.bat"
$Desktop    = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "BTC-ETH Analyzer.lnk"

# Find a terminal icon (use cmd.exe icon as fallback)
$IconPath = "C:\Windows\System32\cmd.exe"

$WScript   = New-Object -ComObject WScript.Shell
$Shortcut  = $WScript.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath       = $LaunchBat
$Shortcut.WorkingDirectory = $ScriptDir
$Shortcut.IconLocation     = "$IconPath, 0"
$Shortcut.Description      = "BTC/ETH Bitunix Futures Signal Analyzer"
$Shortcut.WindowStyle      = 1   # Normal window
$Shortcut.Save()

Write-Host ""
Write-Host "  Shortcut created on Desktop: $ShortcutPath" -ForegroundColor Green
Write-Host "  Double-click 'BTC-ETH Analyzer' on your Desktop to launch." -ForegroundColor Cyan
Write-Host ""
