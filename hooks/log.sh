#!/bin/bash
# Recebe evento do Claude Code via stdin e appenda no log do observer

LOG="$HOME/.claude/observer.jsonl"
LOCK="$LOG.lock"
MAX_SIZE=$((10 * 1024 * 1024))  # ~10MB — rotaciona 1 geração (observer.jsonl.1)

input=$(cat)
ts=$(date +"%F %T")
line=$(echo "$input" | jq -c --arg ts "$ts" '. + {_ts: $ts}')

# Lockfile dedicado (não o próprio LOG) — o mv de rotação não pode invalidar
# o fd de quem já segura o lock via inode antigo.
(
    flock 200
    if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt "$MAX_SIZE" ]; then
        mv "$LOG" "$LOG.1"
    fi
    echo "$line" >> "$LOG"
) 200>"$LOCK"

exit 0
