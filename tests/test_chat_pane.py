"""O pane de chat não pode fechar levando embora o erro que matou o chat.py."""

import subprocess
from pathlib import Path


CHAT_PANE = Path(__file__).parent.parent / "bin" / "chat_pane.sh"


def _run(script, stdin=""):
    return subprocess.run(
        ["bash", str(CHAT_PANE), "bash", "-c", script],
        input=stdin, text=True, capture_output=True, check=False, timeout=10,
    )


def test_saida_com_erro_segura_o_pane_e_mostra_o_codigo():
    result = _run("echo 'unrecognized arguments: --codex' >&2; exit 2", stdin="\n")

    assert result.returncode == 2
    assert "unrecognized arguments: --codex" in result.stderr
    assert "código 2" in result.stdout
    assert "Enter fecha este pane" in result.stdout


def test_saida_normal_fecha_sem_perguntar():
    result = _run("echo tchau; exit 0")

    assert result.returncode == 0
    assert result.stdout == "tchau\n"


def test_argumentos_chegam_intactos_ao_comando():
    result = subprocess.run(
        ["bash", str(CHAT_PANE), "printf", "[%s]", "a b", "--resume"],
        text=True, capture_output=True, check=False, timeout=10,
    )

    assert result.stdout == "[a b][--resume]"
