[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$upstreamRoot = Join-Path $projectRoot 'upstream'
$names = @('TrendRadar', 'RSSHub', 'changedetection', 'OpenBB', 'Folo', 'n8n')

$report = foreach ($name in $names) {
    $repositoryPath = Join-Path $upstreamRoot $name
    if (-not (Test-Path -LiteralPath (Join-Path $repositoryPath '.git') -PathType Container)) {
        [pscustomobject]@{ Name = $name; Present = $false; Branch = $null; Commit = $null; Shallow = $null; CommitCount = $null; Remote = $null }
        continue
    }

    [pscustomobject]@{
        Name = $name
        Present = $true
        Branch = (& git -C $repositoryPath branch --show-current)
        Commit = (& git -C $repositoryPath rev-parse HEAD)
        Shallow = (& git -C $repositoryPath rev-parse --is-shallow-repository)
        CommitCount = (& git -C $repositoryPath rev-list --count HEAD)
        Remote = (& git -C $repositoryPath remote get-url origin)
    }
}

$report | Format-Table -AutoSize
