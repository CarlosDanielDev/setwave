# Story — <uma frase: a mudança, o valor>

Label: `story`. O que distingue uma story: **cada item do Done when é observável e tem dono** — um teste que passa ou um comando que imprime X, e quem responde por ele. O resto, vale `../issue-contract.md`.

`wave lint` cobra: um `## Done when` com item observável (`código` no texto) e `— owner:` em cada linha, as seções `## Done when` e `## Handoff`, a linha `Parent: #N`, evidência `arquivo:linha` e as contradições de branch/`Closes`.

Parent: #N

O achado / a mudança, com evidência que outro confira: `arquivo:linha` no SHA da auditoria, saída de comando, medição.

## Done when

- [ ] observável — o teste que passa ou o comando que imprime X (`python3 -m unittest discover -s tests`) — owner: implementer
- [ ] segundo item, dono reviewer confere a diff — owner: reviewer

## ADR stub

A bifurcação de design concreta, contexto, opções e uma recomendação "para discordar". O PR registra a decisão em três frases.

## Out of scope

O que não se reabre, com o porquê.

## Handoff

- `git worktree add -b feat/<N>-<slug> ../<repo>-<N> origin/<base>`
- `codegraph explore "<símbolos>"`
- O gate do repo, os skills, os inegociáveis, `Closes #N`.
