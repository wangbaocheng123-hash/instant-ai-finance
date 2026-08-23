[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

function Get-ToolVersion {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string[]] $Arguments
    )

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        return 'NOT_FOUND'
    }

    try {
        $result = & $Name @Arguments 2>&1 | Select-Object -First 1
        return [string]$result
    }
    catch {
        return "ERROR: $($_.Exception.Message)"
    }
}

$fileSystemDrives = Get-PSDrive -PSProvider FileSystem | ForEach-Object {
    [pscustomobject]@{
        Drive = $_.Name
        Root = $_.Root
        FreeGB = if ($null -ne $_.Free) { [math]::Round($_.Free / 1GB, 2) } else { $null }
    }
}

[pscustomobject]@{
    CheckedAt = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz')
    PowerShell = $PSVersionTable.PSVersion.ToString()
    Git = Get-ToolVersion -Name 'git' -Arguments @('--version')
    Python = Get-ToolVersion -Name 'python' -Arguments @('--version')
    Node = Get-ToolVersion -Name 'node' -Arguments @('--version')
    Npm = Get-ToolVersion -Name 'npm' -Arguments @('--version')
    Pnpm = Get-ToolVersion -Name 'pnpm' -Arguments @('--version')
    Yarn = Get-ToolVersion -Name 'yarn' -Arguments @('--version')
    Docker = Get-ToolVersion -Name 'docker' -Arguments @('--version')
    Wsl = Get-ToolVersion -Name 'wsl' -Arguments @('--status')
    Drives = $fileSystemDrives
} | ConvertTo-Json -Depth 4

