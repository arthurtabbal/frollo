import os
import sys
import termios
import tty
import time

from .config import load as _load, save as _save, is_first_run, CONFIG_PATH
from .theme import DIM, RESET, YELLOW, WHITE, HEADER_TITLE, HEADER_STONE


_LINE = f"{HEADER_STONE}   {'─' * 46}{RESET}"


def _ask(label: str, desc: str, default: bool) -> bool:
    hint = f"{YELLOW}[S/n]{RESET}" if default else f"{YELLOW}[s/N]{RESET}"
    sys.stdout.write(f"\n{_LINE}\n\n")
    sys.stdout.write(f"   {WHITE}{label}{RESET}\n")
    sys.stdout.write(f"   {DIM}{desc}{RESET}\n")
    sys.stdout.write(f"   {hint}: ")
    sys.stdout.flush()

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            ch = os.read(fd, 1).decode("utf-8", errors="replace")
            if ch == "\x03":
                sys.stdout.write("\n")
                sys.stdout.flush()
                raise KeyboardInterrupt
            if ch in ("\r", "\n"):
                sys.stdout.write(("S" if default else "N") + "\n")
                sys.stdout.flush()
                return default
            ch = ch.lower()
            if ch in ("s", "y"):
                sys.stdout.write("S\n")
                sys.stdout.flush()
                return True
            if ch == "n":
                sys.stdout.write("N\n")
                sys.stdout.flush()
                return False
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def run_configure(*, first_run: bool = False) -> None:
    existing = _load()

    greeting = (
        "Bem-vindo ao Frollo. Configure sua experiência antes de começar."
        if first_run else
        "Reconfiguração — os valores atuais aparecem como padrão."
    )
    sys.stdout.write(f"\n   {HEADER_TITLE}CONFIGURAÇÃO — CLAUDE FROLLO OBSERVER{RESET}\n")
    sys.stdout.write(f"   {DIM}{greeting}{RESET}\n")
    sys.stdout.write(f"   {DIM}Pressione S/N para cada opção; Enter mantém o padrão.{RESET}\n")

    typewriter = _ask(
        "TYPEWRITER",
        "Texto da resposta aparece letra a letra, como pensamento revelado.",
        existing.get("typewriter", True),
    )
    gargoyles = _ask(
        "GÁRGULAS",
        "Victor, Hugo e Gudule comentam seu trabalho nos tools.",
        existing.get("gargoyles", True),
    )
    stats_pane = _ask(
        "PAINEL DE ESTATÍSTICAS",
        "Exibe tokens, tempo e custo após cada turno.",
        existing.get("stats_pane", True),
    )

    cfg = {"typewriter": typewriter, "gargoyles": gargoyles, "stats_pane": stats_pane}
    _save(cfg)

    sys.stdout.write(f"\n{_LINE}\n\n")
    sys.stdout.write(f"   {DIM}Configuração salva em {CONFIG_PATH}{RESET}\n\n")
    if not stats_pane:
        sys.stdout.write(f"   {DIM}Painel de stats desativado — reinicie o Frollo para aplicar.{RESET}\n\n")
    sys.stdout.flush()
    time.sleep(1.2)
