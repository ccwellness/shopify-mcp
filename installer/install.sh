#!/usr/bin/env bash
# Shopify Connector MCP — Claude Desktop installer (macOS).
#
# Installs uv if missing, copies the bundled source into a per-user
# install dir, runs `uv sync`, prompts for DATABASE_URL, and merges a
# `shopify-connector` entry into claude_desktop_config.json.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/Library/Application Support/shopify-connector-mcp"
CONFIG_DIR="$HOME/Library/Application Support/Claude"
CONFIG_PATH="$CONFIG_DIR/claude_desktop_config.json"

step() { printf "\n>> %s\n" "$*"; }

step "Shopify Connector MCP installer"
echo "    install dir: $INSTALL_DIR"
echo "    config file: $CONFIG_PATH"

# ---- 1. uv ------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
    step "Installing uv (Astral)"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # The installer drops uv in ~/.local/bin and updates the shell profile,
    # but the running shell doesn't see it yet.
    export PATH="$HOME/.local/bin:$PATH"
fi
UV="$(command -v uv)"
echo "    uv: $UV"

# ---- 2. Copy source ---------------------------------------------------------
step "Copying source to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
for item in app mcp_server; do
    rm -rf "$INSTALL_DIR/$item"
    cp -R "$SCRIPT_DIR/$item" "$INSTALL_DIR/"
done
cp "$SCRIPT_DIR/pyproject.toml" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/uv.lock" "$INSTALL_DIR/"
# `uv sync` reads README.md from pyproject.toml; ship a stub if missing.
[ -f "$INSTALL_DIR/README.md" ] || echo "Shopify Connector MCP runtime." > "$INSTALL_DIR/README.md"

# ---- 3. uv sync -------------------------------------------------------------
step "Resolving dependencies (uv sync) — first run can take a few minutes"
( cd "$INSTALL_DIR" && "$UV" sync )

# ---- 4. .env ----------------------------------------------------------------
ENV_FILE="$INSTALL_DIR/.env"
if [ -f "$ENV_FILE" ]; then
    step "Keeping existing .env"
else
    step "Configuring database connection"
    echo "Paste the team DATABASE_URL"
    echo "(format: postgresql+psycopg://user:pass@host:5432/dbname)"
    printf "DATABASE_URL: "
    read -r DB_URL
    if [ -z "$DB_URL" ]; then
        echo "DATABASE_URL is required" >&2
        exit 1
    fi
    printf "DATABASE_URL=%s\n" "$DB_URL" > "$ENV_FILE"
fi

# ---- 5. Merge into Claude Desktop config -----------------------------------
step "Updating Claude Desktop config"
mkdir -p "$CONFIG_DIR"
( cd "$INSTALL_DIR" && "$UV" run python "$SCRIPT_DIR/_merge_config.py" "$CONFIG_PATH" "$INSTALL_DIR" "$UV" )

echo ""
echo "Done."
echo "Restart Claude Desktop fully (Cmd+Q, then reopen)."
echo "In a new chat, the tools menu should list 'shopify-connector'."
