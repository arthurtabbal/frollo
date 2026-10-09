"""Validação de argumentos que precisa acontecer antes da criação do tmux."""

import subprocess
from pathlib import Path


ROOT = Path(__file__).parent.parent
FROLLO = ROOT / "bin" / "frollo.sh"


def _run(*args):
    return subprocess.run(
        ["bash", str(FROLLO), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_backed_sugere_backend_antes_de_abrir_tmux():
    result = _run("--resume", "--backed", "codex")

    assert result.returncode == 2
    assert "você quis dizer --backend?" in result.stdout


def test_backend_sem_valor_falha_antes_de_abrir_tmux():
    result = _run("--backend")

    assert result.returncode == 2
    assert "--backend exige um valor" in result.stdout
