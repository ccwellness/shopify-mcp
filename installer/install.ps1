# Shopify Connector MCP — Claude Desktop installer (Windows).
#
# Installs uv if missing, copies the bundled source into a per-user
# install dir, runs `uv sync`, prompts for DATABASE_URL, and merges a
# `shopify-connector` entry into claude_desktop_config.json.

$ErrorActionPreference = "Stop"

$ScriptDir  = $PSScriptRoot
$InstallDir = Join-Path $env:LOCALAPPDATA "shopify-connector-mcp"
$ConfigDir  = Join-Path $env:APPDATA "Claude"
$ConfigPath = Join-Path $ConfigDir "claude_desktop_config.json"

function Write-Step($msg) { Write-Host ">> $msg" -ForegroundColor Cyan }

Write-Step "Shopify Connector MCP installer"
Write-Host "    install dir: $InstallDir"
Write-Host "    config file: $ConfigPath"
Write-Host ""

# ---- 1. uv ------------------------------------------------------------------
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCmd) {
    Write-Step "Installing uv (Astral)"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    # The installer drops uv.exe in %USERPROFILE%\.local\bin and updates the
    # user PATH for new shells, but doesn't refresh the current shell.
    $localBin = Join-Path $env:USERPROFILE ".local\bin"
    if (Test-Path (Join-Path $localBin "uv.exe")) {
        $env:PATH = "$localBin;$env:PATH"
    }
    $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uvCmd) { throw "uv install did not put uv on PATH" }
}
$uv = $uvCmd.Source
Write-Host "    uv: $uv"

# ---- 2. Copy source ---------------------------------------------------------
Write-Step "Copying source to $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
foreach ($item in @("app", "mcp_server")) {
    $src = Join-Path $ScriptDir $item
    $dst = Join-Path $InstallDir $item
    if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
    Copy-Item -Recurse -Force $src $dst
}
Copy-Item -Force (Join-Path $ScriptDir "pyproject.toml") $InstallDir
Copy-Item -Force (Join-Path $ScriptDir "uv.lock") $InstallDir
# `uv sync` reads README.md from pyproject.toml; ship a stub if missing.
$readmeDst = Join-Path $InstallDir "README.md"
if (-not (Test-Path $readmeDst)) {
    "Shopify Connector MCP runtime." | Out-File -FilePath $readmeDst -Encoding utf8
}

# ---- 3. uv sync -------------------------------------------------------------
Write-Step "Resolving dependencies (uv sync) — first run can take a few minutes"
Push-Location $InstallDir
try {
    & $uv sync
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed (exit $LASTEXITCODE)" }
} finally {
    Pop-Location
}

# ---- 4. .env ----------------------------------------------------------------
$envFile = Join-Path $InstallDir ".env"
if (Test-Path $envFile) {
    Write-Step "Keeping existing .env"
} else {
    Write-Step "Configuring database connection"
    Write-Host "Paste the team DATABASE_URL"
    Write-Host "(format: postgresql+psycopg://user:pass@host:5432/dbname)"
    $dbUrl = Read-Host "DATABASE_URL"
    if ([string]::IsNullOrWhiteSpace($dbUrl)) { throw "DATABASE_URL is required" }
    "DATABASE_URL=$dbUrl" | Out-File -FilePath $envFile -Encoding utf8
}

# ---- 5. Merge into Claude Desktop config -----------------------------------
Write-Step "Updating Claude Desktop config"
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null

$mergeScript = Join-Path $ScriptDir "_merge_config.py"
Push-Location $InstallDir
try {
    & $uv run python $mergeScript $ConfigPath $InstallDir $uv
    if ($LASTEXITCODE -ne 0) { throw "config merge failed (exit $LASTEXITCODE)" }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "Restart Claude Desktop fully (right-click tray icon -> Quit, then reopen)."
Write-Host "In a new chat, the tools menu should list 'shopify-connector'."
