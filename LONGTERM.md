# LONGTERM.md — Direção de Longo Prazo do Frollo

> Documento vivo. Registra a estratégia de evolução do projeto, os débitos técnicos conhecidos e
> as melhorias planejadas — com priorização por custo-benefício. Atualizar sempre que um item for
> concluído ou a estratégia mudar. Todo número aqui tem data: foi medido, não estimado.

**Última atualização**: 2026-09-09

---

## 1. Contexto estratégico: de frontend do Claude Code a plataforma de observabilidade

A direção está **definida nas issues #1–#7** (umbrella: #6, jul/2026) e resumida no `README.md`:
o valor do Frollo não está preso a um agente específico, está em tornar a engenharia de software
autônoma **observável**. O que já é real hoje, e o que ainda é intenção:

| Peça | Estado (medido em 09/09) |
|---|---|
| Backend Claude Code (hooks + stream-json) | Produção. Continua sendo o backend primário de desenvolvimento |
| Backend Codex App Server (#7) | Experimental, mas real: processo persistente, cota, thinking resumido. Já normaliza para `frollo.event.v0` |
| Capabilities por backend (#3) | Feito: `lib/runner/capabilities.py`; a UI já degrada por flag, não por nome de provedor |
| Protocolo canônico `frollo.event.v0` (#2) | **Parcial**: o Codex fala v0; o Claude sintetiza v0 em paralelo (`turn.frollo_events`) mas o renderer Claude ainda consome a máquina antiga |
| Métricas comportamentais (#1) | Não começou. O stream já carrega o dado |
| OTLP/JSON (#4), Runtime nativo (#5) | Deferidos de propósito — não há backend real que os justifique hoje |

Implicações práticas:

1. **Claude Code é o backend primário e continua sendo.** Toda feature nova nasce nele; o Codex é
   o segundo ponto de vista que impede a abstração de virar cópia do Claude.
2. **Abstração vem depois da observação, não antes** — regra escrita no `FROLLO.md` (Estratégia de
   agentes e backends) e na #7 ("prefer incomplete implementations over temporary architectures").
   Nenhuma camada nova entra para "preparar" um terceiro backend que não existe.
3. **O observer passivo (`hooks/log.sh` → `observer.jsonl`) é o ativo mais valioso do projeto** e o
   menos cuidado: é global, captura toda sessão da máquina e já é um flight recorder. Duas das
   oportunidades abaixo (§3.1, §3.3) só existem por causa dele.

---

## 2. Débitos técnicos priorizados

### 2.1 Hook instalado desatualizado — o `install.sh` não reinstala (custo baixo, ganho alto)

- [ ] **Medido em 09/09**: `~/.claude/observer.jsonl` com **235 MB**, quando o `hooks/log.sh` do
  repo rotaciona a 10 MB. Causa: o hook que o `settings.json` aponta
  (`~/.claude/hooks/log.sh`, de **26/05**) é uma versão anterior — sem `MAX_SIZE`, sem lockfile
  dedicado (faz `flock` no próprio log) e com timestamp `%H:%M:%S` em vez de `%F %T`. O
  `install.sh` é idempotente para *registrar* o hook, mas não compara o conteúdo do que já está
  instalado. Fix: comparar hash (repo × instalado) e recopiar quando divergir; o smoke test final do
  `install.sh` deve falhar alto se o instalado ≠ repo. Teste em `tests/test_install_sh.sh`.
- [ ] Rotação de **uma** geração a 10 MB descarta história. Se o replay (§3.3) virar feature, a
  política muda: rotação por sessão ou por dia, não por tamanho. Decidir junto com §3.3, não antes.
- [ ] O `observe.sh` deve tolerar os dois formatos de `_ts` enquanto houver logs antigos.

### 2.2 Referência pendurada: `PLANO_MELHORIAS.md`

- [ ] `FROLLO.md` (Modo persistente) e a docstring de `_terminate_proc` (`lib/runner/__init__.py`)
  citam `PLANO_MELHORIAS.md` (Fases 2–4, spike 4.1). O arquivo foi **removido em 77a1468
  (2026-07-06, "concluído")**, mas as duas referências ficaram — e a Fase 4 que elas citam
  ("alguns dias sem sustos" para virar default) não estava concluída. O conteúdo que sobreviveu
  está espalhado: Fase 2 = `Turn`, Fase 3 = `RenderQueue`, Fase 4 = persistente. Decisão
  proposta: este `LONGTERM.md` é o sucessor; trocar as duas referências (`git show 77a1468^:PLANO_MELHORIAS.md`
  recupera o original se precisar) e registrar aqui o que falta da Fase 4 (§2.3).

### 2.3 Modo persistente ainda não é default

- [ ] Critério registrado no `FROLLO.md`: "alguns dias sem sustos". Não há registro de que a
  verificação tenha sido feita. Definir o que conta como susto (processo órfão, `missing_result`,
  `idle_timeout` sem causa) e medir com `errors.jsonl` durante uma semana de uso real.
- [ ] Sem interrupt via protocolo: Ctrl+C mata o processo e o turno seguinte paga o `--resume`.
  Investigar se o `--input-format stream-json` aceita um sinal de cancelamento antes de aceitar
  isso como limite permanente.
- [ ] O modo persistente é **pré-requisito de identidade estável de peer** (§5). Isso muda a
  prioridade dele: deixa de ser otimização de cold-start e vira habilitador de feature.

### 2.4 Renderer Claude na máquina antiga (o que falta da #2)

- [ ] O Claude sintetiza eventos v0 mas não os consome — dois caminhos paralelos para o mesmo
  turno é o tipo de duplicação que diverge em silêncio. Sequência em §4.
- [ ] O rascunho `tmp/event-map-draft.md` (2026-07-18) é a única fonte do mapeamento
  Codex × Claude → v0 — e `tmp/` está no `.gitignore`: **o arquivo existe só nesta máquina**.
  Promover para `docs/` (ou para o corpo da #2) antes que se perca.

### 2.5 Documentação com números à mão

- [ ] A tabela "Módulos (bin/)" do `FROLLO.md` traz contagem de linhas por arquivo, e já driftou:
  `chat.py` consta como "~430" e tem **938** (09/09). Número que só serve para divergir sai; se a
  informação importa, um teste gera a tabela ou a checa. Mesma doutrina do `noharm-cli` para
  versão em rodapé de doc.
- [ ] `README.md` e `FROLLO.md` repetem a lista de features. A regra do `AGENT_RULES.md` já diz quem
  é canônico (`FROLLO.md`); falta um lint mínimo que pegue comando/flag citado num e ausente no
  outro (as tabelas "Comandos no chat" e a de flags do `README.md` são o embrião).

### 2.6 Rascunhos fora do versionamento

- [ ] `tmp/` é ignorado pelo git de propósito (runtime), mas acumulou rascunhos de desenho
  (`event-map-draft.md`, `frollo-demo-edit.md`) que não são runtime. Criar `docs/drafts/` para o
  que é pensamento em andamento e deixar `tmp/` só para o que pode ser apagado sem perda. Custo
  trivial; o risco é o do item §2.4.

---

## 3. Oportunidades de features

### 3.1 Mensagens entre sessões — o Frollo como cockpit de orquestração

Achado de campo de 09/09, documentado em `docs/peer-messaging.md`: o Claude Code 2.1.x tem
mensageria entre sessões locais (`ListAgents`/`SendMessage`), com registro em disco
(`~/.claude/sessions/<pid>.json`) e um socket unix por processo. O Frollo hoje **quebra o caminho
de volta** (identidade de peer é por pid; o spawn por turno gera um peer novo por turno) e é
**cego ao tráfego** (só vê `SendMessage` como uma tool call qualquer). Cinco features (F1–F5) e
cinco experimentos estão no documento; a trilha com gates está em §5. É a oportunidade com melhor
razão custo/valor do documento: F1 e F2 são leitura de JSON já existente, stdlib, sem tocar no
runner.

### 3.2 Métricas comportamentais (#1)

O stream já carrega tudo que a issue lista (arquivos lidos antes da primeira edição, razão
leitura/escrita, tempo até o primeiro edit, caminhos abandonados). Regra de desenho:
**determinístico, derivado do JSONL, descritivo e não avaliativo** — e por sessão, para caber no
stats pane como quinta linha antes de virar dashboard. Depende de §4 (uma fonte de eventos só)
para não implementar duas vezes.

### 3.3 Replay

O `observer.jsonl` e o `events.log` do cliente já são gravações. `frollo --replay <session_id>`
reproduz uma sessão nos mesmos panes, no ritmo original ou acelerado — é o "flight recorder" da
tese do `README.md` virando feature, e o instrumento natural para depurar backend, protocolo e
renderer sem gastar cota. Pré-requisitos: rotação por sessão (§2.1) e eventos v0 como formato de
gravação (§4). Vale para os dois backends de graça se a gravação for v0.

### 3.4 Visão multi-sessão

O observer é global e todo evento traz `session_id` + `cwd`; o registro de peers traz nome, status
e o pane tmux de cada sessão. Um pane que mostra as sessões vivas da máquina, o que cada uma está
fazendo (última tool) e o tráfego entre elas é a extensão natural do `observe.sh`. Compartilha
fundação com §3.1 (F1/F2) — é a mesma feature vista de fora do cliente.

### 3.5 OTLP/JSON (#4) e Runtime nativo (#5)

Ficam como estão: deferidos, com as restrições já escritas nas issues (stdlib only, JSON legível,
nunca o SDK). Entram aqui só para que a ordem de §6 os posicione no fim, de propósito.

---

## 4. Trilha do protocolo canônico — fechar a #2

Decisão implícita nas issues #2/#3/#7 e no código: derivar a abstração de duas implementações
reais (Claude e Codex) e só então unificar o renderer. As duas existem; falta a unificação.

### Direção

1. **Um renderer, alimentado por v0.** O `Turn` do Claude passa a **empurrar** eventos v0 para o
   mesmo renderer que o Codex usa; a máquina antiga (`_handle_stream_event`,
   `_handle_content_block_delta`…) vira só o *adapter* que produz v0, sem escrever em pane nenhum.
2. **Gravação em v0.** `events.log` grava o envelope canônico, não o stream-json bruto (o bruto
   continua disponível atrás de flag para depurar o adapter). É o que habilita §3.3 e §3.2 uma
   vez só.
3. **`peer.message` como kind v0** (`from`, `to`, `preview`), produzido pelo observer a partir do
   `PreToolUse` de `SendMessage` — primeiro kind que não vem de um backend, e sim da máquina.

### Sequência de PRs

1. **refactor(turn)**: renderer Claude consome `turn.frollo_events` atrás de flag
   (`render_v0: true`), com teste de equivalência: mesma sessão gravada, mesma saída nos panes
   pelos dois caminhos.
2. **refactor(turn)**: flag vira default; máquina antiga só produz eventos.
3. **feat(protocol)**: gravação v0 + `--replay` mínimo (lê o arquivo, empurra no renderer).
4. **feat(protocol)**: kind `peer.message` + render no observer (é o F2 de §5).
5. **chore**: promover `tmp/event-map-draft.md`, fechar #2, atualizar `FROLLO.md`.

---

## 5. Frollo como cockpit de orquestração — o ciclo do peer messaging

Decisão de 2026-09-09: é o próximo ciclo com **fase de experimento antes de código**, porque parte
do comportamento depende do CLI (o que ele faz ao receber mensagem em `--print` ocioso) e não do
Frollo. Detalhe em `docs/peer-messaging.md`; aqui só a ordem e os gates.

### O problema, em dois eixos

**Eixo 1 — identidade.** Nome e socket de peer nascem por **processo**. No modo default o Frollo
spawna um `claude` por turno: quatro turnos, quatro nomes (`noharm-cli-b9`, `-a5`, `-8f`, `-69`,
medidos em 09/09). Qualquer sessão que tente responder a esta morre no turno seguinte.

**Eixo 2 — escuta.** Entre turnos não há processo (default) ou há processo mas ninguém lê o
stdout (persistente). No segundo caso, uma mensagem recebida que gere eventos enche o pipe e trava
o CLI — o backpressure que a Fase 3 resolveu para o typewriter, agora entre turnos.

### Fases (a ordem importa)

- **Fase 1 — ver** (F1 + F2): pane de peers lendo `~/.claude/sessions/*.json` (só `.json`, nunca
  `.key`) e tráfego de `SendMessage` no observer, primeira linha da mensagem apenas. Sem tocar no
  runner. Gate: nenhum — é leitura.
- **Fase 2 — avisar** (F3): nome de peer da sessão corrente no stats pane; `warning` via
  `errors.report` na primeira chamada de `SendMessage`/`ListAgents` com `persistent` desligado
  ("esta sessão não recebe respostas"). Erro nunca em silêncio — hoje é um caminho mudo.
- **Fase 3 — medir** (experimentos 1–5 do doc): identidade estável com `persistent: true`;
  comportamento do CLI ocioso ao receber mensagem; `notify_when_idle` a partir de processo por
  turno; `ScheduleWakeup`; classe de permissão (`--dangerously-skip-permissions` × interativa).
  **Gate das fases seguintes** — sem o resultado, F4 é chute.
- **Fase 4 — escutar** (F4): leitor de stdout entre turnos no modo persistente e "turno não
  solicitado" no renderer, se o experimento 2 mostrar que o CLI emite eventos sozinho. Se não
  emitir, a fase encolhe para sinalizar mensagem pendente.
- **Fase 5 — cockpit** (F5): uma janela por sessão (o registro já traz `tmux: "claude:@0.%1"`),
  tráfego no meio. Depende de §4 (kind `peer.message`).

### Limites que não mudam

O Frollo **observa** o registro e o stream de tools; não fala no socket e não lê tokens. Se um dia
injetar mensagens, é decisão explícita, com testes — e muda a natureza do projeto (observador que
age), o que merece parágrafo próprio no `FROLLO.md`, não um PR.

---

## 6. Ordem sugerida de execução

| # | Item | Esforço | Impacto |
|---|------|---------|---------|
| 1 | Reinstalar hook por hash + smoke test (§2.1) | Trivial | Alto (o observer está gravando com hook de maio) |
| 2 | Trocar referências a `PLANO_MELHORIAS.md` (§2.2) e `tmp/` → `docs/` (§2.4, §2.6) | Trivial | Baixo |
| 3 | Peer messaging Fase 1 — ver (§5) | Baixo | Alto (feature nova sem tocar no runner) |
| 4 | Peer messaging Fase 2 — avisar (§5) | Baixo | Médio |
| 5 | Persistente: definir "susto" e medir uma semana (§2.3) | Baixo | Alto (destrava §5 Fases 3–4) |
| 6 | Peer messaging Fase 3 — experimentos (§5) | Baixo | Alto (decide o desenho do F4) |
| 7 | Protocolo: renderer Claude em v0, PRs 1–2 (§4) | Médio | Alto (fecha a #2, uma fonte de eventos) |
| 8 | Gravação v0 + replay mínimo (§4 PR 3, §3.3) | Médio | Alto |
| 9 | Peer messaging Fase 4 — escutar (§5) | Médio–Alto | Alto (depende do item 6) |
| 10 | Métricas comportamentais (§3.2) | Médio | Médio (depende do item 7) |
| 11 | Lint README × FROLLO e tabela de módulos sem números à mão (§2.5) | Baixo | Baixo |
| 12 | Cockpit multi-sessão (§3.4, §5 Fase 5) | Alto | Alto |
| 13 | OTLP/JSON, Runtime (§3.5) | Alto | Adiado de propósito |

---

## 7. Princípios que este documento assume

1. **Stdlib only** — segurança de supply chain, não estilo. Toda feature acima cabe em `json`,
   `pathlib`, `os`, `select`, `threading`.
2. **Erro nunca em silêncio** — todo caminho de falha novo passa por `errors.report()`. Caminho
   mudo descoberto (como o `SendMessage` sem resposta possível) vira item aqui até ganhar aviso.
3. **O observado nunca depende do observador** — hooks async, append-only, leitura de registro.
   Um Frollo que fala no socket de peers deixa de ser só observador; isso é decisão, não feature.
4. **Abstração depois da observação** — nenhuma camada nasce para um backend hipotético; duas
   implementações reais antes de unificar.
5. **Número com data** — o que está aqui foi medido (235 MB, 938 linhas, 4 nomes em 4 turnos,
   336 testes verdes em 09/09). Quando remedir, atualizar; quando estimar, dizer que é estimativa.
6. **Todo item concluído sai daqui** e, se mudou comportamento, atualiza `FROLLO.md` (canônico) e
   `README.md` (promessa pública), na forma que o `AGENT_RULES.md` prescreve.
