# Build the redistributable installer zip for the Shopify Connector MCP
# server. Output: dist\shopify-connector-mcp-installer.zip.

$ErrorActionPreference = "Stop"

$RepoRoot   = Resolve-Path (Join-Path $PSScriptRoot "..")
$Stage      = Join-Path $env:TEMP "shopify-connector-mcp-installer-build"
$DistDir    = Join-Path $RepoRoot "dist"
$ZipOut     = Join-Path $DistDir "shopify-connector-mcp-installer.zip"

Write-Host ">> Building installer zip" -ForegroundColor Cyan
Write-Host "    repo:  $RepoRoot"
Write-Host "    stage: $Stage"
Write-Host "    out:   $ZipOut"

# ---- 1. Reset staging dir ---------------------------------------------------
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
if (Test-Path $ZipOut) { Remove-Item -Force $ZipOut }

# ---- 2. Copy runtime sources ------------------------------------------------
Copy-Item -Recurse -Force (Join-Path $RepoRoot "app") $Stage
Copy-Item -Recurse -Force (Join-Path $RepoRoot "mcp_server") $Stage
Copy-Item -Force (Join-Path $RepoRoot "pyproject.toml") $Stage
Copy-Item -Force (Join-Path $RepoRoot "uv.lock") $Stage

# ---- 3. Strip generated artifacts ------------------------------------------
Get-ChildItem -Path $Stage -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force
Get-ChildItem -Path $Stage -Recurse -File -Include "*.pyc", "*.pyo" |
    Remove-Item -Force

# ---- 4. Copy installer files (flat at zip root) ----------------------------
$installerDir = Join-Path $RepoRoot "installer"
foreach ($f in @("install.ps1", "install.sh", "_merge_config.py", "README.md", "env.example")) {
    Copy-Item -Force (Join-Path $installerDir $f) $Stage
}

# ---- 5. Zip -----------------------------------------------------------------
# -Force on Compress-Archive doesn't overwrite reliably on some Windows
# builds, hence the explicit Remove-Item above.
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $ZipOut

$sizeMb = [math]::Round((Get-Item $ZipOut).Length / 1MB, 2)
Write-Host ""
Write-Host "Built $ZipOut ($sizeMb MB)" -ForegroundColor Green
Write-Host "Send this zip to your coworker. Inside, they run install.ps1 (Windows) or install.sh (macOS)."
