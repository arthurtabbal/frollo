import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib.runner.models import discover_claude_efforts, discover_claude_models


def _completed(result):
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps({"result": result, "is_error": False})
    proc.stderr = ""
    return proc


def test_claude_models_vem_do_slash_command_nativo():
    result = (
        "Current model: `Opus 5.5`\nUsage: /model <name>. Available: "
        "sonnet, opus, best, sonnet[1m], default, or a full model ID."
    )
    with patch("lib.runner.models.subprocess.run", return_value=_completed(result)) as run:
        models = discover_claude_models("/repo")

    assert [item["model"] for item in models] == [
        "sonnet", "opus", "best", "sonnet[1m]", "default",
    ]
    assert "--no-session-persistence" in run.call_args.args[0]


def test_claude_efforts_vem_do_slash_command_nativo():
    with patch(
        "lib.runner.models.subprocess.run",
        return_value=_completed("Usage: /effort <low|high|max|ultracode|auto>"),
    ):
        assert discover_claude_efforts("/repo") == ["low", "high", "max", "ultracode", "auto"]
