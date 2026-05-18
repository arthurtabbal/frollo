#!/bin/bash
# Viewer: tail do log de eventos do Claude Code com formatação via jq

LOG="$HOME/.claude/observer.jsonl"
touch "$LOG"

E=$'\e'
tail -n 0 -f "$LOG" | jq -r --unbuffered \
  --arg dim    "${E}[2m"          \
  --arg reset  "${E}[0m"          \
  --arg green  "${E}[38;5;108m"  \
  --arg yellow "${E}[38;5;222m"  \
  --arg cyan   "${E}[38;5;110m"  \
  --arg blue   "${E}[38;5;67m"   \
  --arg purple "${E}[38;5;60m"   \
  --arg white  "${E}[38;5;253m"  \
  --arg grey   "${E}[2;37m"      \
  '
  def dim(s):    $dim    + s + $reset;
  def green(s):  $green  + s + $reset;
  def yellow(s): $yellow + s + $reset;
  def cyan(s):   $cyan   + s + $reset;
  def blue(s):   $blue   + s + $reset;
  def purple(s): $purple + s + $reset;
  def grey(s):   $dim    + s + $reset;
  def white(s):  $white  + s + $reset;
  def muted(s):  $grey   + s + $reset;

  def relpath(cwd):
    if cwd != null and startswith(cwd + "/") then "." + .[(cwd|length):]
    elif startswith(env.HOME + "/") then "~" + .[env.HOME|length:]
    else . end;

  def duration:
    if . >= 1000 then ((. / 1000 * 10 | round) / 10 | tostring) + "s"
    else tostring + "ms" end;

  .cwd as $cwd |
  (.cwd | split("/") | last) as $proj |

  if .hook_event_name == "PreToolUse" then
    dim(._ts) + "  " + grey("[" + $proj + "]") + "  " + (
      if .tool_name == "Bash" then
        green("⚡") + "  " + (
          if .tool_input.description and (.tool_input.description | length) > 0
          then .tool_input.description
          else (.tool_input.command | gsub("\n"; " ") | if length > 90 then .[0:90] + "…" else . end)
          end
        )
      elif .tool_name == "Read" then
        cyan("◎") + "  " + (.tool_input.file_path | relpath($cwd))
      elif .tool_name == "Glob" then
        cyan("◌") + "  " + (.tool_input.pattern // "*")
      elif .tool_name == "Edit" then
        yellow("✎") + "  " + (.tool_input.file_path | relpath($cwd))
      elif .tool_name == "Write" then
        yellow("✎") + "  " + (.tool_input.file_path | relpath($cwd))
      elif .tool_name == "Agent" then
        purple("◈") + "  " + (.tool_input.description // .tool_name)
      elif .tool_name == "WebFetch" then
        blue("↓") + "  " + (.tool_input.url // .tool_name)
      elif .tool_name == "WebSearch" then
        blue("↓") + "  " + (.tool_input.query // .tool_name)
      elif (.tool_name | startswith("mcp__")) then
        blue("◉") + "  " + .tool_name
      else
        grey("→") + "  " + .tool_name
      end
    )

  elif .hook_event_name == "PostToolUse" and .tool_name == "Bash" then
    dim(._ts) + "  " + grey("[" + $proj + "]") + "  " + grey("└─") + "  " + (
      (.duration_ms | duration) +
      (if (.tool_response.stderr // "") != "" then "  " + yellow("⚠") else "" end)
    )

  elif .hook_event_name == "UserPromptSubmit" then
    dim(._ts) + "  " + grey("[" + $proj + "]") + "  " + white("▶") + "  " +
    white((.prompt // "") | gsub("\n"; " ") | if length > 120 then .[0:120] + "…" else . end)

  elif .hook_event_name == "Stop" then
    dim(._ts) + "  " + grey("[" + $proj + "]") + "  " + muted("◀") + "  " +
    muted(((.last_assistant_message // "") | gsub("\n"; " ") | if length > 120 then .[0:120] + "…" else . end))

  else empty
  end
'
