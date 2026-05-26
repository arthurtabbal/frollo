#!/bin/bash
# Unit tests for install.sh — version comparison and parsing logic
set -euo pipefail

_pass=0; _fail=0

_assert() {
    local desc="$1" result="$2"
    if [ "$result" = "0" ]; then
        printf "  [OK]   %s\n" "$desc"; _pass=$(( _pass + 1 ))
    else
        printf "  [FAIL] %s\n" "$desc"; _fail=$(( _fail + 1 ))
    fi
}

_ver_ge() { printf '%s\n%s\n' "$2" "$1" | sort -V -C; }

echo "── _ver_ge ──────────────────────────────────────────"
_assert "3.4 >= 2.6"    "$( _ver_ge 3.4  2.6  && echo 0 || echo 1 )"
_assert "2.6 >= 2.6"    "$( _ver_ge 2.6  2.6  && echo 0 || echo 1 )"
_assert "2.5 < 2.6"     "$( _ver_ge 2.5  2.6  && echo 1 || echo 0 )"
_assert "3.10 >= 3.9"   "$( _ver_ge 3.10 3.9  && echo 0 || echo 1 )"
_assert "3.9 < 3.10"    "$( _ver_ge 3.9  3.10 && echo 1 || echo 0 )"
_assert "1.7 >= 1.6"    "$( _ver_ge 1.7  1.6  && echo 0 || echo 1 )"
_assert "0.10 >= 0.10"  "$( _ver_ge 0.10 0.10 && echo 0 || echo 1 )"
_assert "0.9 < 0.10"    "$( _ver_ge 0.9  0.10 && echo 1 || echo 0 )"

echo ""
echo "── tmux version parser ──────────────────────────────"
_parse_tmux() { echo "$1" | awk '{print $2}' | tr -d 'a-z'; }
_assert '"tmux 3.4a" → "3.4"' "$( [ "$( _parse_tmux 'tmux 3.4a' )" = '3.4' ] && echo 0 || echo 1 )"
_assert '"tmux 3.0a" → "3.0"' "$( [ "$( _parse_tmux 'tmux 3.0a' )" = '3.0' ] && echo 0 || echo 1 )"
_assert '"tmux 2.6"  → "2.6"' "$( [ "$( _parse_tmux 'tmux 2.6'  )" = '2.6' ] && echo 0 || echo 1 )"
_assert '"tmux 3.2a" → "3.2"' "$( [ "$( _parse_tmux 'tmux 3.2a' )" = '3.2' ] && echo 0 || echo 1 )"

echo ""
echo "── jq version parser ────────────────────────────────"
_parse_jq() { echo "$1" | tr -d 'jq-'; }
_assert '"jq-1.7" → "1.7"' "$( [ "$( _parse_jq 'jq-1.7' )" = '1.7' ] && echo 0 || echo 1 )"
_assert '"jq-1.6" → "1.6"' "$( [ "$( _parse_jq 'jq-1.6' )" = '1.6' ] && echo 0 || echo 1 )"

echo ""
echo "── nvim version parser ──────────────────────────────"
_parse_nvim() { echo "$1" | awk '{gsub(/[^0-9.]/,"",$0); print $0}'; }
_assert '"NVIM v0.11.0" → "0.11.0"' "$( [ "$( _parse_nvim 'NVIM v0.11.0' )" = '0.11.0' ] && echo 0 || echo 1 )"
_assert '"NVIM v0.10.4" → "0.10.4"' "$( [ "$( _parse_nvim 'NVIM v0.10.4' )" = '0.10.4' ] && echo 0 || echo 1 )"
_assert '"NVIM v0.9.5"  → "0.9.5"'  "$( [ "$( _parse_nvim 'NVIM v0.9.5'  )" = '0.9.5'  ] && echo 0 || echo 1 )"

echo ""
if [ $_fail -eq 0 ]; then
    echo "✓ $_pass passed"
else
    echo "✗ $_fail failed, $_pass passed"
    exit 1
fi
