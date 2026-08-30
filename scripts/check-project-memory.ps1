[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function ConvertFrom-Utf8Base64([string]$Value) {
    return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Value))
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$requiredFiles = @(
    'AGENTS.md',
    'PROJECT_CHARTER.md',
    'STATUS.md',
    'ROADMAP.md',
    'TASKS.md',
    'CHANGELOG.md',
    'UPSTREAM_LOCK.yaml',
    'LICENSE',
    'README.md',
    'docs/FOUNDATION_DIRECTIVE_R0.md',
    'docs/MEMORY_PROTOCOL.md',
    'docs/decisions/ADR-0003-single-user-no-account-layer.md',
    'docs/decisions/ADR-0005-worldmonitor-presentation-fork.md',
    'docs/decisions/ADR-0006-cached-headline-translation.md',
    'docs/decisions/ADR-0007-light-ui-thumbnail-cache.md',
    'docs/decisions/ADR-0008-automatic-collection-article-images-client-window.md',
    'docs/decisions/ADR-0010-ephemeral-hot-event-radar.md',
    'docs/decisions/ADR-0011-public-passwordless-additive-cloud-entry.md',
    'docs/decisions/ADR-0012-grandpaamu-custom-domain.md',
    'docs/decisions/ADR-0013-on-demand-chinese-reader.md',
    'docs/decisions/ADR-0014-browser-native-original-translation.md',
    'docs/decisions/ADR-0015-ios-direct-chrome-handoff.md',
    'docs/decisions/ADR-0016-time-compass-watch-events.md',
    'docs/decisions/ADR-0017-official-event-channel-monitoring.md',
    'docs/decisions/ADR-0018-official-signal-compass-codex-handoff.md',
    'docs/decisions/ADR-0019-cloud-codex-publish-channel.md',
    'docs/decisions/ADR-0020-single-owner-thirty-day-session.md',
    'docs/decisions/ADR-0021-model-mr-sanitized-module.md',
    'docs/decisions/ADR-0022-model-mr-owner-mobile-library.md',
    'docs/decisions/ADR-0023-model-mr-mobile-interaction-parity.md',
    'deploy/aliyun/check-publish-channel.sh',
    'deploy/aliyun/update-model-mr-owner-metadata.sh',
    'deploy/aliyun/install-codex-publish-channel.sh',
    'deploy/aliyun/compassdev-instant-ai-publish.sudoers',
    'research/REUSE_DECISION.md',
    'research/MVP_BLUEPRINT.md',
    'research/WORLDMONITOR_FINANCE_FORK.md',
    'config/storage.paths.example.toml',
    'client/instant-ai/README.md'
)

$libraryRoot = ConvertFrom-Utf8Base64 'SDpc5Y2z5pe2QUnmlofku7blupM='
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
    @{ Path = 'AGENTS.md'; Text = ConvertFrom-Utf8Base64 'MzAg5aSp5a6J5YWo5Lya6K+d' },
    @{ Path = 'PROJECT_CHARTER.md'; Text = ConvertFrom-Utf8Base64 '5Y2V5Li75Lq644CB5peg5rOo5YaM44CB5peg5aSa55So5oi35L2T57O7' },
    @{ Path = 'docs/decisions/ADR-0020-single-owner-thirty-day-session.md'; Text = ConvertFrom-Utf8Base64 '5LiN5bu656uL55So5oi36KGo' },
    @{ Path = 'docs/decisions/ADR-0022-model-mr-owner-mobile-library.md'; Text = 'single-owner-mobile-library' },
    @{ Path = 'STATUS.md'; Text = 'LOCAL_0_15_1_VERIFIED' },
    @{ Path = 'STATUS.md'; Text = 'https://grandpaamu.com/' },
    @{ Path = 'PROJECT_CHARTER.md'; Text = 'ADR-0010' },
    @{ Path = 'AGENTS.md'; Text = ConvertFrom-Utf8Base64 '5LiN5b6X5omT5byA5oiW5o6n5Yi2IENocm9tZeOAgUVkZ2XjgIHlhoXnva7mtY/op4jlmajmiJbpmL/ph4zkupHnvZHpobXmjqfliLblj7A=' },
    @{ Path = 'AGENTS.md'; Text = 'sudo -n /usr/local/sbin/instant-ai-publish' },
    @{ Path = 'STATUS.md'; Text = 'CODEX_CLOUD_PUBLISH_READY' }
)

foreach ($relativePath in $requiredFiles) {
    $fullPath = Join-Path $projectRoot $relativePath
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
        $issues.Add("Missing project memory file: $relativePath")
    }
}

foreach ($rule in $requiredMemoryRules) {
    $fullPath = Join-Path $projectRoot $rule.Path
    if ((Test-Path -LiteralPath $fullPath -PathType Leaf) -and
        -not (Get-Content -LiteralPath $fullPath -Raw -Encoding UTF8).Contains($rule.Text)) {
        $issues.Add("Required project rule is missing or changed: $($rule.Path)")
    }
}

foreach ($directory in $requiredLibraryDirs) {
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        $issues.Add("Missing runtime library directory: $directory")
    }
}

$gitCommand = Get-Command git -ErrorAction SilentlyContinue
if ($null -eq $gitCommand) {
    $issues.Add('Git is unavailable; repository leakage cannot be checked.')
}
else {
    $candidateFiles = @(& git -C $projectRoot ls-files --cached --others --exclude-standard)
    foreach ($candidateFile in $candidateFiles) {
        $normalized = $candidateFile -replace '\\', '/'
        $isUpstreamSource = $normalized -match '^upstream/(?!README\.md$)'
        $isSensitiveOrData = $normalized -match '(?i)(^|/)(\.env($|\.)|\.venv/|venv/|node_modules/|data/|downloads/|cache/|logs/|.*cookie.*|.*credential.*|.*secret.*|id_(rsa|dsa|ecdsa|ed25519).*|.*\.(db|sqlite3?|log|pem|key|zip|7z|tar|gz)$)'
        if ($isUpstreamSource -or $isSensitiveOrData) {
            $issues.Add("File must not enter Git scope: $candidateFile")
        }
    }
}

if ($issues.Count -gt 0) {
    Write-Host 'Instant AI project-memory check failed:' -ForegroundColor Red
    foreach ($issue in $issues) {
        Write-Host " - $issue" -ForegroundColor Red
    }
    exit 1
}

Write-Host 'Instant AI project-memory check passed.' -ForegroundColor Green
Write-Host "Project root: $projectRoot"
Write-Host "Runtime library: $libraryRoot"
