# Mensagens entre sessões (peer messaging) e o Frollo

Rascunho de exploração, 2026-09-09. Registra um achado de campo feito **de dentro de uma sessão
conduzida pelo Frollo** e propõe features. Nada aqui está implementado; a seção "Experimentos"
diz o que precisa ser medido antes de codar.

## O achado

O Claude Code (2.1.x) tem um mecanismo de **mensagens entre sessões na mesma máquina**. Do lado do
modelo aparecem duas tools:

- `ListAgents` — lista subagentes, teammates e **outras sessões Claude locais**, cada uma com um
  nome (`evals-cb`, `central-vpn-29`), um ref curto (`[42898e]`), o tipo (`interactive`), o
  status (`busy`/`idle`) e há quanto tempo começou.
- `SendMessage` — envia texto para um desses nomes. A mensagem chega no destinatário como
  `<cross-session-message from="…">` e é processada **no próximo tool round dele**. Há uma opção
  `notify_when_idle` que assina um aviso único de "essa sessão ficou ociosa".

Cenário em que isso foi visto: uma sessão coordenadora rodando no Frollo (cwd `noharm-cli`)
despachou briefings para duas sessões `claude` interativas abertas em outros repositórios. O envio
funcionou nas duas. O caminho de volta, não — e o motivo é o modo como o Frollo conduz a sessão.

### O sintoma

Em quatro turnos consecutivos da mesma conversa (mesmo transcript, retomado com `--resume`), a
sessão do Frollo se apresentou com **quatro identidades diferentes**:

| turno | nome | ref |
|---|---|---|
| 1 | `noharm-cli-b9` | `b7bd0b` |
| 2 | `noharm-cli-a5` | `97b77f` |
| 3 | `noharm-cli-8f` | `7e5a08` |
| 4 | `noharm-cli-69` | — |

As duas sessões interativas mantiveram nome e ref estáveis o tempo todo. Qualquer instrução do tipo
"me responda em `noharm-cli-a5`" morreu no turno seguinte: o endereço não existe mais.

### O mecanismo (visto em disco)

A identidade de peer é **por processo**, não por transcript. O registro vive em:

```
~/.claude/sessions/<pid>.json          # 0644 — metadados públicos do peer
~/.claude/sessions/<pid>.<hash>.key    # 0600 — peerToken; NUNCA ler/logar
$XDG_RUNTIME_DIR/cc-socks/<pid>.sock   # unix socket de mensagens, um por processo
```

Campos relevantes do `.json` (valores reais da sessão do Frollo, sem segredo):

```json
{
  "pid": 1289768,
  "sessionId": "13ea4449-…",
  "cwd": "/home/arthur/noharm/dev/noharm-cli",
  "kind": "interactive",
  "entrypoint": "sdk-cli",
  "peerProtocol": 1,
  "peerFeatures": ["notify_idle", "reply_across_default_dirs", "artifact_yield"],
  "messagingSocketPath": "/run/user/1000/cc-socks/1289768.sock",
  "name": "noharm-cli-69",
  "nameSource": "derived",
  "status": "busy",
  "tmux": "claude:@0.%1"
}
```

Observações:

- `name` é derivado do basename do `cwd` + dois hex, gerado **no start do processo**.
- Sessões interativas registram `entrypoint: "cli"`; a do Frollo, `"sdk-cli"` (efeito do `--print`
  + `--input-format stream-json`). O campo `kind` é `interactive` nos dois casos — não serve para
  distinguir.
- `status` alterna `busy`/`idle` com `statusUpdatedAt` — é o que o `ListAgents` mostra.
- O `.key` carrega um token de autenticação do peer. O Frollo pode ler o `.json`; **não deve
  tocar no `.key`**.

### Por que o Frollo quebra o caminho de volta

`lib/runner/__init__.py::_ensure_proc` no modo default (`persistent: false`) spawna um processo
`claude --print … --resume <session_id>` **por turno**. Cada processo:

1. ganha um pid → um `~/.claude/sessions/<pid>.json` novo → um nome novo → um socket novo;
2. morre no fim do turno → o socket some → mensagens endereçadas a ele não têm para onde ir.

Entre dois turnos **não existe processo**; logo, não existe "próximo tool round" em que uma mensagem
recebida pudesse ser drenada. A sessão do Frollo é, para a rede de peers, só emissora.

Com `persistent: true` (Fase 4, já no código) o processo sobrevive entre turnos, então o pid, o nome
e o socket **deveriam** ficar estáveis. Mas o Frollo só lê `proc.stdout` dentro de `run_turn`; se
uma mensagem recebida fizer o CLI emitir eventos enquanto ninguém lê, o pipe (64 KB) enche e o
processo bloqueia — o mesmo backpressure que a Fase 3 resolveu para o typewriter, agora entre turnos.

## O que isso habilita (e por que interessa ao Frollo)

A tese do projeto é observabilidade: "o que o agente está fazendo agora?". Com várias sessões
conversando, a pergunta ganha uma dimensão: **quem está falando com quem, e o que já foi
combinado?** Hoje esse tráfego é invisível — só aparece como um `SendMessage` no pane de tools de
quem enviou, e como um bloco de texto no turno de quem recebeu.

Caso de uso concreto que motivou o achado: uma sessão coordenadora (visão de cima de um ciclo que
atravessa quatro repositórios) e duas sessões executoras, uma por repositório, cada uma carregando
o `CLAUDE.md` do seu repo. A coordenadora despacha briefings, cobra contratos transversais e lê o
placar; as executoras trabalham e reportam. O Frollo é o lugar natural para *ver* essa orquestra —
desde que ele mesmo não quebre o canal.

## Features propostas

Em ordem de custo/valor. As duas primeiras não dependem de nada além do que já existe no repo.

### F1 — Pane de peers (só leitura, stdlib)

Ler `~/.claude/sessions/*.json` (ignorando `.key`), filtrar pids vivos (`os.kill(pid, 0)` ou
`/proc/<pid>`), e renderizar uma lista: nome, cwd (basename), status, há quanto tempo, e se é a
própria sessão do Frollo. É o `ListAgents` sem gastar turno do modelo. Encaixa no stats pane ou num
pane novo, atualizado pelo mesmo laço de 150 ms.

Aceite: abrir dois `claude` interativos em outros diretórios e vê-los aparecer/sumir no pane, com
status mudando de `idle` para `busy` quando um deles roda um turno.

### F2 — Tráfego de peers no observer

O hook global (`hooks/log.sh`) já captura `PreToolUse` de **toda** sessão da máquina, com
`tool_name` e `tool_input`. Para `tool_name == "SendMessage"` o `tool_input` traz `to`, `summary` e
`message`; para `ListAgents`, nada além do nome. Basta `observe.sh` (e o pane de tools do cliente)
renderizarem `SendMessage` como uma linha de tráfego: `sessão-A → sessão-B: <primeira linha>`.

Cuidado: `message` pode conter contexto sensível (é texto livre entre agentes). Mostrar só a
primeira linha, que o próprio protocolo trata como preview. Mesmo raciocínio da decisão de manter
`tool_input` em plaintext no `observer.jsonl` — local, não sai da máquina.

### F3 — Identidade estável: `persistent` como pré-requisito e um aviso quando não está ligado

Com `persistent: true` o nome de peer deve ficar fixo. Duas coisas a fazer:

- exibir o nome de peer da sessão corrente no stats pane (vem do `.json` cujo `pid` é
  `client.proc.pid`) — o usuário passa a saber "como me chamam";
- quando `persistent` estiver desligado e o modelo chamar `SendMessage`/`ListAgents` (visível no
  stream de tools), avisar uma vez por sessão, via `errors.report(…, severity="warning")`: "no
  modo por turno esta sessão não recebe respostas de peers". Erro nunca em silêncio — e este é
  exatamente um caminho mudo hoje.

### F4 — Inbox entre turnos (o pedaço difícil)

No modo persistente, um leitor de `proc.stdout` **entre** turnos. Duas hipóteses sobre o que o CLI
faz quando recebe uma mensagem de peer estando ocioso em `--print`/stream-json:

- **(a)** inicia um turno sozinho e emite eventos → o Frollo precisa de um "turno não solicitado":
  renderizar no chat com um marcador distinto (mensagem de `evals-cb`), com typewriter, e sem
  quebrar a máquina `Turn` (que hoje nasce dentro de `run_turn`);
- **(b)** só enfileira e drena quando o próximo turno começar → nada sai do stdout entre turnos, e o
  que falta é apenas o Frollo *sinalizar* que há mensagem pendente (não há API pública para isso;
  ver Experimentos).

Em qualquer das duas, a mensagem recebida aparece para o modelo como
`<cross-session-message from="…">`. Vale detectar esse marcador no texto do turno e renderizá-lo
como citação, não como fala do assistente.

### F5 — Frollo como cockpit de orquestração (direção, não spec)

Se F1–F4 ficarem de pé, o próximo passo natural é um layout com **uma janela por sessão** (o
registro traz `tmux: "claude:@0.%1"` — a sessão já sabe em que pane vive) e o pane de tráfego
mostrando o fio entre elas. Isso conversa com a seção "Direção Futura" do `FROLLO.md`: o protocolo
`frollo.event.v0` ganharia um kind `peer.message` com `from`, `to`, `preview`.

## Experimentos antes de codar

1. **Identidade com `persistent: true`.** Ligar a flag, rodar dois turnos que chamem `ListAgents`
   e comparar o nome próprio. Esperado: igual. Se mudar, a hipótese "identidade por pid" está
   incompleta.
2. **Inbound em modo persistente, sessão ociosa.** Com o processo vivo e sem turno, mandar uma
   mensagem de um `claude` interativo para o nome da sessão do Frollo. Observar `proc.stdout` com
   um leitor provisório (ou `strace -p` no fd 1) — sai evento ou não? Decide entre F4(a) e F4(b).
3. **`notify_when_idle` de dentro do Frollo.** A assinatura foi feita neste achado (às duas sessões
   executoras) a partir de um processo por turno. Se um aviso chegar num turno futuro, ele chegou
   por transcript e não por processo — isso muda a leitura de "identidade por pid" e abre uma via
   de push mesmo no modo default. Se não chegar, confirma pull-only.
4. **`ScheduleWakeup`/loop dinâmico.** A tool existe no toolset da sessão do Frollo. No modo por
   turno não há quem a honre (o processo acaba). No persistente, ver se o CLI se auto-acorda e
   emite eventos — cai no mesmo problema de leitor do experimento 2.
5. **Classe de permissão.** O retorno do `notify_when_idle` diz que o aviso só é entregue se a outra
   sessão "roda na mesma classe de permissão ou não afirma nenhuma". O Frollo em modo `auto` passa
   `--dangerously-skip-permissions`; as interativas não. Medir se isso bloqueia entrega em algum
   sentido.

## Limites e regras a respeitar

- **Não ler `*.key`.** É credencial do protocolo de peers. F1 deve fazer glob de `*.json` apenas.
- **Não falar no socket.** O transporte não é público; o Frollo observa o registro e o stream de
  tools, não injeta mensagens. Se um dia isso mudar, é decisão explícita, com testes.
- **Stdlib only** continua valendo: `json`, `pathlib`, `os`, `time` bastam para F1–F3.
- **Não confundir `sessionId` com identidade de peer.** O primeiro sobrevive ao `--resume`; o
  segundo não. O Frollo já guarda o `sessionId` (evento `result`) — são dois conceitos, dois campos.

## Referências no código

- `bin/lib/runner/__init__.py` — `_ensure_proc` (spawn por turno vs persistente), `run_turn` (único
  lugar que lê `proc.stdout`).
- `bin/lib/config.py` — flag `persistent` (default `False`).
- `hooks/log.sh` + `bin/observe.sh` — captura e render do `PreToolUse` global (base de F2).
- `bin/lib/errors.py` — sink para o aviso de F3.
- `FROLLO.md` › "Últimas features" › *Processo `claude` persistente* — achado de que o processo não
  sai sozinho ao fechar stdin (relevante para o leitor entre turnos de F4).
