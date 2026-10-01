# Publish a new StockWidget version to everyone who has the exe.
# Usage:  .\release.ps1 1.0.1 "what changed"
# Steps:  bump APP_VERSION -> build dist\StockWidget.exe -> upload as a GitHub Release.
# Running widgets check at startup and every 30 minutes, then update and restart themselves.
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Notes = "Update"
)
$ErrorActionPreference = "Continue"  # native tools log to stderr; failures are checked via $LASTEXITCODE
Set-Location $PSScriptRoot
$repo = "hyojun956/stock-widget"
$src = Join-Path $PSScriptRoot "stock_widget.pyw"

# 1) bump version in source (keep UTF-8 without BOM)
$utf8 = New-Object System.Text.UTF8Encoding($false)
$code = [IO.File]::ReadAllText($src, $utf8)
$code = [regex]::Replace($code, 'APP_VERSION = "[^"]*"', "APP_VERSION = `"$Version`"")
[IO.File]::WriteAllText($src, $code, $utf8)

# 2) build exe
$work = Join-Path $env:TEMP "stockwidget-build"
python -m PyInstaller --onefile --noconsole --name StockWidget --distpath dist `
    --workpath $work --specpath $work --noconfirm stock_widget.pyw
if ($LASTEXITCODE -ne 0) { throw "build failed" }

# 3) version.json = what running widgets poll (asset URLs avoid GitHub API rate limits)
$size = (Get-Item "dist\StockWidget.exe").Length
[IO.File]::WriteAllText((Join-Path $PSScriptRoot "dist\version.json"),
    "{`"version`": `"$Version`", `"size`": $size}", $utf8)

# 4) publish release (asset names must stay StockWidget.exe / version.json)
gh release create "v$Version" "dist\StockWidget.exe" "dist\version.json" --repo $repo --title "v$Version" --notes $Notes
if ($LASTEXITCODE -ne 0) { throw "release upload failed" }
Write-Host "Released v$Version - widgets will offer the update automatically."
