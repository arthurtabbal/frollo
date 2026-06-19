import re
import subprocess


def fetch_usage(timeout=20.0):
    """
    Runs `claude -p /usage` and parses the plain-text quota report.

    Print mode (`-p`) skips the workspace-trust dialog and the external
    CLAUDE.md import prompt that otherwise block an interactive session —
    so no PTY/fork dance is needed. `/usage` is a local slash command, so
    this costs no model tokens. Returns a dict or None on failure.
    """
    try:
        proc = subprocess.run(
            ["claude", "-p", "/usage"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return _parse(proc.stdout)


def _parse(text):
    result = {}

    m = re.search(r'[Cc]urrent\s+session.{0,200}?(\d+)%\s*used', text, re.DOTALL)
    if m:
        result['session_pct'] = int(m.group(1))

    m = re.search(r'[Cc]urrent\s+week.{0,200}?(\d+)%\s*used', text, re.DOTALL)
    if m:
        result['week_pct'] = int(m.group(1))

    # "resets Jun 19, 1:39am (America/Sao_Paulo)" → capture up to the timezone paren
    resets = re.findall(r'resets?\s+([A-Z][a-z]{2}\s+\d+[^\n()]*)', text)
    if resets:
        result['session_reset'] = resets[0].strip().rstrip(' ·')
    if len(resets) >= 2:
        result['week_reset'] = resets[1].strip().rstrip(' ·')

    m = re.search(r'(\d+)%\s+of\s+your\s+usage\s+was\s+at\s+>(\d+)k', text, re.IGNORECASE)
    if m:
        result['heavy_ctx_pct'] = int(m.group(1))
        result['heavy_ctx_threshold'] = int(m.group(2))

    return result if result else None
