# Shopify Connector MCP — Claude Desktop Installer

Adds the `shopify-connector` MCP server to Claude Desktop so you can ask
Claude about Shopify orders, products, inventory, KPIs, and subscriptions
across the team's stores.

## Prerequisites

- **Claude Desktop** installed (https://claude.ai/download)
- **DATABASE_URL** for the team Postgres — grab it from the team password
  manager. Looks like `postgresql+psycopg://user:pass@host:5432/dbname`
- An internet connection (the installer will download `uv` and ~200 MB of
  Python dependencies on first run)

You do **not** need Python, git, or any IDE installed — `uv` handles that.

## Install

### Windows

Right-click `install.ps1` → **Run with PowerShell**, or from a PowerShell
prompt in this folder:

```powershell
.\install.ps1
```

If you get `cannot be loaded because running scripts is disabled`, run this
once and try again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### macOS

From Terminal in this folder:

```sh
bash install.sh
```

## What the installer does

1. Installs `uv` to `~/.local/bin` if you don't already have it.
2. Copies the bundled source into a per-user install dir:
   - Windows: `%LOCALAPPDATA%\shopify-connector-mcp`
   - macOS: `~/Library/Application Support/shopify-connector-mcp`
3. Runs `uv sync` to materialize the Python venv with all dependencies.
4. Prompts for `DATABASE_URL` and writes it to `.env` in the install dir.
5. Merges a `shopify-connector` entry into Claude Desktop's
   `claude_desktop_config.json`, preserving any other MCP servers you have.

Existing config is preserved — re-running the installer is safe and will
just refresh the source/deps.

## After install

**Restart Claude Desktop fully** — close isn't enough:

- Windows: right-click tray icon → **Quit**
- macOS: **Cmd+Q**

Then start a new chat. The tools menu (hammer icon) should list
`shopify-connector`. Try:

> show daily kpis for lubelife for the last 30 days

> what's the lowest-stock SKU on shopjo right now?

> compare revenue between lubelife and shopjo for last month

## Troubleshooting

**The server fails to start.** Check
`%APPDATA%\Claude\logs\mcp-server-shopify-connector.log` (Windows) or
`~/Library/Logs/Claude/mcp-server-shopify-connector.log` (macOS). The most
common cause is a typo in `DATABASE_URL` or a VPN that isn't connected —
edit `.env` in the install dir to fix.

**uv install was blocked.** Some corporate machines block the Astral
install script. Install `uv` manually
(https://docs.astral.sh/uv/getting-started/installation/) and re-run.

**"Database is unreachable."** Confirm you can `psql` to the host from your
machine (VPN if needed). The MCP server is read-only on the DB; no schema
changes are made.

## Uninstall

1. Edit `claude_desktop_config.json` and delete the `shopify-connector`
   entry under `mcpServers`.
2. Delete the install dir.
