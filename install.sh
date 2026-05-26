#!/bin/bash
# Installs Frollo hooks globally and symlinks the frollo command

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK_SCRIPT="$HOME/.claude/hooks/log.sh"
SETTINGS="$HOME/.claude/settings.json"

# Dependency check
missing=()
command -v jq      >/dev/null 2>&1 || missing+=("jq")
command -v tmux    >/dev/null 2>&1 || missing+=("tmux")
command -v python3 >/dev/null 2>&1 || missing+=("python3")
command -v claude  >/dev/null 2>&1 || missing+=("claude  (Claude Code — https://claude.ai/code)")

if [ ${#missing[@]} -gt 0 ]; then
    echo "✗ Missing dependencies:"
    for dep in "${missing[@]}"; do
        printf "    %s\n" "$dep"
    done
    echo ""
    echo "Install the above and re-run ./install.sh"
    exit 1
fi

# Version checks — compare X.Y strings via sort -V
_ver_ge() { printf '%s\n%s\n' "$2" "$1" | sort -V -C; }

# Python >= 3.10
if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
    _pyver=$(python3 -c "import sys; print('%d.%d' % sys.version_info[:2])")
    echo "✗ Python 3.10+ required (found $_pyver)"
    echo "  Ubuntu 22.04+, Debian 12+ and most modern distros ship 3.10 or newer."
    exit 1
fi

# tmux >= 2.6  (select-pane -T and pane-border-status)
_tmuxver=$(tmux -V | awk '{print $2}' | tr -d 'a-z')
if ! _ver_ge "$_tmuxver" "2.6"; then
    echo "✗ tmux 2.6+ required (found $_tmuxver)"
    echo "  Ubuntu 20.04+ ships 3.0a or newer."
    exit 1
fi

# jq >= 1.6  (--unbuffered, --arg)
_jqver=$(jq --version 2>/dev/null | tr -d 'jq-')
if ! _ver_ge "$_jqver" "1.6"; then
    echo "✗ jq 1.6+ required (found $_jqver)"
    echo "  Ubuntu 20.04+ ships 1.6 or newer."
    exit 1
fi

# Install hook script
mkdir -p "$HOME/.claude/hooks"
cp "$REPO_DIR/hooks/log.sh" "$HOOK_SCRIPT"
chmod +x "$HOOK_SCRIPT"
echo "✓ Hook installed at $HOOK_SCRIPT"

# Merge hooks safely — adds each event type only if our command isn't already registered,
# preserving any existing hooks the user may have configured.
mkdir -p "$(dirname "$SETTINGS")"
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"

cp "$SETTINGS" "$SETTINGS.bak"
echo "✓ Backup saved at $SETTINGS.bak"

jq --arg cmd "$HOOK_SCRIPT" '
    def add_if_missing(event; matcher):
        .hooks //= {} |
        .hooks[event] //= [] |
        if (.hooks[event] | map(.hooks // [] | map(.command) | any(. == $cmd)) | any)
        then .
        else .hooks[event] += [{"matcher": matcher, "hooks": [{"type": "command", "command": $cmd, "async": true, "timeout": 5}]}]
        end;
    add_if_missing("PreToolUse"; "*") |
    add_if_missing("PostToolUse"; "*") |
    add_if_missing("Stop"; "") |
    add_if_missing("UserPromptSubmit"; "")
' "$SETTINGS" > "$SETTINGS.tmp"
mv "$SETTINGS.tmp" "$SETTINGS"

echo "✓ Hooks configured in $SETTINGS"

# Install frollo command
mkdir -p "$HOME/.local/bin"
ln -sf "$REPO_DIR/bin/frollo.sh" "$HOME/.local/bin/frollo"
chmod +x "$REPO_DIR/bin/frollo.sh"
echo "✓ Command installed: frollo → $REPO_DIR/bin/frollo.sh"

# Warn if ~/.local/bin is not in PATH
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo ""
    echo "⚠  Add to your shell profile (~/.bashrc or ~/.zshrc):"
    echo '    export PATH="$HOME/.local/bin:$PATH"'
fi

# Optional: nvim + NvChad config (requires nvim >= 0.10)
if command -v nvim >/dev/null 2>&1; then
    _nvimver=$(nvim --version 2>/dev/null | awk 'NR==1{gsub(/[^0-9.]/,"",$0); print $0}')
    echo ""
    printf "Install NvChad config for nvim? This will back up any existing ~/.config/nvim. [y/N] "
    read -r _ans
    if [[ "$_ans" =~ ^[Yy]$ ]]; then
        if ! _ver_ge "$_nvimver" "0.10"; then
            echo "✗ NvChad requires nvim 0.10+ (found $_nvimver) — skipping"
        else
            if [ -d "$HOME/.config/nvim" ]; then
                _bak="$HOME/.config/nvim.bak.$(date +%Y%m%d%H%M%S)"
                mv "$HOME/.config/nvim" "$_bak"
                echo "✓ Backup saved at $_bak"
            fi
            cp -r "$REPO_DIR/conf/nvim" "$HOME/.config/nvim"
            echo "✓ NvChad config installed at ~/.config/nvim"
            echo "  Installing plugins (isso pode demorar um minuto)..."
            nvim --headless "+Lazy! sync" +qa 2>&1 | grep -v "^$" || true
            echo "✓ Plugins instalados"
        fi
    fi
fi

echo ""
echo "Usage:"
echo "  frollo                        # open in current directory"
echo "  frollo /path/to/project       # open in specific project"
echo "  ./bin/observe.sh              # passive observer only"
