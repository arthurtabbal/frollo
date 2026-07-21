# AGENT_RULES.md

Regras operacionais para agentes trabalhando no Frollo. Este arquivo existe para evitar commits,
pushes, branches e mudanças de projeto feitas no impulso. Todo agente deve ler isto antes de
alterar o repositório.

## Regra de Git -- obrigatoria

> **REGRA ABSOLUTA**: o agente NUNCA deve executar `git commit` nem `git push` por conta propria.

Excecao unica: se o usuario pedir explicitamente, na propria conversa, para o agente commitar ou
pushear, o agente pode executar exatamente aquela acao. Isso nao e autorizacao permanente, nao vale
para turnos futuros e nao autoriza a outra operacao implicitamente. "Pode commitar" nao autoriza
`git push`; "pode subir" so autoriza push se o contexto disser claramente o que deve ser enviado.

### Staging

> **REGRA ABSOLUTA**: o agente nao deve executar `git add`, `git restore --staged`, `git reset`,
`git checkout --`, `git switch`, `git merge`, `git rebase`, `git branch -D` ou qualquer comando que
altere estado de Git sem pedido explicito do usuario.

O agente pode e deve usar comandos de leitura:

```bash
git status --short --branch
git diff
git diff --stat
git log --oneline --decorate --max-count=20
git branch --show-current
```

Ao terminar uma tarefa, apresente os arquivos alterados e uma sugestao de comandos para o usuario,
mas deixe staging, commit e push nas maos dele.

### Branching

`main` e codigo estavel. Desenvolvimento substancial deve acontecer em branch temporaria.

Padroes:

| Tipo | Padrao | Base |
|---|---|---|
| Feature | `feature/<descricao>` | `main` |
| Bugfix | `bugfix/<descricao>` | `main` |
| Hotfix | `hotfix/<descricao>` | `main` |
| Docs/chore | `chore/<descricao>` ou `docs/<descricao>` | `main` |

Regras:

- Nao criar, trocar, mergear ou apagar branches sem pedido explicito.
- Se o agente estiver em `main` e a tarefa envolver mudanca de codigo, avise o usuario e peca ou
  sugira uma branch antes de editar, salvo quando o usuario ja tiver autorizado trabalhar ali.
- Nunca fazer merge para `main` por iniciativa propria.
- Antes de qualquer operacao de Git que mude estado, rode e mostre o resumo de `git status`.
- Nunca reverter mudancas que o agente nao fez. Se houver alteracoes do usuario, trabalhe ao redor
  delas ou pergunte.

### Mensagens de commit

Quando sugerir mensagem, use Conventional Commits:

```text
<tipo>(<escopo>): <descricao curta>
```

Tipos aceitos: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`.

> **REGRA ABSOLUTA -- MENSAGENS DE COMMIT**: jamais usar aspas simples (`'`) ou duplas (`"`) no texto
> da mensagem. Para delimitar nomes, valores ou trechos de codigo use `()`, crases, `[]` ou `{}`.

Exemplos:

```text
docs(agent): adiciona regras de git e testes
fix(runner): preserva reasoning vazio ate o result
test(input): cobre bracketed paste com crlf
```

## Regra de testes -- obrigatoria

> **REGRA ABSOLUTA**: o agente deve rodar os testes relevantes antes de considerar qualquer tarefa
> concluida, e deve criar ou atualizar testes para toda feature nova, bugfix ou mudanca de
> comportamento observavel.

Comandos padrao do projeto:

```bash
python3 -m pytest tests/
bash tests/test_install_sh.sh
```

Use a suite inteira quando a mudanca tocar runner, input, protocolo, tools, install, hooks ou fluxo
de usuario. Para mudancas estreitas, rode primeiro o teste focado e depois a suite inteira quando o
risco justificar.

Se nao for possivel rodar testes, diga explicitamente o motivo e o risco residual.

### Onde escrever testes

- Codigo Python em `bin/lib/` deve ganhar cobertura em `tests/test_*.py`.
- Mudancas no backend Claude/Codex, protocolo de eventos ou renderizacao devem ter testes de
  contrato/unitarios nos arquivos existentes, ou um novo `tests/test_<area>.py` se fizer sentido.
- Mudancas no `install.sh` devem ser cobertas por `tests/test_install_sh.sh` quando forem
  testaveis sem alterar o sistema real.
- Mudancas em hooks devem preferir testes deterministas que validem parsing, serializacao, locks ou
  invariantes sem depender de uma sessao Claude real.
- Bugs corrigidos devem ganhar teste que falharia antes da correcao.

## Regra de documentacao

Ao adicionar feature, comando, flag, backend, capability ou alterar comportamento visivel, atualize
a documentacao junto do codigo:

| Arquivo | Quando atualizar |
|---|---|
| `README.md` | Mudancas de uso, setup, screenshots, comandos, flags ou promessa publica |
| `FROLLO.md` | Arquitetura, decisoes tecnicas, ultimas features, contratos internos |
| `AGENT_RULES.md` | Regras de trabalho dos agentes |
| `CLAUDE.md` | Apenas ponteiros para os documentos que o agente deve ler |

Nao deixe codigo e docs divergirem quando o comportamento publico mudou.

## Design de projeto

- Preserve a regra **stdlib only**: nao adicione dependencia pip, `requirements.txt` ou gerenciador de
  pacotes sem autorizacao explicita do usuario.
- Prefira os padroes existentes do repo a novas abstracoes. Adicione abstracao so quando ela reduzir
  complexidade real ou duplicacao importante.
- Mantenha adapters de backend atras de capacidades declaradas em `bin/lib/runner/capabilities.py`.
  UI nao deve decidir comportamento testando nomes de provedores quando uma capability resolve.
- Preserve o schema canonico `frollo.event.v0` em `bin/lib/runner/protocol.py`; mudancas no contrato
  exigem testes e documentacao.
- Codigo de terminal/tmux deve ser defensivo: restaure termios, encerre processos persistentes e
  evite deixar panes, PTYs ou subprocessos em estado quebrado.
- Logs e snapshots podem conter comandos e contexto sensivel. Nao amplie superficie de vazamento e
  nao envie conteudo local para servicos externos.
- Evite refactors oportunistas. Se encontrar problema fora do escopo, registre ou mencione, mas nao
  misture com a tarefa sem necessidade.

## Checklist antes de encerrar uma tarefa

1. `git status --short --branch` lido e entendido.
2. Mudancas limitadas ao escopo pedido.
3. Testes relevantes criados/atualizados.
4. Testes relevantes executados, ou impossibilidade explicada.
5. Docs atualizadas quando comportamento publico ou arquitetura mudou.
6. Nenhum `git add`, `git commit`, `git push`, merge, rebase ou troca de branch executado sem pedido
   explicito.
7. Resposta final informa arquivos alterados, testes rodados e mensagem de commit sugerida quando
   util.
