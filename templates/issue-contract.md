# O que uma issue precisa ter para `/wave` executá-la sem ninguém no meio

`wave.py lint <N…>` checa isto. Sem isto, o agente vai adivinhar — e adivinhar é o que estamos eliminando.

## Estrutura no GitHub (o estado mora aqui, não num chat)

- **Épico** = issue pai com label `epic`. Filhas ligadas como **sub-issues** (API `sub_issues`), não só citadas no texto. Netas idem. `/wave` só despacha **folhas** (issues sem sub-issues).
- **Dependência** = `blocked_by` da API (não "Depends on" só no texto). `wave next` só libera folha sem `blocked_by` aberto.
- Uma folha = um PR = um branch = uma worktree. Pais nunca recebem PR; fecham quando as filhas fecham (`wave close-parents`).
- Milestone opcional; o épico é a unidade.

## Corpo da issue (o agente lê isto a frio)

1. Primeira linha: `Parent: #N.` e, se houver, `Depends on #A, #B.` (espelha o `blocked_by`, para humanos).
2. **O achado / a mudança**, com evidência que outro possa conferir: `arquivo:linha` no SHA da auditoria, saída de comando, medição. Sem `file:line`, sem issue.
3. `## Done when` — lista de caixas, cada uma **observável** (teste que passa, comando que imprime X) e com **dono** (`— owner: implementer` / `reviewer`).
4. `## ADR stub` — a bifurcação de design concreta, contexto, as opções e uma recomendação "para discordar". O PR registra a decisão em três frases.
5. `## Out of scope` — o que não se reabre, com o porquê.
6. `## Handoff` — bloco com:
   - `git worktree add -b <branch> ../<repo>-<N> origin/<base>` (é daqui que `/wave` lê o nome do branch);
   - `codegraph explore "<símbolos>"` (é daqui que `/wave` lê a query inicial);
   - o gate do repo, os skills, os inegociáveis, `Closes #N`.

## Comentários da issue = trilha de mudanças

Quando um PR muda um símbolo que outra issue aberta cita, o agente comenta nessa issue em uma linha. O agente seguinte lê os comentários antes de começar. É assim que "o que a onda 2 herda" deixa de ser texto de alguém e vira dado.

## Regras do repo em `.wave.json` (opcional, na raiz)

```json
{"base": "main",
 "gate": ["cargo fmt --check", "cargo clippy --all-targets -- -D warnings", "cargo test", "cargo deny check"],
 "protected": ["src/safety"],
 "serial": ["src/store/mod.rs"],
 "worktree_prefix": "../dev-cleaner-"}
```

`protected`: um PR que toca aqui falha no `verify`. `serial`: dois PRs que tocam aqui nunca entram no mesmo lote (lista de migrations, índice gerado, arquivo de versão) — `order` os põe em fila na ordem do `blocked_by`.

## O PR fecha o ciclo: o ledger

O corpo do PR repete a lista `## Done when` da issue com cada item marcado: `- [x]` feito, `- [ ]` não feito com uma linha do porquê, `- [ ] ~~item~~ — dropped: motivo`. Hoje é lido por olho; na v0.4.0 (#12 do repo do plugin) `verify` recusa PR sem ledger e `merge` aplica as marcas na issue — o próximo agente recebe só o que falta.

Sem o arquivo, `/wave` descobre: branch default via `gh`, gate lendo `run:` dos workflows do CI (ou o manifesto: Cargo/npm/pyproject/go), nenhum caminho protegido.
