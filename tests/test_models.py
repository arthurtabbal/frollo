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
    resolved = {
        "sonnet": "Set model to `Sonnet 5.5` for this session only",
        "opus": "Set model to `Opus 5.5` for this session only",
        "best": "Set model to `Opus 5.5 (default)` for this session only",
        "sonnet[1m]": "Set model to `Sonnet 5.5` for this session only",
        "default": "Set model to `Opus 5.5 (default)` for this session only",
    }

    def command_result(argv, **_kwargs):
        command = argv[2]
        if command == "/model":
            return _completed(result)
        return _completed(resolved[command.removeprefix("/model ")])

    with patch("lib.runner.models.subprocess.run", side_effect=command_result) as run:
        models = discover_claude_models("/repo")

    assert [item["model"] for item in models] == [
        "sonnet", "opus", "best", "sonnet[1m]", "default",
    ]
    assert [item["display_name"] for item in models] == [
        "Sonnet 5.5", "Opus 5.5", "best · Opus 5.5 (default)",
        "sonnet[1m] · Sonnet 5.5", "default · Opus 5.5 (default)",
    ]
    assert all("--no-session-persistence" in call.args[0] for call in run.call_args_list)


def test_claude_efforts_vem_do_slash_command_nativo():
    with patch(
        "lib.runner.models.subprocess.run",
        return_value=_completed("Usage: /effort <low|high|max|ultracode|auto>"),
    ):
        assert discover_claude_efforts("/repo") == ["low", "high", "max", "ultracode", "auto"]
