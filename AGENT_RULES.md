# AGENT_RULES.md

Regras de convivencia para agentes trabalhando no Frollo.

O Frollo e um projeto de codigo, mas tambem e uma homenagem a propria experiencia de trabalhar com
IA. A ideia aqui nao e deixar o agente esteril: e proteger o repositorio das acoes irreversiveis e
preservar o tom vivo do projeto enquanto ele amadurece.

Guia curto: seja criativo no projeto, conservador no Git.

Na duvida, leia `O Rascunho Fundador` em `FROLLO.md`.

## Git

Esta e a parte dura.

O agente nao deve executar por iniciativa propria:

- `git add`
- `git commit`
- `git push`
- `git switch`
- `git merge`
- `git rebase`
- `git reset`
- `git checkout --`
- `git restore --staged`
- apagamento de branch

Essas operacoes so podem acontecer quando o usuario pedir explicitamente na conversa atual. A
autorizacao vale apenas para a acao pedida, naquele momento. "Pode commitar" nao autoriza push.
"Pode subir" so autoriza push se o contexto disser claramente o que deve ser enviado.

Comandos de leitura sao bem-vindos:

```bash
git status --short --branch
git diff
git diff --stat
git log --oneline --decorate --max-count=20
git branch --show-current
```

Antes de qualquer operacao de Git que mude estado, confira e comunique o resumo de `git status`.
Nunca reverta mudancas que voce nao fez.

## Branches

`main` e o chao estavel do projeto. Mudancas substanciais devem nascer em uma branch temporaria,
mas o agente nao cria nem troca branches sem pedido explicito.

Padroes sugeridos:

| Tipo | Padrao | Base |
|---|---|---|
| Feature | `feature/<descricao>` | `main` |
| Bugfix | `bugfix/<descricao>` | `main` |
| Hotfix | `hotfix/<descricao>` | `main` |
| Docs/chore | `docs/<descricao>` ou `chore/<descricao>` | `main` |

Nao faca merge para `main` por iniciativa propria.

## Commits

Quando o usuario pedir um commit, use Conventional Commits:

```text
<tipo>(<escopo>): <descricao curta>
```

Tipos comuns: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`.

Evite aspas simples ou duplas na mensagem de commit. Para delimitar nomes, valores ou trechos de
codigo, prefira `()`, crases, `[]` ou `{}`.

Exemplos:

```text
docs(agent): suaviza regras de trabalho
fix(runner): preserva reasoning vazio ate o result
test(input): cobre bracketed paste com crlf
```

## Testes

Teste e parte da forma do projeto, nao um ritual separado. Quando houver feature nova, bugfix ou
mudanca de comportamento observavel, crie ou atualize testes.

Comandos padrao:

```bash
python3 -m pytest tests/
bash tests/test_install_sh.sh
```

Rode a suite inteira quando a mudanca tocar runner, input, protocolo, tools, install, hooks ou fluxo
de usuario. Para mudancas estreitas, um teste focado pode vir primeiro; depois amplie quando o risco
pedir. Se nao der para rodar testes, diga por que e qual risco fica.

Onde colocar:

- Python em `bin/lib/`: `tests/test_*.py`.
- Backend Claude/Codex, protocolo ou renderizacao: testes de contrato/unitarios perto dos testes
  existentes.
- `install.sh`: `tests/test_install_sh.sh`, quando testavel sem alterar o sistema real.
- Hooks: testes deterministas de parsing, serializacao, locks ou invariantes.
- Bug corrigido: teste que falharia antes da correcao.

## Documentacao

Se a mudanca altera uso, comportamento publico, arquitetura ou contrato interno, atualize o mapa
junto da estrada.

| Arquivo | Quando mexer |
|---|---|
| `README.md` | Uso, setup, screenshots, comandos, flags, promessa publica |
| `FROLLO.md` | Arquitetura, decisoes tecnicas, ultimas features, contratos internos |
| `AGENT_RULES.md` | Modo de trabalho dos agentes |
| `CLAUDE.md` | Ponteiros para documentos que o agente deve ler |
| `.codex/config.toml` | Adaptador de configuracao do Codex para ler a documentacao canonica |

### Documentacao canonica de agentes

Evite duplicar o mesmo conteudo em arquivos especificos de agentes. A fonte canonica do projeto e
`FROLLO.md`; arquivos como `CLAUDE.md`, `.codex/config.toml` ou futuros adaptadores devem apenas
ensinar cada ferramenta a encontrar essa fonte.

Quando um novo backend de agente estiver nascendo, use o agente nativo dele ate o primeiro MVP
funcional. Depois que `frollo --backend <nome>` conseguir conduzir turnos reais, prefira dogfooding
pelo proprio Frollo e use o agente nativo como escape hatch de depuracao.

## Design

- Preserve a regra **stdlib only**. Nao adicione dependencia pip, `requirements.txt` ou gerenciador de
  pacotes sem conversa explicita.
- Prefira os padroes existentes do repo. Abstracao nova precisa pagar aluguel: reduzir complexidade
  real, duplicacao importante ou expressar uma divisao que o projeto ja esta pedindo.
- Mantenha adapters de backend atras de capacidades em `bin/lib/runner/capabilities.py`.
- Preserve o schema canonico `frollo.event.v0` em `bin/lib/runner/protocol.py`; mudancas no contrato
  pedem testes e documentacao.
- Codigo de terminal/tmux deve ser cuidadoso com termios, PTYs, panes e subprocessos persistentes.
- Erro nunca passa em silencio. Todo caminho de falha novo deve chamar `errors.report()`
  (`bin/lib/errors.py`), que ja escreve no chat, no pane de tools e em `errors.jsonl`. `except: pass`,
  `return []` para mensagem desconhecida e timeout mudo sao bug: o usuario fica olhando spinner sem
  saber que quebrou. Se a falha for degradacao esperada, use `chat=False` — some da conversa, nunca
  do log. Ver a secao `Erros nunca em silencio` em `FROLLO.md`.
- Logs e snapshots podem conter comandos e contexto sensivel. Nao amplie a superficie de vazamento.
- Evite refactors oportunistas. Se encontrar algo fora do escopo, mencione ou registre, mas nao
  misture sem necessidade.
- Preserve a estranheza boa do Frollo. As gargulas, o typewriter, o braseiro e o thinking pane nao
  sao enfeite colado; eles sao parte da tese do projeto.

## Antes de encerrar

Confira:

- `git status --short --branch` lido e entendido.
- Mudancas limitadas ao escopo.
- Testes relevantes criados ou atualizados.
- Testes relevantes executados, ou impossibilidade explicada.
- Docs atualizadas quando necessario.
- Nenhum Git mutante executado sem pedido explicito.
- Resposta final com arquivos alterados, testes rodados e, quando util, mensagem de commit sugerida.
