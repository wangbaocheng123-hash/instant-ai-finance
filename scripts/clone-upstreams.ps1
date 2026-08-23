[CmdletBinding()]
param(
    [switch] $UserApproved,
    [ValidateSet('Ssh443', 'Https')]
    [string] $Transport = 'Ssh443'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $UserApproved) {
    throw '未执行克隆：必须在用户明确批准开始 R0 后传入 -UserApproved。'
}

if ($null -eq (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git 不可用。此脚本不会自动安装 Git。'
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$upstreamRoot = Join-Path $projectRoot 'upstream'
$repositories = [ordered]@{
    TrendRadar = [ordered]@{ Slug = 'sansan0/TrendRadar'; Branch = 'master'; Commit = '8ee26026ba6c11dec41a95fb3895a7162876caa1' }
    RSSHub = [ordered]@{ Slug = 'DIYgod/RSSHub'; Branch = 'master'; Commit = '5151c3233bc7bacfaecc6e4f01aba2b60022d683' }
    changedetection = [ordered]@{ Slug = 'dgtlmoon/changedetection.io'; Branch = 'master'; Commit = 'fce24780e74199bf34c62a0d90188cc2fc12f061' }
    OpenBB = [ordered]@{ Slug = 'OpenBB-finance/OpenBB'; Branch = 'develop'; Commit = '3e071fcc2cd9f891cac6040ae60296dba76dab46' }
    Folo = [ordered]@{ Slug = 'RSSNext/Folo'; Branch = 'dev'; Commit = '7c220c69a841defbfeeb00a86ed75ad482b22a57' }
    n8n = [ordered]@{ Slug = 'n8n-io/n8n'; Branch = 'master'; Commit = '7968432083cdc2526b3b08983d84d0dc73176356' }
}

$gitPrefix = @('-c', 'core.longpaths=true')
if ($Transport -eq 'Ssh443') {
    $sshRoot = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.ssh'
    $identityFile = Join-Path $sshRoot 'id_ed25519_jishi_ai'
    $knownHosts = Join-Path $sshRoot 'known_hosts_jishi_ai'
    if (-not (Test-Path -LiteralPath $identityFile -PathType Leaf)) {
        throw "缺少专用 SSH 私钥: $identityFile"
    }
    if (-not (Test-Path -LiteralPath $knownHosts -PathType Leaf)) {
        throw "缺少已核验的 GitHub 主机指纹文件: $knownHosts"
    }
    $sshCommand = "ssh -4 -o BatchMode=yes -o ConnectTimeout=30 -o ConnectionAttempts=3 -o IPQoS=none -o KexAlgorithms=curve25519-sha256 -o HostKeyAlgorithms=ssh-ed25519 -o StrictHostKeyChecking=yes -o UserKnownHostsFile=`"$knownHosts`" -o IdentitiesOnly=yes -i `"$identityFile`""
    $gitPrefix += @('-c', "core.sshCommand=$sshCommand")
}

foreach ($entry in $repositories.GetEnumerator()) {
    $destination = Join-Path $upstreamRoot $entry.Key
    $metadata = $entry.Value
    $repositoryUrl = if ($Transport -eq 'Ssh443') {
        "ssh://git@ssh.github.com:443/$($metadata.Slug).git"
    }
    else {
        "https://github.com/$($metadata.Slug).git"
    }

    if (Test-Path -LiteralPath $destination) {
        if (-not (Test-Path -LiteralPath (Join-Path $destination '.git') -PathType Container)) {
            throw "目标目录已存在但不是 Git 仓库: $destination"
        }
    }
    else {
        $cloned = $false
        foreach ($attempt in 1..3) {
            Write-Host "轻量克隆 $($entry.Key)（尝试 $attempt/3）..."
            & git @gitPrefix clone --depth 1 --filter=blob:none --no-tags --single-branch --branch $metadata.Branch $repositoryUrl $destination
            if ($LASTEXITCODE -eq 0) {
                $cloned = $true
                break
            }

            if (Test-Path -LiteralPath $destination) {
                $verifiedRoot = [IO.Path]::GetFullPath($upstreamRoot).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
                $verifiedDestination = [IO.Path]::GetFullPath($destination)
                if (-not $verifiedDestination.StartsWith($verifiedRoot, [StringComparison]::OrdinalIgnoreCase)) {
                    throw "拒绝清理不在 upstream 内的失败克隆目录: $verifiedDestination"
                }
                Remove-Item -LiteralPath $verifiedDestination -Recurse -Force
            }
        }
        if (-not $cloned) {
            throw "克隆失败（已重试 3 次）: $($entry.Key)"
        }
    }

    $actualCommit = (& git -C $destination rev-parse HEAD).Trim()
    if ($actualCommit -ne $metadata.Commit) {
        Write-Host "分支头已变化；为 $($entry.Key) 获取锁定提交 ..."
        & git @gitPrefix -C $destination fetch --depth 1 --filter=blob:none --no-tags origin $metadata.Commit
        if ($LASTEXITCODE -ne 0) {
            throw "无法获取锁定提交: $($entry.Key) $($metadata.Commit)"
        }
        & git -C $destination checkout --detach $metadata.Commit
        if ($LASTEXITCODE -ne 0) {
            throw "无法检出锁定提交: $($entry.Key) $($metadata.Commit)"
        }
        $actualCommit = (& git -C $destination rev-parse HEAD).Trim()
    }

    if ($actualCommit -ne $metadata.Commit) {
        throw "提交核验失败: $($entry.Key) expected=$($metadata.Commit) actual=$actualCommit"
    }

    Write-Host "已核验 $($entry.Key): $actualCommit" -ForegroundColor Green
}
