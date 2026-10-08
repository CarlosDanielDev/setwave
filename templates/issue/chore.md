# Chore — <uma frase: o que se arruma>

Label: `chore`. Um chore **precisa dizer por que não é story**: sem mudança observável para o usuário, sem Done when de comportamento — e mesmo assim o que fica melhor, e como se confere. O resto, vale `../issue-contract.md`.

`wave lint` cobra: a seção `## Por que não é story`, as seções `## Done when` e `## Handoff`, a linha `Parent: #N`, evidência `arquivo:linha` e as contradições de branch/`Closes`.

Parent: #N

O achado, com evidência que outro confira: `arquivo:linha` no SHA da auditoria, saída de comando, medição.

## Por que não é story

Uma linha: nenhum comportamento muda para o usuário; o ganho é <manutenção, higiene, custo>.

## Done when

- [ ] observável — o teste que passa ou o comando que imprime X — owner: implementer

## ADR stub

A bifurcação de design, o contexto, as opções e uma recomendação "para discordar". Curto.

## Out of scope

O que não se reabre, com o porquê.

## Handoff

- `git worktree add -b chore/<N>-<slug> ../<repo>-<N> origin/<base>`
- `codegraph explore "<símbolos>"`
- O gate do repo, os skills, os inegociáveis, `Closes #N`.
