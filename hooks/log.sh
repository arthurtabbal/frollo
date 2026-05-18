#!/bin/bash
# Recebe evento do Claude Code via stdin e appenda no log do observer

LOG="$HOME/.claude/observer.jsonl"

input=$(cat)
ts=$(date +"%H:%M:%S")

echo "$input" | jq -c --arg ts "$ts" '. + {_ts: $ts}' | flock "$LOG" tee -a "$LOG" > /dev/null

exit 0
