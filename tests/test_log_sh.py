"""Teste de integração para hooks/log.sh — serialização concorrente com flock."""
import json
import re
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
                # ISO completo (%F %T) — não só HH:MM:SS
                assert re.match(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$', obj["_ts"]), obj["_ts"]
            except json.JSONDecodeError as e:
                raise AssertionError(f"linha {i} corrompida: {line!r}") from e


def test_rotaciona_quando_log_excede_tamanho_maximo():
    """Log > ~10MB deve virar observer.jsonl.1 antes do append seguinte."""
    with tempfile.TemporaryDirectory() as tmpdir:
        claude_dir = Path(tmpdir) / ".claude"
        claude_dir.mkdir()
        log_file = claude_dir / "observer.jsonl"
        log_file.write_bytes(b"x" * (11 * 1024 * 1024))  # > MAX_SIZE

        _run({"id": "novo"}, tmpdir)

        rotated = claude_dir / "observer.jsonl.1"
        assert rotated.exists()
        assert rotated.stat().st_size >= 11 * 1024 * 1024

        lines = log_file.read_text().splitlines()
        assert len(lines) == 1
        obj = json.loads(lines[0])
        assert obj["id"] == "novo"
