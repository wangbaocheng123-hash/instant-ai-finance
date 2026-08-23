[CmdletBinding(SupportsShouldProcess)]
param(
    [switch] $UserApproved
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$experimentsRoot = (Resolve-Path (Join-Path $projectRoot 'experiments')).Path
$artifactNames = @('node_modules', '.venv', 'venv', 'dist', 'build', '.next', '.cache', '__pycache__')

$targets = Get-ChildItem -LiteralPath $experimentsRoot -Directory -Recurse -Force | Where-Object {
    $artifactNames -contains $_.Name
}

if ($targets.Count -eq 0) {
    Write-Host '没有发现可清理的实验构建产物。'
    return
}

$targets | Select-Object FullName
if (-not $UserApproved) {
    Write-Host '仅列出目标，未删除。确认后使用 -UserApproved；可同时使用 -WhatIf 预演。'
    return
}

foreach ($target in $targets) {
    $resolvedTarget = $target.FullName
    if (-not $resolvedTarget.StartsWith($experimentsRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理实验目录之外的路径: $resolvedTarget"
    }
    if ($PSCmdlet.ShouldProcess($resolvedTarget, '递归删除构建产物')) {
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }
}

