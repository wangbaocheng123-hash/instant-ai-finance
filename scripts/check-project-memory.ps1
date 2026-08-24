[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$requiredFiles = @(
    'AGENTS.md',
    'PROJECT_CHARTER.md',
    'STATUS.md',
    'ROADMAP.md',
    'TASKS.md',
    'CHANGELOG.md',
    'UPSTREAM_LOCK.yaml',
    'docs/FOUNDATION_DIRECTIVE_R0.md',
    'docs/MEMORY_PROTOCOL.md',
    'docs/decisions/ADR-0003-single-user-no-account-layer.md',
    'docs/decisions/ADR-0005-worldmonitor-presentation-fork.md',
    'docs/decisions/ADR-0006-cached-headline-translation.md',
    'docs/decisions/ADR-0007-light-ui-thumbnail-cache.md',
    'docs/decisions/ADR-0008-automatic-collection-article-images-client-window.md',
    'research/REUSE_DECISION.md',
    'research/MVP_BLUEPRINT.md',
    'research/WORLDMONITOR_FINANCE_FORK.md',
    'config/storage.paths.example.toml'
)

$libraryRoot = 'H:\即时AI文件库'
$requiredLibraryDirs = @(
    $libraryRoot,
    (Join-Path $libraryRoot 'raw'),
    (Join-Path $libraryRoot 'evidence'),
    (Join-Path $libraryRoot 'database'),
    (Join-Path $libraryRoot 'exports'),
    (Join-Path $libraryRoot 'backups'),
    (Join-Path $libraryRoot 'cache'),
    (Join-Path $libraryRoot 'logs')
)

$issues = [System.Collections.Generic.List[string]]::new()

$requiredMemoryRules = @(
    @{ Path = 'AGENTS.md'; Text = '永久按单机单用户设计' },
    @{ Path = 'PROJECT_CHARTER.md'; Text = '单机、单用户、无账户体系' },
    @{ Path = 'docs/decisions/ADR-0003-single-user-no-account-layer.md'; Text = '不建立用户表、用户偏好中心或多租户数据隔离抽象' },
    @{ Path = 'research/MVP_BLUEPRINT.md'; Text = '不为这些能力预留 schema/API' }
)

foreach ($relativePath in $requiredFiles) {
    $fullPath = Join-Path $projectRoot $relativePath
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
        $issues.Add("缺少项目记忆文件: $relativePath")
    }
}

foreach ($rule in $requiredMemoryRules) {
    $fullPath = Join-Path $projectRoot $rule.Path
    if ((Test-Path -LiteralPath $fullPath -PathType Leaf) -and
        -not (Get-Content -LiteralPath $fullPath -Raw -Encoding UTF8).Contains($rule.Text)) {
        $issues.Add("永久单用户规则缺失或被改写: $($rule.Path)")
    }
}

foreach ($directory in $requiredLibraryDirs) {
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        $issues.Add("缺少 H 盘文件库目录: $directory")
    }
}

$gitCommand = Get-Command git -ErrorAction SilentlyContinue
if ($null -eq $gitCommand) {
    $issues.Add('Git 不可用，无法检查版本控制泄漏。')
}
else {
    $candidateFiles = @(& git -C $projectRoot ls-files --cached --others --exclude-standard)
    foreach ($candidateFile in $candidateFiles) {
        $normalized = $candidateFile -replace '\\', '/'
        $isUpstreamSource = $normalized -match '^upstream/(?!README\.md$)'
        $isSensitiveOrData = $normalized -match '(?i)(^|/)(\.env($|\.)|\.venv/|venv/|node_modules/|data/|downloads/|cache/|logs/|.*cookie.*|.*credential.*|.*secret.*|id_(rsa|dsa|ecdsa|ed25519).*|.*\.(db|sqlite3?|log|pem|key|zip|7z|tar|gz)$)'
        if ($isUpstreamSource -or $isSensitiveOrData) {
            $issues.Add("不应进入 Git 范围: $candidateFile")
        }
    }
}

if ($issues.Count -gt 0) {
    Write-Host '即时 AI 项目记忆检查失败:' -ForegroundColor Red
    foreach ($issue in $issues) {
        Write-Host " - $issue" -ForegroundColor Red
    }
    exit 1
}

Write-Host '即时 AI 项目记忆检查通过。' -ForegroundColor Green
Write-Host "项目根目录: $projectRoot"
Write-Host "业务文件库: $libraryRoot"
