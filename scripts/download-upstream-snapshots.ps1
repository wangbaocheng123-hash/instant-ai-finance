[CmdletBinding()]
param(
    [switch] $UserApproved
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $UserApproved) {
    throw '未执行下载：必须在用户明确批准开始 R0 后传入 -UserApproved。'
}

if ($null -eq (Get-Command tar -ErrorAction SilentlyContinue)) {
    throw '系统 tar 不可用；此脚本不会自动安装解压工具。'
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$upstreamRoot = (Resolve-Path (Join-Path $projectRoot 'upstream')).Path
$archiveRoot = 'H:\即时AI文件库\cache\upstream-archives'
New-Item -ItemType Directory -Path $archiveRoot -Force | Out-Null

$items = @(
    @{ Name = 'TrendRadar'; Repository = 'sansan0/TrendRadar'; Commit = '8ee26026ba6c11dec41a95fb3895a7162876caa1' },
    @{ Name = 'RSSHub'; Repository = 'DIYgod/RSSHub'; Commit = '5151c3233bc7bacfaecc6e4f01aba2b60022d683' },
    @{ Name = 'changedetection'; Repository = 'dgtlmoon/changedetection.io'; Commit = 'fce24780e74199bf34c62a0d90188cc2fc12f061' },
    @{ Name = 'OpenBB'; Repository = 'OpenBB-finance/OpenBB'; Commit = '3e071fcc2cd9f891cac6040ae60296dba76dab46' },
    @{ Name = 'Folo'; Repository = 'RSSNext/Folo'; Commit = '7c220c69a841defbfeeb00a86ed75ad482b22a57' },
    @{ Name = 'n8n'; Repository = 'n8n-io/n8n'; Commit = '7968432083cdc2526b3b08983d84d0dc73176356' }
)

$headers = @{ 'User-Agent' = 'ImmediateAI-R0-Research' }
foreach ($item in $items) {
    $destination = Join-Path $upstreamRoot ($item.Name + '-snapshot')
    $archive = Join-Path $archiveRoot ($item.Name + '-' + $item.Commit + '.tar.gz')

    if (Test-Path -LiteralPath $destination) {
        Write-Warning "快照目录已存在，未覆盖: $destination"
        continue
    }

    if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
        $uri = "https://api.github.com/repos/$($item.Repository)/tarball/$($item.Commit)"
        Write-Host "从 GitHub 官方 API 下载 $($item.Name)@$($item.Commit) ..."
        Invoke-WebRequest -Uri $uri -Headers $headers -OutFile $archive -MaximumRedirection 5 -TimeoutSec 600
    }

    New-Item -ItemType Directory -Path $destination | Out-Null
    Write-Host "解压 $($item.Name) 到明确标注的源码快照目录 ..."
    & tar -xzf $archive -C $destination --strip-components=1
    if ($LASTEXITCODE -ne 0) {
        throw "解压失败: $($item.Name)。保留目录和归档供人工检查，未自动删除。"
    }

    $hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
    $sizeMB = [math]::Round((Get-Item -LiteralPath $archive).Length / 1MB, 2)
    Write-Host "完成 $($item.Name)：归档 ${sizeMB} MiB，SHA256 $hash"
}

Write-Warning '这些目录是 GitHub 官方提交归档快照，不是 Git 克隆；不得把它们记为 clone_mode=git。'

