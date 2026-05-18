#!/bin/bash
# Abre layout tmux: pane esquerdo (claude, 65%) + pane direito (observer, 35%)

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OBSERVE="$REPO_DIR/bin/observe.sh"
LOG="$HOME/.claude/observer.jsonl"

> "$LOG"

if [ -z "$TMUX" ]; then
    COLS=$(tput cols 2>/dev/null || echo 220)
    ROWS=$(tput lines 2>/dev/null || echo 50)
    tmux new-session -d -s claude-obs -x "$COLS" -y "$ROWS" \; \
        split-window -h -l "35%" "$OBSERVE" \; \
        select-pane -L
    if [ $# -gt 0 ]; then
        tmux send-keys -t claude-obs "claude $(printf '%q ' "$@")" Enter
    fi
    tmux attach-session -t claude-obs
else
    tmux split-window -h -l "35%" "$OBSERVE"
    tmux select-pane -L
    if [ $# -gt 0 ]; then
        tmux send-keys "claude $(printf '%q ' "$@")" Enter
    fi
fi
