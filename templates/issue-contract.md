# O que uma issue precisa ter para `/wave` executá-la sem ninguém no meio

`wave.py lint <N…>` checa isto. Sem isto, o agente vai adivinhar — e adivinhar é o que estamos eliminando.

Este arquivo é o **índice**: o estado que mora no GitHub, a trilha de comentários, as regras do repo e o ledger do PR — partes que todo tipo de issue compartilha. O corpo da issue tem **um template por tipo de trabalho**, em `issue/` — leia o arquivo antes de escrever o corpo, não parafraseie de memória:

| Label | Template | O que o tipo acrescenta |
| --- | --- | --- |
| `bug` | [`issue/bug.md`](issue/bug.md) | `## Reprodução` com passos e a saída observada |
| `story` | [`issue/story.md`](issue/story.md) | cada item do `Done when` observável (`código`) e com `— owner:` |
| `chore` | [`issue/chore.md`](issue/chore.md) | `## Por que não é story` |
| `feature` | [`issue/feature.md`](issue/feature.md) | `## Fatia` nomeando as stories que a decompõem |

Sem label de tipo, o `lint` cobra o contrato comum (as seções `## Done when` e `## Handoff`, a linha `Parent: #N`, evidência `arquivo:linha`, as contradições de branch e de `Closes`) e **avisa** que o template do tipo não foi cobrado — issue híbrida existe. Todo template carrega o espinhaço comum acima; nenhuma seção obrigatória de hoje desaparece.

## Estrutura no GitHub (o estado mora aqui, não num chat)

- **Épico** = issue pai com label `epic`. Filhas ligadas como **sub-issues** (API `sub_issues`), não só citadas no texto. Netas idem. `/wave` só despacha **folhas** (issues sem sub-issues).
- **Dependência** = `blocked_by` da API (não "Depends on" só no texto). `wave next` só libera folha sem `blocked_by` aberto.
- Uma folha = um PR = um branch = uma worktree. Pais nunca recebem PR; fecham quando as filhas fecham (`wave close-parents`).
- Milestone opcional; o épico é a unidade.

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
