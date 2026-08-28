[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$productRoot = Join-Path $projectRoot 'product'
$database = Join-Path ([string]::Concat('H:\', [char]0x5373, [char]0x65F6, 'AI', [char]0x6587, [char]0x4EF6, [char]0x5E93)) 'database\instant_ai.db'

Push-Location $productRoot
try {
    & python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw 'Instant AI Python tests failed.'
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $database -PathType Leaf)) {
    throw "Production database is missing: $database"
}

$health = Invoke-RestMethod -Uri 'http://127.0.0.1:18765/api/health' -TimeoutSec 5
if (-not $health.ok) {
    throw 'Instant AI health check failed.'
}
if ($health.version -ne '0.8.2') {
    throw "Unexpected Instant AI version: $($health.version)"
}

$status = Invoke-RestMethod -Uri 'http://127.0.0.1:18765/api/status' -TimeoutSec 5
if ($status.retention.archive_enabled -ne $false -or $status.retention.critical_days -ne 7) {
    throw 'The short-lived hot-event retention policy is not active.'
}
if ($status.database_path -ne $database) {
    throw "Unexpected database path: $($status.database_path)"
}
if ($status.collection.mode -ne 'automatic' -or $status.collection.interval_seconds -ne 300) {
    throw 'Automatic five-minute collection is not active.'
}

$staticApp = Join-Path $productRoot 'instant_ai\static\app.js'
$staticText = Get-Content -LiteralPath $staticApp -Raw -Encoding UTF8
if (-not $staticText.Contains('/api/hot?limit=')) {
    throw 'The combined hot-event API is missing from the client.'
}
if (-not $staticText.Contains('aria-current') -or -not $staticText.Contains('behavior:"auto"')) {
    throw 'Single-channel navigation is missing from the client.'
}

$staticIndex = Get-Content -LiteralPath (Join-Path $productRoot 'instant_ai\static\index.html') -Raw -Encoding UTF8
$staticStyles = Get-Content -LiteralPath (Join-Path $productRoot 'instant_ai\static\styles.css') -Raw -Encoding UTF8
$staticManifest = Join-Path $productRoot 'instant_ai\static\manifest.webmanifest'
$staticWorker = Join-Path $productRoot 'instant_ai\static\sw.js'
if (-not $staticIndex.Contains('manifest.webmanifest') -or -not $staticStyles.Contains('mobile-dock')) {
    throw 'The mobile entry point or mobile dock is missing.'
}
if (-not $staticStyles.Contains('header-tools') -or -not $staticStyles.Contains('.finance-panel[hidden]')) {
    throw 'The compact header or single-channel style is missing.'
}
if (-not (Test-Path -LiteralPath $staticManifest -PathType Leaf) -or -not (Test-Path -LiteralPath $staticWorker -PathType Leaf)) {
    throw 'The web-app manifest or service worker is missing.'
}

$aiStatus = Invoke-RestMethod -Uri 'http://127.0.0.1:18765/api/ai/status' -TimeoutSec 5
if (-not $aiStatus.contract_version) {
    throw 'The AI evidence contract is unavailable.'
}

$translationStatus = Invoke-RestMethod -Uri 'http://127.0.0.1:18765/api/translation/status' -TimeoutSec 5
if (-not $translationStatus.enabled -or -not $translationStatus.target_language) {
    throw 'The title translation service is unavailable.'
}

$sampleItems = @(Invoke-RestMethod -Uri 'http://127.0.0.1:18765/api/items?limit=1' -TimeoutSec 5)
if ($sampleItems.Count -ne 1 -or -not $sampleItems[0].thumbnail_url) {
    throw 'The sample news item has no thumbnail URL.'
}
$thumbnail = Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:18765" + $sampleItems[0].thumbnail_url) -TimeoutSec 45
$thumbnailContentType = [string]$thumbnail.Headers.'Content-Type'
if (-not $thumbnailContentType.StartsWith('image/')) {
    throw 'The thumbnail endpoint did not return an image.'
}

$notifications = Invoke-RestMethod -Uri 'http://127.0.0.1:18765/api/notifications' -TimeoutSec 5
if ($null -eq $notifications) {
    throw 'The notification outbox is unavailable.'
}

$shortcutDirectory = [Environment]::GetFolderPath('Desktop')
$shortcuts = @(Get-ChildItem -LiteralPath $shortcutDirectory -Filter '*.lnk' -File)
if ($shortcuts.Count -lt 2) {
    throw 'The desktop and mobile-preview shortcuts are missing.'
}

Write-Host 'Instant AI runtime verification passed.' -ForegroundColor Green
Write-Host "Database: $database"
Write-Host "Active-window items: $($status.items.total)"
Write-Host "Cached translated titles: $($translationStatus.cached_titles)"
Write-Host "Pending important alerts: $($status.notifications.pending)"
