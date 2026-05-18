"""Teste de integração para hooks/log.sh — serialização concorrente com flock."""
import json
import subprocess
import tempfile
import threading
from pathlib import Path

HOOK = Path(__file__).parent.parent / "hooks" / "log.sh"


def _run(payload: dict, home: str):
    subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload),
        text=True,
        env={"HOME": home, "PATH": "/usr/bin:/bin"},
    )


def test_writes_serializados_sem_corrupcao():
    """N threads escrevendo simultaneamente — cada linha deve ser JSON válido."""
    N = 20
    with tempfile.TemporaryDirectory() as tmpdir:
        # log.sh usa $HOME/.claude/observer.jsonl
        claude_dir = Path(tmpdir) / ".claude"
        claude_dir.mkdir()
        log_file = claude_dir / "observer.jsonl"
        log_file.touch()  # flock precisa que o arquivo exista

        threads = [
            threading.Thread(target=_run, args=({"id": i, "data": "x" * 200}, tmpdir))
            for i in range(N)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        lines = log_file.read_text().splitlines()
        assert len(lines) == N, f"esperado {N} linhas, obtido {len(lines)}"

        for i, line in enumerate(lines):
            try:
                obj = json.loads(line)
                assert "id" in obj
                assert "_ts" in obj
            except json.JSONDecodeError as e:
                raise AssertionError(f"linha {i} corrompida: {line!r}") from e
