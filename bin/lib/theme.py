import re

E = "\033"
DIM    = f"{E}[2m"
RESET  = f"{E}[0m"
BOLD   = f"{E}[1m"
ITALIC = f"{E}[3m"

# ── Paleta base (Nord + custom) ───────────────────────────
GREEN  = f"{E}[38;5;108m"   # #a3be8c Nord14 sage
YELLOW = f"{E}[38;5;222m"   # #ebcb8b Nord13 âmbar quente
RED    = f"{E}[38;5;167m"   # #bf616a Nord11 — só para erro
CYAN   = f"{E}[38;5;110m"   # #87afd7  frost blue
BLUE   = f"{E}[38;5;67m"    # #5e81ac  Nord10 azul marinho
PURPLE = f"{E}[38;5;60m"    # #5e517f  roxo custom
WHITE  = f"{E}[38;5;253m"   # #d8dee9  Nord4 branco suave

BG_USER = f"{E}[48;5;236m"  # #2e3440 Nord0 fundo
BG_PERM = f"{E}[48;5;52m"   # vermelho escuro (permissões)

# pedra, manto de Frollo, olhos âmbar
_ST = f"{E}[38;5;238m"   # pedra — Nord1 cinza escuro
_FR = f"{E}[38;5;236m"   # manto — Nord0 fundo
_EY = f"{E}[38;5;222m"   # olhos âmbar — Nord13

# ── Papéis de UI (trocar aqui muda a aplicação inteira) ───
CHAT_FG         = _EY          # âmbar: Frollo falando
THINKING_FG     = CYAN         # azul pastel: o interior etéreo
THINKING_TS     = BLUE         # azul marinho: âncora do timestamp
TOOLS_BASH      = GREEN        # ⚡
TOOLS_EDIT      = YELLOW       # ✎
TOOLS_WRITE     = GREEN        # ◆
TOOLS_READ      = CYAN         # ◎
TOOLS_AGENT_IC  = PURPLE       # ◈ (ic = ícone, evita conflito com AGENT)
TOOLS_WEB       = BLUE         # ↓
TOOLS_TODO      = WHITE        # ☑
HEADER_TITLE    = CYAN         # "Claude Frollo Observer"
HEADER_STONE    = f"{E}[38;5;241m"  # pedra clara — catedral
HEADER_DARK     = f"{E}[38;5;238m"  # pedra escura — sombra
HEADER_ROSE     = f"{E}[38;5;220m"  # rosácea — dourado
ERROR_FG        = RED          # ✖ falha que o usuário precisa ver
WARN_FG         = YELLOW       # ! degradação, não falha
GARGOYLE_VICTOR = PURPLE
GARGOYLE_HUGO   = GREEN
GARGOYLE_GUDULE = f"{E}[38;5;103m"  # lilás acinzentado
MD_CODE         = f"{E}[38;5;220m"  # inline code no markdown

AGENT = CHAT_FG  # alias de compatibilidade

# Citações de Notre-Dame de Paris (Victor Hugo, 1831 — domínio público)
_QUOTES = [
    '"Ceci tuera cela." — o livro matará o edifício.',
    '"ΑΝΆΓΚΗ" — a fatalidade que Frollo gravou na pedra.',
    '"A arquitetura é a escrita da humanidade em pedra."',
    '"Quasimodo via em Notre-Dame a concha, o lar, a pátria, o universo."',
    '"Paris vista do alto é um oceano. Nenhum olho a abarca inteira."',
    '"Frollo lera tudo, sabia tudo — e assim perdera tudo."',
    '"O tempo é o arquiteto mais paciente. E o mais cruel."',
    '"A alma de Frollo era a chama que ele tanto temia."',
    '"Cada sino de Notre-Dame era uma voz, e ele as conhecia todas."',
    '"O que o homem constrói em séculos, o fogo desfaz em horas."',
    '"Há mais sabedoria numa pedra de Notre-Dame do que em todos os livros de Frollo."',
]

# Chamas — referência ao braseiro interno de Frollo
_F = [
    f"{E}[38;5;226m▲{E}[38;5;208m▲{E}[38;5;196m▲",
    f"{E}[38;5;208m▲{E}[38;5;226m▲{E}[38;5;208m▲",
    f"{E}[38;5;196m▲{E}[38;5;208m▲{E}[38;5;226m▲",
    f"{E}[38;5;208m▲{E}[38;5;196m▲{E}[38;5;208m▲",
]

# Gradiente do braseiro — "lanterna na palavra": escuro → branco → escuro
_GLOW = [
    f"{E}[38;5;52m",
    f"{E}[38;5;88m",
    f"{E}[38;5;124m",
    f"{E}[38;5;160m",
    f"{E}[38;5;196m",
    f"{E}[38;5;202m",
    f"{E}[38;5;208m",
    f"{E}[38;5;214m",
    f"{E}[38;5;220m",
    f"{E}[38;5;226m",
    f"{E}[38;5;229m",
    f"{E}[38;5;255m",
    f"{E}[38;5;229m",
    f"{E}[38;5;226m",
    f"{E}[38;5;220m",
    f"{E}[38;5;214m",
    f"{E}[38;5;208m",
    f"{E}[38;5;202m",
    f"{E}[38;5;196m",
    f"{E}[38;5;160m",
    f"{E}[38;5;124m",
    f"{E}[38;5;88m",
]


_MD_BUFFER_CAP = 2000  # chars acumulados antes de forçar flush mesmo desbalanceado


class MdBuffer:
    """Acumula chunks de streaming até que todos os spans markdown estejam fechados."""

    def __init__(self):
        self._buf = ""

    def feed(self, chunk: str) -> str:
        """Recebe chunk; retorna texto processado quando spans balanceados, senão ''.

        Um caractere solto sem par (ex: "2 * 3 = 6") nunca balanceia — sem o cap,
        o buffer acumularia o resto inteiro da resposta e despejaria tudo de uma vez
        no fim do bloco, matando o efeito typewriter pro resto do turno.
        """
        self._buf += chunk
        if self._balanced() or len(self._buf) > _MD_BUFFER_CAP:
            return self._flush()
        return ""

    def flush(self) -> str:
        """Força processamento do que restar no buffer (fim de bloco)."""
        return self._flush()

    def _flush(self) -> str:
        out = _md(self._buf)
        self._buf = ""
        return out

    def _balanced(self) -> bool:
        t = self._buf
        # Fenced code block open: wait for closing ```
        if t.count('```') % 2 != 0:
            return False
        # Heading in progress: don't flush until the line ends with \n
        if re.search(r'(?:^|\n)#{1,3} [^\n]*\Z', t):
            return False
        # Pipe tables only become recognizable after the separator row arrives.
        # Streaming chunks can split a row before the closing pipe, so hold a
        # trailing table candidate as soon as the line starts like a pipe table.
        if _ends_with_tableish_line(t):
            return False
        if len(re.findall(r'\*\*', t)) % 2 != 0:
            return False
        if re.sub(r'\*\*', '', t).count('*') % 2 != 0:
            return False
        if len(re.findall(r'(?<!`)`(?!`)', t)) % 2 != 0:
            return False
        return True


CLEAR = "\033[2J\033[H"  # erase display + cursor home (preserva scrollback no tmux)


def _render_code_block(m):
    lang = m.group(1).strip()
    code = m.group(2)
    if code.endswith('\n'):
        code = code[:-1]
    label = (DIM + CYAN + lang + RESET + '\n') if lang else ''
    lines = '\n'.join('  ' + MD_CODE + ln + RESET + CHAT_FG for ln in code.split('\n'))
    return label + lines + '\n'


def _split_table_row(line):
    stripped = line.strip()
    if not stripped or "|" not in stripped:
        return None
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    cells = re.split(r'(?<!\\)\|', stripped)
    if len(cells) < 2:
        return None
    return [cell.replace(r"\|", "|").strip() for cell in cells]


def _is_table_separator(cells):
    if not cells or len(cells) < 2:
        return False
    return all(re.fullmatch(r':?-{3,}:?', cell.replace(" ", "")) for cell in cells)


def _table_alignments(cells):
    align = []
    for cell in cells:
        marker = cell.replace(" ", "")
        if marker.startswith(":") and marker.endswith(":"):
            align.append("center")
        elif marker.endswith(":"):
            align.append("right")
        else:
            align.append("left")
    return align


def _plain_markdown_width(text):
    text = re.sub(r'`([^`\n]+)`', r'\1', text)
    text = re.sub(r'\*\*([^*\n]+)\*\*', r'\1', text)
    text = re.sub(r'__([^_\n]+)__', r'\1', text)
    text = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'\1', text)
    text = re.sub(r'(?<!\w)_([^_\n]+)_(?!\w)', r'\1', text)
    return len(text)


def _pad_cell(text, width, align):
    visible = _plain_markdown_width(text)
    pad = max(0, width - visible)
    if align == "right":
        return (" " * pad) + text
    if align == "center":
        left = pad // 2
        return (" " * left) + text + (" " * (pad - left))
    return text + (" " * pad)


def _render_table(header, alignments, rows):
    cols = len(header)

    def normalize(row):
        return (row + [""] * cols)[:cols]

    table_rows = [normalize(header)] + [normalize(row) for row in rows]
    widths = [
        max(_plain_markdown_width(row[col]) for row in table_rows)
        for col in range(cols)
    ]
    sep = "  " + DIM + "-+-".join("-" * width for width in widths) + RESET + CHAT_FG
    rendered = [
        "  " + " | ".join(BOLD + _pad_cell(header[col], widths[col], alignments[col]) + RESET + CHAT_FG
                           for col in range(cols)),
        sep,
    ]
    for row in rows:
        row = normalize(row)
        rendered.append("  " + " | ".join(
            _pad_cell(row[col], widths[col], alignments[col])
            for col in range(cols)
        ))
    return rendered


def _format_tables(text):
    lines = text.split("\n")
    out = []
    i = 0
    while i < len(lines):
        header = _split_table_row(lines[i])
        separator = _split_table_row(lines[i + 1]) if i + 1 < len(lines) else None
        if header and separator and len(header) == len(separator) and _is_table_separator(separator):
            rows = []
            j = i + 2
            while j < len(lines):
                row = _split_table_row(lines[j])
                if not row:
                    break
                rows.append(row)
                j += 1
            out.extend(_render_table(header, _table_alignments(separator), rows))
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def _ends_with_tableish_line(text):
    if text.endswith("\n\n"):
        return False
    lines = text.split("\n")
    idx = len(lines) - 1
    while idx >= 0 and lines[idx] == "":
        idx -= 1
    if idx < 0:
        return False
    line = lines[idx].strip()
    if not line.startswith("|"):
        return False
    return True


def _md(text):
    """Converte markdown comum para ANSI. Funciona por chunk — spans que cruzam chunks ficam crus."""
    B = "\033[1m"  # bold — herda âmbar do CHAT_FG
    I = "\033[4m"          # sublinhado — mais confiável que italic no tmux
    R = RESET + CHAT_FG
    text = re.sub(r'```([^\n]*)\n(.*?)```', _render_code_block, text, flags=re.DOTALL)
    text = _format_tables(text)
    text = re.sub(r'`([^`\n]+)`',                MD_CODE + r'\1' + R, text)
    text = re.sub(r'\*\*([^*\n]+)\*\*',          B       + r'\1' + R, text)
    text = re.sub(r'__([^_\n]+)__',              B       + r'\1' + R, text)
    text = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', I       + r'\1' + R, text)
    text = re.sub(r'(?<!\w)_([^_\n]+)_(?!\w)',     I       + r'\1' + R, text)
    text = re.sub(r'^#{1,3} (.+)$',              B       + r'\1' + R, text, flags=re.MULTILINE)
    return text
