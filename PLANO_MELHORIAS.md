# Plano de Melhorias — Frollo

Plano de implementação derivado da análise crítica do projeto (jul/2026). Ordenado por
dependência: primeiro a rede de proteção (CI), depois fixes baratos e independentes, depois o
refactor que destrava as mudanças estruturais, e por fim o input. Cada item vira um commit
pequeno. As fases 3 e 4 mudam comportamento visível e têm pontos de verificação manual marcados
(o autor testa; o agente não roda o client — ver memória `feedback_no_testing`).

## Diagnóstico (resumo)

A arquitetura do **observer passivo** (`hooks → JSONL → tail -f | jq` com `flock`) é sólida e não é
onde estão os problemas. Os gargalos reais estão no **cliente ativo**, concentrados em dois pontos
estruturais mais alguns bugs latentes e três casos de drift entre `FROLLO.md` e o código.

**Gargalo 1 — um subprocess `claude` por turno, com `--resume` recarregando a sessão inteira.**
Cada turno paga o cold-start do CLI (Node) + reload do transcript completo — custo que cresce
linearmente com o tamanho da sessão. O client fica mais lento justamente nas conversas longas.
Sintomas secundários: `/model` só vale "no próximo turno", `fetch_usage` precisa de outro spawn,
estado espalhado em arquivos (`last_session.json`, `last_quota.json`). O caminho natural é um
processo persistente com `--input-format stream-json` bidirecional — que o código **já usa** para
turnos com imagem (`lib/runner/__init__.py:51`); falta manter o processo vivo entre turnos.

**Gargalo 2 — a animação roda dentro do loop de eventos.** `log_animated` e `_typewrite` dormem no
thread principal; enquanto animam, nada é drenado de `proc.stdout`. Consequências: thinking
limitado a ~1000 chars/s (1ms/char) → quando o modelo produz mais rápido, o pipe de 64KB enche e o
CLI bloqueia no write (**a animação vira backpressure sobre o turno**); spinner congela; um
`control_request` de permissão espera atrás de segundos de animação. Correção: ingestão num loop
rápido + apresentação numa fila consumida por um thread de render.

---

## Fase 0 — Rede de proteção (pré-requisito de tudo)

**✅ Concluída** (`f3e2855`).

### 0.1 CI no GitHub Actions — `.github/workflows/ci.yml`
- Job em `ubuntu-latest`, matrix Python 3.10 e 3.12 (3.10 = mínimo documentado; 3.12 pega deprecations).
- Passos: `python3 -m pytest tests/` e `bash tests/test_install_sh.sh`. Runner já tem `jq`; testes do
  install.sh só exercitam parsers, não precisam de tmux.
- Critério: badge verde no README.

---

## Fase 1 — Fixes baratos e independentes (qualquer ordem)

**✅ Concluída** — todos os 8 itens implementados e commitados, 121 testes passando.
1.7 e 1.8 mudam comportamento visível — verificação manual do autor ainda pendente
(badge de modelo no prompt; skip do typewriter com qualquer tecla).

### 1.1 Turno zumbi (imagem + permissão) — `lib/runner/__init__.py`, `lib/runner/permissions.py`, `bin/chat.py`
**✅ `4b964da`**
No turno com imagem, `proc.stdin` é fechado (`runner/__init__.py:102`). Se o CLI depois emitir
`control_request`/`permission_request`, os handlers escrevem em `proc.stdin` (`permissions.py:88`,
`runner/__init__.py:398`) → `ValueError: I/O on closed file`. A exceção sobe ao handler genérico do
`chat()`, que faz `continue` **sem matar `client.proc`** → subprocess órfão escrevendo em pipe morto.
- Envolver todos os `proc.stdin.write` (permissões + `control_response`) em
  `try/except (ValueError, BrokenPipeError)`: stdin fechado → tratar como negação + avisar no chat.
- No handler genérico de `Exception` do `chat()` (`chat.py:364`), matar `self.proc` se vivo antes do `continue`.
- Teste: unitário simulando `proc.stdin` fechado no `_handle_control_request`.

### 1.2 Separar stderr do stream JSON — `lib/runner/__init__.py`
**✅ `b549388`**
`stderr=subprocess.STDOUT` mistura dois canais num parser que assume um. O rate-limit depende de
regex sobre linhas não-JSON do canal misturado (`runner/__init__.py:215`); stderr no meio de uma
linha JSON parcial corrompe o evento silenciosamente.
- `stderr=subprocess.PIPE` + thread daemon que lê linha a linha, appenda em `RUNDIR/stderr.log` e roda
  ali o parsing textual de rate-limit, sinalizando o loop via a flag `rate_limited`.
- No loop, `JSONDecodeError` vira anomalia logada, não caminho esperado.
- Teste: função pura `_parse_rate_limit_line(raw)` extraída, testada com linhas reais de `rate-limit.log`.

### 1.3 Tabela de preços/contexto — `lib/runner/stats.py`
**✅ `8b74918`**
`_MODEL_PRICES` (`stats.py:3`) tem Opus a $15/$75 e Haiku a $0.80/$4 (desatualizado). Mitigado porque
`result_cost` do evento `result` tem precedência, mas o fallback (erro/cancelamento) superestima Opus 3×.
- Atualizar: Opus 4.x → `(5.0, 25.0)`, Haiku 4.5 → `(1.0, 5.0)`, Sonnet mantém `(3.0, 15.0)`.
  Prefixos específicos primeiro (`claude-opus-4-1`/`4-0` mantêm 15/75; genérico `claude-opus-4` → 5/25).
  Comentário `# verificado jul/2026`.
- Teste: `_model_price` com IDs reais dos três modelos.

### 1.4 Rotação e timestamp do observer.jsonl — `hooks/log.sh`, `bin/observe.sh`
**✅ `c698319`**
Só `layout.sh` trunca o log; o `frollo.sh` (fluxo usado) não. Hooks são globais → toda sessão de
qualquer projeto appenda para sempre. `tool_input` de Bash pode conter segredos em plaintext.
- No `log.sh`, dentro do `flock`: se o log passar de ~10 MB, `mv` para `observer.jsonl.1` (1 geração).
- `_ts` vira ISO completo (`%F %T`); `observe.sh` exibe só `._ts[11:19]`. Ajustar `tests/test_log_sh.py`.
- Nota no FROLLO.md: log contém `tool_input` em plaintext — decisão consciente documentada.

### 1.5 Corrida da cota — `lib/runner/__init__.py`, `bin/chat.py`
**✅ `a1d4c41`**
Turnos rápidos consecutivos disparam threads `_bg_usage` que terminam fora de ordem e pintam cota
stale por cima da fresca.
- Contador de geração no client (`client._usage_gen`): cada `_bg_usage` captura o valor no início e só
  pinta a linha 4 se ainda for a geração corrente.

### 1.6 Session picker resiliente — `lib/session.py`
**✅ `b71c000`**
`session.py:42` só reconhece sessões cujo 1º evento é `queue-operation/enqueue`; qualquer exceção vira
`return None` silencioso. Quando o schema do CLI mudar, o picker "não acha nada" sem pista.
- Fallback: sem `queue-operation/enqueue`, usar 1º evento `type=="user"` com texto.
- Trocar `except Exception: return None` cego por log em `RUNDIR/err.log` antes de retornar.
- Teste: jsonl sintético nos dois schemas.

### 1.7 Badge de modelo no prompt (religar código morto) — `bin/chat.py`, `lib/input.py`
**✅ `70d3244`** — verificação visual do autor ainda pendente.
`ClaudeClient._prompt` (`chat.py:105`) monta o badge de modelo mas nunca é chamado; quem renderiza é
`InputReader._prompt` (`input.py:84`), que só conhece o modo. FROLLO.md afirma que o badge aparece —
não aparece.
- `InputReader` ganha `prompt_provider` opcional (callable); `ClaudeClient` passa seu `_prompt`.
- `_vprompt` (cálculo de largura p/ wrap) precisa incluir o badge do modelo — senão o cursor desalinha.
- Teste: `_visual_pos` com o prompt novo; verificação visual do autor.

### 1.8 Skip do typewriter por qualquer tecla (de verdade) — `lib/runner/__init__.py`, `lib/runner/text.py`
**✅ `21267df`** — verificação manual do autor ainda pendente.
Durante o turno o termios só desliga `ECHO` (`runner/__init__.py:113`); `ICANON` fica ligado, então o
`select` em `_typewrite` (`text.py:47`) só acorda com uma linha completa (Enter). "Qualquer tecla" na
prática é Enter.
- No setup de termios do turno, desligar também `ICANON` (VMIN=1, VTIME=0), mantendo `ISIG` (Ctrl+C ok).
- Em `_typewrite`, trocar `sys.stdin.readline()` por `os.read(fd, 1024)`.
- Conferir que os `_raw_stdin` aninhados das permissões continuam restaurando (salvam/restauram próprio estado).
- Verificação manual: tecla qualquer pula, Ctrl+C cancela, prompt volta normal.

---

## Fase 2 — Refactor do `run_turn` (destrava 3 e 4)

**✅ Concluída** — 131 testes passando (121 anteriores + 10 novos em `test_turn.py`).

`run_turn` era uma god-function de ~500 linhas com ~30 variáveis de estado e closures aninhadas — e
era exatamente a parte sem teste (os testes cobriam a periferia; o loop de eventos, permissões e
rate-limit não tinham cobertura direta).

### 2.1 Extrair a máquina de estados do turno — novo `lib/runner/turn.py`
**✅**
- Classe `Turn` com o estado que antes eram ~30 variáveis locais (tokens, rate-limit, thinking, `md_buf`,
  flags de bloco) e `handle_event(event)` com dispatch por `type`/`event.type` — cada branch virou método
  (`_handle_stream_event`, `_handle_content_block_delta`, `_handle_content_block_stop`,
  `_handle_assistant`, `_handle_user`, `_handle_result`, `_handle_rate_limit_event`).
- `run_turn` encolheu para: montar `cmd`, spawn, loop `select` → `turn.handle_line(raw)`, finalize
  (stats, cota, restore). `try/finally` do termios e do pane permanecem.
- Transplante, não redesign — mesma ordem de operações e mesmos efeitos colaterais de antes.
- `tests/test_runner.py` ajustado: patches de `_log`/`log_animated`/`_resize_thinking`/`RUNDIR` que
  antes miravam `lib.runner.*` agora também miram `lib.runner.turn.*`, onde a lógica passou a viver.

### 2.2 Testes do dispatcher — `tests/test_turn.py`
**✅**
- Eventos sintéticos (`message_start`, `thinking_delta`, `text_delta`, `tool_use`, `tool_result` com
  erro de permissão, `result`, `rate_limit_event`) no `Turn` com render mockado; asserts sobre estado
  e chamadas. Cobertura direta que antes não existia sobre a parte mais crítica.

---

## Fase 3 — Render desacoplado do loop de eventos

### 3.1 Fila + thread de render — novo `lib/runner/render.py`
- `RenderQueue`: `queue.Queue` de itens `(destino, texto, delay)`, destino ∈ {stdout-chat,
  thinking-log, tools-log}; thread consumidor aplica o typewriter. **Fila única** preserva a ordem
  relativa entre chat/thinking/gárgulas (duas filas dessincronizariam a narrativa).
- Skip = flag que faz o consumidor despejar a fila sem delay (substitui `SKIP_FLAG` em arquivo).
- Loop de eventos nunca mais dorme por animação: ingestão na velocidade do pipe, fim do backpressure.
- Pontos de sincronização: antes de banner de permissão e antes do prompt de fim de turno,
  `queue.join()` (flush) — permissão nunca aparece no meio de uma frase.
- Spinner: responsabilidade do render thread quando a fila está vazia e o turno vivo — some a lógica
  de `_show_status` espalhada.
- `elapsed`/stats medidos no evento `result` (tempo real), não no fim da animação.
- Riscos a vigiar: termios (render thread escreve, main mexe em termios — ok porque trocas de modo
  acontecem só nos pontos de flush) e Ctrl+C (matar proc + drenar fila + parar thread, nessa ordem).
- Verificação manual: thinking longo não congela o spinner, skip funciona, permissão aparece limpa,
  Ctrl+C não deixa lixo.

---

## Fase 4 — Processo `claude` persistente (a maior mudança)

### 4.1 Spike de protocolo (antes de codar)
Investigar, com o CLI real, três perguntas:
1. Com `--input-format stream-json` e processo vivo, o CLI aceita múltiplas mensagens `user`
   sequenciais, emitindo um `result` por turno?
2. Existe controle de interrupt via stdin (cancelar turno sem matar o processo)?
3. Modelo e permission-mode são fixos por processo (provável) ou trocáveis?

Docs + teste rápido em terminal (parte de docs/protocolo pelo agente; teste interativo do autor).

### 4.2 Implementação atrás de flag — `lib/runner/__init__.py`, `bin/chat.py`, `lib/config.py`
- `persistent: true` na config (default `false` até estabilizar). Caminho per-turn atual permanece
  como fallback — não é rewrite, é modo alternativo.
- `ClaudeClient` ganha `ensure_proc()`: spawna uma vez com `--input-format stream-json`; cada envio
  vira linha JSON no stdin (serialização já existe no caminho de imagem). Evento `result` delimita o turno.
- `/model` e Shift+Tab (auto↔normal): se per-process, trocam matando + re-spawnando com
  `--resume <session_id>` — custo igual ao modelo atual, nunca pior.
- Ctrl+C: se houver interrupt via protocolo, usa; senão kill + respawn com `--resume` (comportamento atual).
- `/refresh` e `/new` continuam `execvp` — não mudam.
- Ganho: latência de início de turno constante em vez de crescer com a sessão; some o reload do transcript.
- Verificação manual: sessão longa, turnos com tool calls, cancelamento, troca de modelo, resume após sair.

### 4.3 Promover a default
Depois de alguns dias com `persistent: true` sem sustos, virar default e documentar.

---

## Fase 5 — Input (independente, pode intercalar)

### 5.1 Bracketed paste — `lib/input.py`
Colar texto multilinha envia na primeira quebra de linha (`input.py:216` trata `\n` como submit) — é
a razão de existir o `/paste`. Além disso, cada keypress fora do fim da linha dispara `_redraw`
completo com `_visual_pos` O(n) → paste/edição grande fica O(n²).
- Emitir `\033[?2004h` ao entrar em `read_input`, `\033[?2004l` no finally. Tratar `ESC[200~ … ESC[201~`:
  ler em loop até o terminador (o `os.read(fd, 8)` atual não basta), inserir conteúdo com `\n` literais
  como texto — sem submit — e fazer **um** redraw ao final.
- Elimina o "Enter no meio do paste envia" e resolve o pior caso de performance.
- `/paste` continua para textos gigantes, mas deixa de ser necessário no caso comum.
- Teste: parser da sequência de paste com bytes sintéticos.

### 5.2 (Opcional) Redraw incremental
Só se digitação em bloco multilinha grande ainda incomodar depois do 5.1; senão não vale a complexidade.

---

## Fase 6 — Documentação

### 6.1 Atualizar FROLLO.md
Corrigir os drifts na direção do código novo: badge no prompt (agora real), skip por qualquer tecla
(agora real), preços, rotação do log, tabela de módulos (`turn.py`, `render.py`), seção do modo
persistente com a flag de config.

---

## Sequência e esforço

| Ordem | Itens | Tamanho | Risco |
|---|---|---|---|
| 1 | Fase 0 + Fase 1 (8 commits pequenos) | ~1 sessão | baixo |
| 2 | Fase 2 (refactor + testes) | ~1 sessão | médio (mitigado: transplante + testes novos) |
| 3 | Fase 3 (render thread) | ~1 sessão | médio-alto (concorrência + terminal) |
| 4 | Fase 4 (persistente, atrás de flag) | spike + ~1 sessão | alto (protocolo), mas com fallback |
| 5 | Fases 5 e 6 | curtas | baixo |

**Regra de ouro da sequência:** 3 e 4 só depois de 2 (refatorar `run_turn` com render thread e
processo persistente embutidos seria refatorar três coisas ao mesmo tempo); tudo depois de 0 (cada
fase aterrissa com pytest rodando no CI).

## O que NÃO muda (bem resolvido, não tocar)

Pipeline `hooks → JSONL → tail -f | jq` com `flock`; `try/finally` do termios; RUNDIR per-processo
(`/tmp/claude-client-$$` no `frollo.sh`); cap do `MdBuffer`; preferência pelo evento `result` para custo.
