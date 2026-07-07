# Frollo

[![CI](https://github.com/arthurtabbal/frollo/actions/workflows/ci.yml/badge.svg)](https://github.com/arthurtabbal/frollo/actions/workflows/ci.yml)

> *"Il observait Paris du haut de Notre-Dame."*

A terminal observability layer for [Claude Code](https://claude.ai/code).

The name carries a double meaning: **Claude** is both the AI model and **Claude Frollo**, the archdeacon who watches over Paris from the heights of Notre-Dame. This project builds the window from which Claude is observed.

---

## Features

- **Passive observer** — hooks into every Claude Code session system-wide via `~/.claude/settings.json`; renders tool calls, edits, and commands with colors and icons as they happen, across all open sessions simultaneously
- **Active client** (`frollo`) — full TUI wrapping `claude`, with typewriter rendering, separate panes for thinking and tool calls, and Paris city art as ambient backdrop
- **Three gargoyles** — Victor (pompous), Hugo (bored, hungry), Gudule (nihilistic) comment on tool calls at 15% probability, always typewritten; their lines live in `bin/characters/*.json`
- **Hellfire spinner** — animated flame gradient while Claude thinks, cycling through colors in the `_F` palette

---

## Requirements

- [Claude Code](https://claude.ai/code) CLI (`claude`)
- `tmux` ≥ 3.1 (the layout uses `split-window -l <percentage>`, added in 3.1)
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
| `/new` | Restart with a fresh context |
| `/model [name]` | Show or switch model (`opus`/`sonnet`/`haiku` or full ID), effective next turn |
| `Shift+Tab` | Toggle Normal ↔ Auto mode |
| `Alt+Enter` | Insert newline (multiline input) |
| `Ctrl+V` | Paste an image from the clipboard |
| `Ctrl+C` | Cancel running turn (or clear line if idle) |
| `Ctrl+D` | Exit |

> **Tip — run Frollo with Sonnet.** Sonnet streams its reasoning into the thinking pane (`display: "summarized"`), which is half the point of Frollo. Opus 4.8/4.7 omit the thinking text at the API level (`display: "omitted"` — only an encrypted `signature` is returned, unrecoverable by the client), so the pane just shows a "thinking omitted" note. If you do use Opus, consider turning off `thinking_autoresize` in the config so the empty pane stays small.

---

## The Gargoyles

Three chimeras from the cathedral — **Victor**, **Hugo**, and **Gudule** — comment on what they observe. Inspired by the chimeras Victor Hugo describes in the novel, which Quasimodo lives among.

- **Victor** (purple) — pompous, theatrical: *"Mon Dieu! Que comando audacioso!"*
- **Hugo** (green) — bored, hungry: *"heh. isso vai dar certo. acho."*
- **Gudule** (lilac) — nihilistic, melancholic: *"mais um."*, *"..."*

They appear in the tools pane (reacting to Bash, Edit, Write, Read) and at turn boundaries (thinking, errors, rate limits, permission prompts). 15% probability per event; error/rate-limit/permission events are forced. Each gargoyle is a JSON file in `bin/characters/` with a `name`, a `color`, and `falas` keyed by event category — adding a fourth requires no code changes.

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
  → claude --print --output-format stream-json --verbose --include-partial-messages
    → text / thinking deltas   →  chat pane + thinking pane (typewriter)
    → tool_use / tool_result   →  tools pane + gargoyles
    → usage / cost             →  stats pane (Rio Sena)
```

Hooks are global (`~/.claude/settings.json`) — the observer captures all Claude Code sessions at once. Events carry `.cwd` and `.session_id` to distinguish them. `async: true` ensures the observed system never waits on the observer.

`runner` and `tools` are packages, not single files; gargoyle lines live in `bin/characters/*.json`.

| File | Lines | Responsibility |
|---|---|---|
| `bin/chat.py` | ~320 | Main TUI loop, commands |
| `bin/lib/runner/` | ~740 | Turn execution: subprocess, streaming, spinner, panes, permissions, stats, typewriter |
| `bin/lib/tools/` | ~190 | Tool call log in the tools pane, per-tool dispatch, nvim jump |
| `bin/lib/input.py` | ~254 | Raw input, cursor, multiline, history, image paste |
| `bin/lib/gargulas.py` | ~91 | Loads the gargoyles from `characters/*.json` |
| `bin/lib/typewriter.py` | ~39 | File typewriter + per-char delay |
| `bin/lib/theme.py` | ~147 | ANSI colors, flames, markdown |
| `bin/lib/session.py` | ~103 | Session picker (`--resume`) |
| `bin/lib/config.py` / `configure.py` | ~127 | Config file + first-run wizard |
| `bin/characters/*.json` | ~640 | Victor, Hugo, Gudule lines by event category |
| `bin/frollo.sh` | ~240 | tmux layout + ASCII art |
| `bin/observe.sh` | ~87 | Passive observer viewer |
| `hooks/log.sh` | 11 | Core hook — `jq -c` + `flock` + `tee -a` |

---

## About the theme

The project is themed exclusively on Victor Hugo's *Notre-Dame de Paris* (1831), which is public domain. The Disney adaptation (1996) is under copyright — no lyrics, no specific visual designs from that version.

---

## License

MIT — see [LICENSE](LICENSE).
