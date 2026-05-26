# Frollo

> *"Il observait Paris du haut de Notre-Dame."*

A terminal observability layer for [Claude Code](https://claude.ai/code).

The name carries a double meaning: **Claude** is both the AI model and **Claude Frollo**, the archdeacon who watches over Paris from the heights of Notre-Dame. This project builds the window from which Claude is observed.

---

## Features

- **Passive observer** — hooks into every Claude Code session system-wide via `~/.claude/settings.json`; renders tool calls, edits, and commands with colors and icons as they happen, across all open sessions simultaneously
- **Active client** (`frollo`) — full TUI wrapping `claude`, with typewriter rendering, separate panes for thinking and tool calls, and Paris city art as ambient backdrop
- **Three gargoyles** — Victor (pompous), Hugo (bored, hungry), Gudule (nihilistic) comment on tool calls at 30% probability, always typewritten
- **Hellfire spinner** — animated flame gradient while Claude thinks, cycling through colors in the `_F` palette

---

## Requirements

- [Claude Code](https://claude.ai/code) CLI (`claude`)
- `tmux` ≥ 3.0
- Python 3.10+
- `jq` 1.6+
- `nvim` (optional — used in the editor pane)

---

## Install

```bash
git clone https://github.com/arthurtabbal/frollo
cd frollo
./install.sh
```

`install.sh` installs the hook script and configures it in `~/.claude/settings.json` (merging safely with any existing hooks), then symlinks `frollo` into `~/.local/bin`.

If `~/.local/bin` is not in your `PATH`, add this to `~/.bashrc` or `~/.zshrc`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

---

## Usage

### Full layout

```bash
frollo                        # open in current directory
frollo /path/to/project       # open in specific project
```

Opens a tmux session with six panes:

```
┌──── nvim editor ───────────────────┬──── thinking ─────────────────────┐
│                                    │    céu · lua                       │
│              60%                   ├──── chat ──────────────────────────┤
│                                    ├──── stats (Rio Sena) ──────────────┤
├──── terminal ──────────────────────├──── tools ─────────────────────────┤
│            30%                     │    paris urbana                    │
└────────────────────────────────────┴────────────────────────────────────┘
```

### Passive observer only

```bash
./bin/observe.sh
```

Tails `~/.claude/observer.jsonl` and renders tool calls from all Claude Code sessions running on the machine. Each event is tagged with `[project]` and timestamp. No layout, no client — just the event stream.

### Client commands

| Key / Command | Effect |
|---|---|
| `/snapshot` | Capture current visual state and send to the agent |
| `/paste` | Open `$EDITOR` for long text; sends on close |
| `/refresh` | Restart, resuming current session |
| `Shift+Tab` | Toggle Normal ↔ Auto mode |
| `Alt+Enter` | Insert newline (multiline input) |
| `Ctrl+C` | Cancel running turn (or clear line if idle) |
| `Ctrl+D` | Exit |

---

## The Gargoyles

Three chimeras from the cathedral — **Victor**, **Hugo**, and **Gudule** — comment on what they observe. Inspired by the chimeras Victor Hugo describes in the novel, which Quasimodo lives among.

- **Victor** (purple) — pompous, theatrical: *"Mon Dieu! Que comando audacioso!"*
- **Hugo** (green) — bored, hungry: *"heh. isso vai dar certo. acho."*
- **Gudule** (lilac) — nihilistic, melancholic: *"mais um."*, *"..."*

They appear in the tools pane (reacting to Bash, Edit, Write, Read) and above the thinking spinner. 30% probability per event, with a cooldown timer.

---

## Architecture

```
Passive observer:
Claude Code (any session)
  → PreToolUse / PostToolUse hooks (async)
    → hooks/log.sh  →  ~/.claude/observer.jsonl
      → bin/observe.sh  (tail -n 0 -f | jq)

Active client:
bin/chat.py
  → claude --print --output-format stream-json --verbose
    → text / thinking blocks  →  chat pane + thinking pane
    → tool_use events         →  tools pane + gargoyles
```

Hooks are global (`~/.claude/settings.json`) — the observer captures all Claude Code sessions at once. Events carry `.cwd` and `.session_id` to distinguish them. `async: true` ensures the observed system never waits on the observer.

| File | Lines | Responsibility |
|---|---|---|
| `bin/chat.py` | ~250 | Main TUI loop, commands |
| `bin/lib/runner.py` | ~360 | Turn execution, subprocess, spinner |
| `bin/lib/tools.py` | ~110 | Tool call log in the tools pane |
| `bin/lib/input.py` | ~195 | Raw input, cursor, multiline |
| `bin/lib/gargulas.py` | ~565 | The three gargoyles |
| `bin/lib/theme.py` | ~88 | ANSI colors, flames, markdown |
| `bin/lib/session.py` | ~103 | Session picker (`--resume`) |
| `bin/frollo.sh` | ~75 | tmux layout |
| `bin/observe.sh` | ~87 | Passive observer viewer |
| `hooks/log.sh` | 11 | Core hook — `jq -c` + `flock` + `tee -a` |

---

## About the theme

The project is themed exclusively on Victor Hugo's *Notre-Dame de Paris* (1831), which is public domain. The Disney adaptation (1996) is under copyright — no lyrics, no specific visual designs from that version.

---

## License

MIT — see [LICENSE](LICENSE).
