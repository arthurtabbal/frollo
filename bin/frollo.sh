#!/bin/bash
# Layout tmux para o claude client
#
#  ┌──── nvim-tree + editor ────────────┬─── thinking ──────────┐
#  │                                   │     céu · lua          │
#  │               60%                 ├──── chat ──────────────┤
#  │                                   ├──── stats ─────────────┤
#  ├──── terminal ─────────────────────├──── tools ─────────────┤
#  │           30%                     │     paris urbana        │
#  └───────────────────────────────────┴────────────────────────┘

REPO_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
CLIENT="$REPO_DIR/bin/chat.py"

command -v tmux   >/dev/null 2>&1 || { echo "erro: tmux não encontrado — instale com: sudo apt install tmux"; exit 1; }
command -v claude >/dev/null 2>&1 || { echo "erro: claude CLI não encontrado — instale com: npm i -g @anthropic-ai/claude-code"; exit 1; }

# Primeiro arg sem "-" é o diretório do projeto; o resto vai pro chat.py
PROJ_DIR="$(pwd)"
if [[ $# -gt 0 && "${1:0:1}" != "-" ]]; then
    PROJ_DIR="$(realpath "$1")"
    shift
fi

RUNDIR="/tmp/claude-client"
mkdir -p "$RUNDIR"
> "$RUNDIR/thinking"
> "$RUNDIR/tools"

SRV="claude-$$"       # servidor tmux efêmero único por invocação
TMUX_CONF="$REPO_DIR/conf/tmux.conf"

cleanup() {
    tmux -L "$SRV" kill-server >/dev/null 2>&1 || true
}
trap cleanup EXIT

COLS=$(tput cols 2>/dev/null || echo 220)
ROWS=$(tput lines 2>/dev/null || echo 50)

# Pane inicial: coluna esquerda (placeholder — vira nvim depois)
tmux -L "$SRV" -f "$TMUX_CONF" new-session -d -x "$COLS" -y "$ROWS" -s claude -n main "exec \$SHELL"
P_LEFT=$(tmux -L "$SRV" display-message -t "claude:main" -p "#{pane_id}")

# Coluna direita: chat (40% da largura, altura total)
tmux -L "$SRV" split-window -h -l "40%" -t "$P_LEFT" \
    "cd '$PROJ_DIR' && CLAUDE_TMUX_SRV='$SRV' CLAUDE_NVIM_PANE='$P_LEFT' CLAUDE_EDITOR_BIN='$_editor_bin' python3 '$CLIENT'$( [[ $# -gt 0 ]] && printf ' %q' "$@")"
P_CHAT=$(tmux -L "$SRV" display-message -t "claude:main" -p "#{pane_id}")

# Tools: base da coluna direita (26% do total — calibrado pelo layout manual)
tmux -L "$SRV" split-window -v -l "26%" -t "$P_CHAT" \
    "tail -n 0 -f $RUNDIR/tools 2>/dev/null; exec \$SHELL"
P_TOOLS=$(tmux -L "$SRV" display-message -t "claude:main" -p "#{pane_id}")
tmux -L "$SRV" display-message -t "$P_TOOLS" -p "#{pane_tty}" > "$RUNDIR/tools_tty"

# Stats: exatamente 2 linhas, entre chat e tools
tmux -L "$SRV" split-window -v -l 2 -t "$P_CHAT" \
    "stty -echo; tail -n 0 -f /dev/null"
P_STATS=$(tmux -L "$SRV" display-message -t "claude:main" -p "#{pane_id}")
tmux -L "$SRV" display-message -t "$P_STATS" -p "#{pane_tty}" > "$RUNDIR/stats_tty"

# Thinking: tamanho idle desde o início (mesmo cálculo do runner.py)
ROWS=$(tmux -L "$SRV" display-message -t "claude:main" -p "#{window_height}" 2>/dev/null || tput lines)
IDLE_THINK=$(( ROWS * 16 / 100 ))
[ "$IDLE_THINK" -lt 8 ] && IDLE_THINK=8
tmux -L "$SRV" split-window -v -b -l "$IDLE_THINK" -t "$P_CHAT" \
    "stty -echo; tail -n 0 -f $RUNDIR/thinking 2>/dev/null"
P_THINKING=$(tmux -L "$SRV" display-message -t "claude:main" -p "#{pane_id}")
tmux -L "$SRV" display-message -t "$P_THINKING" -p "#{pane_tty}" > "$RUNDIR/thinking_tty"
echo "$P_THINKING" > "$RUNDIR/thinking_pane"
echo "$P_CHAT"     > "$RUNDIR/chat_pane"
echo "$P_TOOLS"    > "$RUNDIR/tools_pane"
echo "$P_STATS"    > "$RUNDIR/stats_pane"

# Coluna esquerda: terminal na base (26% — alinhado com tools)
tmux -L "$SRV" split-window -v -l "26%" -t "$P_LEFT" \
    "cd '$PROJ_DIR' && exec \$SHELL"
P_TERMINAL=$(tmux -L "$SRV" display-message -t "claude:main" -p "#{pane_id}")

# Títulos dos panes
tmux -L "$SRV" select-pane -t "$P_LEFT"     -T "◈ editor"
tmux -L "$SRV" select-pane -t "$P_CHAT"     -T "▲ chat"
tmux -L "$SRV" select-pane -t "$P_THINKING" -T "◎ thinking"
tmux -L "$SRV" select-pane -t "$P_TOOLS"    -T "⚡ tools"
tmux -L "$SRV" select-pane -t "$P_STATS"    -T "〰 stats"
tmux -L "$SRV" select-pane -t "$P_TERMINAL" -T "$ terminal"

# Arte ASCII inicial — céu noturno (thinking) e Paris urbana (tools)
_RS=$'\e[0m'
_ST=$'\e[2;38;5;153m'   # estrelas — azul pálido dim
_AM=$'\e[38;5;222m'     # âmbar — lua
_CY=$'\e[38;5;110m'     # cyan — nuvens (cor do thinking)
_BL=$'\e[38;5;67m'      # azul — base das nuvens
_SL=$'\e[38;5;241m'     # pedra clara — fachadas
_SD=$'\e[38;5;238m'     # pedra escura — sombras e telhados
_CB=$'\e[38;5;236m'     # calçada

THINKING_TTY=$(cat "$RUNDIR/thinking_tty")
TOOLS_TTY=$(cat "$RUNDIR/tools_tty")
STATS_TTY=$(cat "$RUNDIR/stats_tty")

# Céu noturno — colorização do sky.txt por tipo de caractere
_sky() {
    local _O=$'\e[38;5;111m'    # azul claro  — estrelas grandes  o
    local _P=$'\e[38;5;75m'     # azul médio  — estrelas           +
    local _X=$'\e[38;5;68m'     # azul escuro — estrelas médias    *
    local _D=$'\e[2;38;5;240m'  # cinza faint — pontos ínfimos     .
    local _AM=$'\e[38;5;222m'   # âmbar       — lua
    local R=$'\e[0m'
    local _q="'"
    while IFS= read -r line; do
        # protege padrões da lua antes de colorir chars individuais
        line="${line//.-./__MOON1__}"
        line="${line//) )/__MOON2__}"
        line="${line//\'-´/__MOON3__}"
        # coloriza estrelas e pontos
        line="${line//o/${_O}o${R}}"
        line="${line//+/${_P}+${R}}"
        line="${line//\*/${_X}*${R}}"
        line="${line//./${_D}.${R}}"
        # restaura lua em âmbar
        line="${line//__MOON1__/${_AM}.-.${R}}"
        line="${line//__MOON2__/${_AM}) )${R}}"
        line="${line//__MOON3__/${_AM}${_q}-´${R}}"
        printf '%s\n' "$line"
    done <<'SKYEOF'
  o          .  '                  +     +       '                     '    +
      ++    .              . '                                           '
             .                                             o        o .-.
o         '   '        +                       .           '           ) )
     .        +                      .              o               ' '-´    .
                            *  +       +' o            * .                .
SKYEOF
}
{ printf '\033[H'; printf '%s' "$(_sky)"; } > "$THINKING_TTY" 2>/dev/null || true

# Rio Sena — 2 linhas de água com barco (stats pane)
_river() {
    # ciclo de tons — profundidade → reflexo de estrela
    local -a _WC=(
        $'\e[38;5;18m'   # noite funda
        $'\e[38;5;24m'   # azul escuro
        $'\e[38;5;24m'   # azul escuro
        $'\e[38;5;31m'   # azul médio
        $'\e[38;5;18m'   # noite funda
        $'\e[38;5;24m'   # azul escuro
        $'\e[38;5;67m'   # reflexo de estrela
        $'\e[38;5;24m'   # azul escuro
    )
    local R=$'\e[0m'
    local ci=0
    while IFS= read -r line; do
        local out="" i=0
        while [ $i -lt ${#line} ]; do
            local ch="${line:$i:1}"
            if [ "$ch" = "~" ]; then
                out+="${_WC[$((ci % 8))]}~${R}"; ((ci++))
            else
                out+="$ch"
            fi
            ((i++))
        done
        printf '%s\n' "$out"
    done <<'RIVEREOF'
 ~~ ~ ~ ~~~ ~~  ~~ ~~~~~~~~~~ ~ \   \ ~~ ~ ~~~~ ~ ~ ~~~~ ~ ~~ ~~~ ~
   ~ ~~~~~~~~ ~ ~ ~~ ~ ~ ~ ~ ~~~ \___\    ~~ ~~  ~~ ~ ~~ ~ ~~~~ ~ ~~
RIVEREOF
}
{ printf '\033[H'; printf '%s' "$(_river)"; } > "$STATS_TTY" 2>/dev/null || true

# Paris urbana — cores noturnas, janelas com padrão acesa/apagada
_paris() {
    local _FAR=$'\e[38;5;18m'    # silhueta distante — azul noturno escuro
    local _NEAR=$'\e[38;5;60m'   # pedra noturna — azul-cinza
    local _DIM=$'\e[38;5;17m'    # janela apagada — buraco escuro
    local _LIT=$'\e[38;5;166m'   # janela acesa — laranja discreto
    local _RS2=$'\e[0m'
    # período primo (31) — nunca sincroniza com grupos de 3 janelas
    local -a _PAT=(0 0 1 0 0 0 0 0 0 0 1 0 0 0 0 0 0 1 0 0 0 0 1 0 0 0 0 0 0 0 0)
    local oi=0 n=0
    while IFS= read -r line; do
        ((n++))
        local _SL2
        [ $n -le 4 ] && _SL2="$_FAR" || _SL2="$_NEAR"
        local out="" i=0
        while [ $i -lt ${#line} ]; do
            local ch="${line:$i:1}"
            if [ "$ch" = "o" ]; then
                [ "${_PAT[$((oi % 10))]}" = "1" ] \
                    && out+="${_LIT}o${_SL2}" \
                    || out+="${_DIM}o${_SL2}"
                ((oi++))
            elif [ "$ch" = '"' ]; then
                out+="${_LIT}\"${_SL2}"
            else
                out+="$ch"
            fi
            ((i++))
        done
        printf "${_SL2}%s${_RS2}\n" "$out"
    done <<'CITYEOF'
___       _____     ___              _____   ___        ___   _____     ___
|[ ]|     |. . .|   |[ ]|  ___      |. . .| |[ ]|  ___ |[ ]| |. . .|  : |[ ]|
|[]]|  ___|. . .|   |[]]| |[ ]| ___ |. . .| |[]]| |[ ]||[]]| |. . .|___|[]]|___
|]_||_|_|_|_._.__|___|]_|_|[]]||[ ]||_._.__|]_|__|]|[]]||]_|_|_._..__|_|_|]_||[ ]|
    _____|"|___    .----.    _____|"|___      .----.    _____|"|___
   |o o o|.|o o|  |    | ___|o o o|.|o o|  ___|    |   |o o o|.|o o|    ___
___|o o o|.|o o|  |[ ] ||"|_|o o o|.|o o| |.| |[ ] |___|o o o|.|o o|____|"|___
|.||o o o|.|o o|__|[ ] ||.| |o o o|.|o o|_|.| |[ ] ||.||o o o|.|o o||.||o o o|.|o
|_||_____|.|___||_|____||_|_|_____|.|___|-|_|_|____||_||_____|.|___||_||_____|.|
CITYEOF
}
_paris > "$TOOLS_TTY" 2>/dev/null || true

# Editor no topo da coluna esquerda — nvim com nvim-tree se disponível, senão $EDITOR
if command -v nvim >/dev/null 2>&1; then
    _editor_bin="nvim"
    _editor_cmd="nvim -c \"lua vim.defer_fn(function() require('nvim-tree.api').tree.open() end, 100)\""
else
    _editor_bin="${EDITOR:-nano}"
    _editor_cmd="$_editor_bin"
fi
tmux -L "$SRV" send-keys -t "$P_LEFT" "cd '$PROJ_DIR' && $_editor_cmd" Enter

tmux -L "$SRV" bind-key q kill-server
tmux -L "$SRV" select-pane -t "$P_CHAT"
tmux -L "$SRV" attach -t claude || true
