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


def _fake_bin(tmp_path):
    """PATH com tmux/codex/claude falsos: o tmux só registra os argumentos recebidos."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "tmux.log"
    (bindir / "tmux").write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> '{log}'\n"
        "[[ \"$*\" == *pane_tty* ]] && echo /dev/null && exit 0\n"
        "[[ \"$*\" == *display-message* ]] && echo '%1'\n"
        "exit 0\n"
    )
    for name in ("codex", "claude"):
        (bindir / name).write_text("#!/usr/bin/env bash\nexit 0\n")
    for f in bindir.iterdir():
        f.chmod(0o755)
    return bindir, log


def _chat_pane_command(tmp_path, *args):
    bindir, log = _fake_bin(tmp_path)
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "FROLLO_CONFIG": str(tmp_path / "config.json"),
        "TERM": "dumb",
    }
    result = subprocess.run(
        ["bash", str(FROLLO), *args], cwd=tmp_path, env=env,
        text=True, capture_output=True, check=False, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = log.read_text().splitlines()
    return next(c for c in calls if "chat_pane.sh" in c)


def test_codex_vira_backend_codex_no_chat(tmp_path):
    cmd = _chat_pane_command(tmp_path, "--codex", "--resume")

    assert "--backend codex --resume" in cmd
    assert "--codex" not in cmd


def test_argumentos_comuns_seguem_intactos_para_o_chat(tmp_path):
    cmd = _chat_pane_command(tmp_path, "--backend", "codex", "--model", "gpt 6")

    assert "--backend codex --model gpt\\ 6" in cmd


def test_codex_sem_cli_falha_antes_de_abrir_tmux(tmp_path):
    bindir, log = _fake_bin(tmp_path)
    (bindir / "codex").unlink()
    result = subprocess.run(
        ["bash", str(FROLLO), "--codex"], cwd=ROOT,
        env={"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)},
        text=True, capture_output=True, check=False,
    )

    assert result.returncode == 1
    assert "codex CLI não encontrado" in result.stdout
    assert not log.exists()
