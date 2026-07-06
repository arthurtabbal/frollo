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

**✅ Concluída** — 145 testes passando (131 anteriores + 14 novos em `test_render.py`).
Verificação manual do autor ainda pendente (thinking longo sem congelar o spinner, skip,
permissão limpa, Ctrl+C sem lixo — ver checklist no fim desta seção).

### 3.1 Fila + thread de render — novo `lib/runner/render.py`
**✅**
- `RenderQueue`: fila única (`queue.Queue`) de itens `("stdout", texto, delay)` ou
  `("file", path, texto, delay, on_newline, hesitate)`; uma thread consumidora aplica o
  typewriter. Fila única preserva a ordem relativa entre chat/thinking/gárgulas — `turn.py`
  não chama mais `_typewrite`/`log_animated` diretamente, só `render.push_stdout`/`push_file`.
  `log_animated`/`SKIP_FLAG` (mortos após a migração — `SKIP_FLAG` nunca era aceso por ninguém)
  foram removidos de `typewriter.py`.
- Skip: um `threading.Event` compartilhado — uma tecla detectada durante a animação liga o
  flag, que faz o item atual **e o que já estiver enfileirado** despejarem sem delay, até a
  fila esvaziar (aí o flag reseta sozinho).
- Loop de eventos (`run_turn`) não chama mais `_show_status()` nem dorme por animação —
  ingestão só lê `proc.stdout` e despacha pro `Turn`, na velocidade do pipe.
- Pontos de sincronização: `render.join()` antes de ler estado pós-escrita (`col_is_mid_line()`
  em `_handle_content_block_stop`, fechamento do bloco de thinking) e `render.suspend()`/`resume()`
  ao redor de `control_request`/`permission_request`/`_handle_permission_ask` — `suspend()` espera
  a fila esvaziar, apaga o spinner e segura o lock até `resume()`, então o banner de permissão
  nunca sai intercalado com um frame do spinner.
- Spinner: `RenderQueue` chama `status_cb`/`clear_status_cb` (implementados por
  `Turn._show_status`/`_clear_status`, inalterados) via tick periódico (a cada ~150ms) quando a
  fila está ociosa, **e também no meio de uma animação longa** (`_maybe_tick`, chamado a cada char
  de `_write_stdout`/`_write_file`) — pra thinking longo não congelar o spinner. As chamadas
  espalhadas de `_show_status()` em `message_start`/`tool_use`/`thinking_delta`/`rate_limit_event`
  foram removidas — o tick cobre isso sozinho.
- `stdout_lock` é **`RLock`, não `Lock`**: `_write_stdout` roda com o lock preso (via `_dispatch`)
  e chama `_maybe_tick` a cada char — se um tick disparar nesse meio-tempo, `_tick` precisa
  readquirir o mesmo lock, na mesma thread. Com `Lock` comum isso é autodeadlock (achado e coberto
  por teste de regressão em `test_render.py`, usando a ponta de leitura de um pipe real como stdin
  pra garantir tempo real decorrido durante a escrita).
- Erros de escrita (ex.: disco cheio) num item da fila não travam `join()`/`stop()` pra sempre —
  `_run` chama `task_done()` num `finally`, mesmo se `_dispatch` levantar.
- `run_turn`: `render.stop()` (drena em ritmo normal, para a thread) roda logo após o `while True`
  de ingestão terminar (EOF), antes de qualquer checagem final de `turn.spinner_shown`/`text_started`
  — garante que só o main thread mexe em stdout dali em diante. `render.cancel()` (força
  esvaziamento via skip, não espera a animação) roda incondicionalmente no `finally` como rede de
  segurança — é no-op seguro se `stop()` já rodou (thread morta, `join()` retorna na hora), e é o
  caminho real de limpeza se uma exceção/Ctrl+C interrompeu o turno antes do `stop()` normal.
- `elapsed`/stats continuam medidos no evento `result` (já era assim desde antes da Fase 3).
- Verificação manual (autor): thinking longo não congela o spinner, skip funciona, permissão
  aparece limpa, Ctrl+C não deixa lixo.

---

## Fase 4 — Processo `claude` persistente (a maior mudança)

### 4.1 Spike de protocolo (antes de codar)

**Pesquisa de docs/issues concluída (jul/2026) — teste interativo do autor com o CLI real ainda
pendente antes de codar o 4.2.**

1. **Múltiplas mensagens `user` sequenciais num processo vivo, um `result` por turno?** ✅
   Confirmado por evidência de terceiros: [issue #25629](https://github.com/anthropics/claude-code/issues/25629)
   documenta uma sessão real de **82 turnos** dentro de um único processo `stream-json`, sem fechar
   stdin entre eles. [Issue #41665](https://github.com/anthropics/claude-code/issues/41665) confirma
   que escrever no stdin **durante** um turno em andamento enfileira a mensagem (processada só
   depois que o turno corrente termina) — reforça que múltiplos turnos sequenciais no mesmo processo
   é o comportamento normal, não um caso extremo.
2. **Existe controle de interrupt via stdin?** ❌ Não, hoje. [Issue #41665](https://github.com/anthropics/claude-code/issues/41665)
   é um feature request aberto pedindo exatamente isso (`{"type": "interrupt"}` — abortaria a tool
   corrente mantendo processo/sessão vivos, tipo ESC no modo interativo). Sem isso, as únicas opções
   documentadas são SIGINT (mata o processo inteiro) ou kill + respawn com `--resume` — **exatamente
   o fallback que o item 4.2 já previa**, então nenhuma mudança de plano aqui.
3. **Modelo/permission-mode fixos por processo ou trocáveis via stdin?** Fixos, ao que tudo indica —
   nenhuma doc ou issue encontrada menciona um control message de stdin para trocar `--model`/
   `--permission-mode` em runtime; ambos são só flags de spawn. Confirma a suposição original:
   trocar = matar + respawnar com `--resume`.
4. **Risco novo, não previsto originalmente:** [issue #25629](https://github.com/anthropics/claude-code/issues/25629)
   documenta um bug conhecido (duplicata de #24478/#24481/#1920) onde o processo **não sai sozinho**
   após o `result` final — stdout fica aberto, processo pendura até SIGINT/SIGKILL manual. Implica
   que `ensure_proc()`/encerramento no 4.2 não pode confiar em `proc.wait()` puro após fechar stdin —
   precisa de timeout com SIGKILL de reserva (o workaround documentado no issue).

Fontes: [Run Claude Code programmatically](https://code.claude.com/docs/en/headless) (docs oficiais —
não cobre o protocolo bidirecional em detalhe, só confirma `--input-format`/`--output-format
stream-json` e o evento `system/init`); as três issues do GitHub acima.

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
