[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$launcher = Join-Path $projectRoot 'product\launch_mobile_preview.py'
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "缺少即时 AI 手机预览启动器: $launcher"
}

$pythonw = (Get-Command pythonw -ErrorAction Stop).Source
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop '即时 AI（手机预览）.lnk'
$edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + $launcher + '"'
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = '即时 AI — 在电脑上按手机尺寸预览实时财经新闻'
if (Test-Path -LiteralPath $edge -PathType Leaf) {
    $shortcut.IconLocation = "$edge,0"
}
$shortcut.Save()

Write-Host "手机预览快捷方式已创建: $shortcutPath" -ForegroundColor Green
