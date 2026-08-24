[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$productRoot = Join-Path $projectRoot 'product'
$database = 'H:\即时AI文件库\database\instant_ai.db'

Push-Location $productRoot
try {
    & python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw '即时 AI Python 测试失败。'
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $database -PathType Leaf)) {
    throw "正式数据库不存在: $database"
}

$health = Invoke-RestMethod -Uri 'http://127.0.0.1:18765/api/health' -TimeoutSec 5
if (-not $health.ok) {
    throw '即时 AI 健康检查失败。'
}
if ($health.version -ne '0.6.0') {
    throw "即时 AI 版本不符合自动采集与新闻原图版本: $($health.version)"
}

$status = Invoke-RestMethod -Uri 'http://127.0.0.1:18765/api/status' -TimeoutSec 5
if ($status.database_path -ne $database) {
    throw "数据库路径不符合约定: $($status.database_path)"
}
if ($status.collection.mode -ne 'automatic' -or $status.collection.interval_seconds -ne 300) {
    throw '自动实时采集状态或五分钟调度缺失。'
}

$staticApp = Join-Path $productRoot 'instant_ai\static\app.js'
$staticText = Get-Content -LiteralPath $staticApp -Raw -Encoding UTF8
if ($staticText.Contains('立即采集') -or -not $staticText.Contains('自动实时采集')) {
    throw '客户端仍存在手动采集入口，或自动采集状态缺失。'
}

$aiStatus = Invoke-RestMethod -Uri 'http://127.0.0.1:18765/api/ai/status' -TimeoutSec 5
if (-not $aiStatus.contract_version) {
    throw 'AI 证据接口状态缺失。'
}

$translationStatus = Invoke-RestMethod -Uri 'http://127.0.0.1:18765/api/translation/status' -TimeoutSec 5
if (-not $translationStatus.enabled -or -not $translationStatus.target_language) {
    throw '标题汉化接口状态缺失。'
}

$sampleItems = @(Invoke-RestMethod -Uri 'http://127.0.0.1:18765/api/items?limit=1' -TimeoutSec 5)
if ($sampleItems.Count -ne 1 -or -not $sampleItems[0].thumbnail_url) {
    throw '新闻缩略图地址缺失。'
}
$thumbnail = Invoke-WebRequest -Uri ("http://127.0.0.1:18765" + $sampleItems[0].thumbnail_url) -TimeoutSec 45
$thumbnailContentType = [string]$thumbnail.Headers.'Content-Type'
if (-not $thumbnailContentType.StartsWith('image/')) {
    throw '新闻缩略图接口没有返回图片。'
}

$notifications = Invoke-RestMethod -Uri 'http://127.0.0.1:18765/api/notifications' -TimeoutSec 5
if ($null -eq $notifications) {
    throw '通知 outbox 接口不可用。'
}

$shortcut = 'C:\Users\36590\Desktop\即时 AI.lnk'
if (-not (Test-Path -LiteralPath $shortcut -PathType Leaf)) {
    throw "桌面快捷方式不存在: $shortcut"
}

Write-Host '即时 AI 运行验收通过。' -ForegroundColor Green
Write-Host "数据库: $database"
Write-Host "情报条数: $($status.items.total)"
Write-Host "已缓存中文标题: $($translationStatus.cached_titles)"
Write-Host "待处理重要提醒: $($status.notifications.pending)"
