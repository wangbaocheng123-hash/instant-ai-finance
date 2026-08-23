[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$researchRoot = Join-Path $projectRoot 'research\projects'
$projects = @('TrendRadar', 'RSSHub', 'changedetection', 'OpenBB', 'Folo', 'n8n')
$requiredFiles = @(
    'SUMMARY.md', 'ARCHITECTURE.md', 'ENTRYPOINTS.md', 'DATA_FLOW.md',
    'MODULE_MAP.md', 'DATABASE_AND_STORAGE.md', 'CONFIGURATION.md',
    'EXTENSION_POINTS.md', 'REUSABLE_COMPONENTS.md', 'WINDOWS_RUNBOOK.md',
    'LICENSE_NOTES.md', 'SECURITY_AND_RISKS.md', 'TEST_RESULTS.md',
    'FINAL_ASSESSMENT.md'
)

$issues = [System.Collections.Generic.List[string]]::new()
foreach ($project in $projects) {
    $directory = Join-Path $researchRoot $project
    foreach ($file in $requiredFiles) {
        $path = Join-Path $directory $file
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            $issues.Add("$project 缺少 $file")
            continue
        }
        if ((Get-Item -LiteralPath $path).Length -lt 80) {
            $issues.Add("$project/$file 内容过短，不能视为完成")
        }
    }
}

if ($issues.Count -gt 0) {
    $issues | ForEach-Object { Write-Host " - $_" -ForegroundColor Yellow }
    exit 1
}

Write-Host '六个项目的规定研究文件均存在且通过最小长度检查。' -ForegroundColor Green

