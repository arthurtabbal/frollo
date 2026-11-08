#!/usr/bin/env bash
# Roda o chat.py dentro do pane de chat sem deixar um erro sumir com o pane.
#
# Uso: chat_pane.sh <comando> [args...]
#
# Falhas que acontecem antes do errors.report() existir (flag inválida no argparse,
# import quebrado, interpretador ausente) só chegam ao stderr. Com o `exec` direto
# no pane, o tmux fechava o pane junto com o processo e a mensagem ia embora.
# Aqui, saída diferente de zero segura o pane até o usuário apertar Enter.

"$@"
rc=$?
if [ "$rc" -ne 0 ]; then
    printf '\n\033[31mfrollo: o chat saiu com código %s\033[0m (motivo acima)\n' "$rc"
    printf 'Enter fecha este pane.\n'
    read -r _ || sleep 30
fi
exit "$rc"
