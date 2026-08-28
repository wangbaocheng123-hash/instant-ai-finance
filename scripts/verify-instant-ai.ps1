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
if ($health.version -ne '0.7.2') {
    throw "即时 AI 版本不符合手机版与个人云端准备版本: $($health.version)"
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
if (-not $staticText.Contains('当前频道内容') -or
    -not $staticText.Contains('aria-current') -or
    -not $staticText.Contains('behavior:"auto"')) {
    throw '频道整页直切逻辑缺失。'
}
if (-not $staticText.Contains('即时热点') -or
    $staticText.Contains('全球热点') -or
    $staticText.Contains('hotspotTrack')) {
    throw '顶部即时与热点仍未合并为单一即时热点栏。'
}

$staticIndex = Get-Content -LiteralPath (Join-Path $productRoot 'instant_ai\static\index.html') -Raw -Encoding UTF8
$staticStyles = Get-Content -LiteralPath (Join-Path $productRoot 'instant_ai\static\styles.css') -Raw -Encoding UTF8
$staticManifest = Join-Path $productRoot 'instant_ai\static\manifest.webmanifest'
$staticWorker = Join-Path $productRoot 'instant_ai\static\sw.js'
if (-not $staticIndex.Contains('manifest.webmanifest') -or -not $staticStyles.Contains('mobile-dock')) {
    throw '手机版入口或底部快捷频道缺失。'
}
if (-not $staticStyles.Contains('header-tools') -or -not $staticStyles.Contains('.finance-panel[hidden]')) {
    throw '紧凑状态工具条或单频道页面样式缺失。'
}
if (-not (Test-Path -LiteralPath $staticManifest -PathType Leaf) -or -not (Test-Path -LiteralPath $staticWorker -PathType Leaf)) {
    throw '可添加到手机主屏的清单或离线外壳缺失。'
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
$mobileShortcut = 'C:\Users\36590\Desktop\即时 AI（手机预览）.lnk'
if (-not (Test-Path -LiteralPath $mobileShortcut -PathType Leaf)) {
    throw "手机预览快捷方式不存在: $mobileShortcut"
}

Write-Host '即时 AI 运行验收通过。' -ForegroundColor Green
Write-Host "数据库: $database"
Write-Host "情报条数: $($status.items.total)"
Write-Host "已缓存中文标题: $($translationStatus.cached_titles)"
Write-Host "待处理重要提醒: $($status.notifications.pending)"
