# Feature — <uma frase: a capacidade nova>

Label: `feature`. Uma feature **precisa da `## Fatia`**: as stories que a decompõem, uma por linha — viram sub-issues e é nelas que `/wave` despacha. O resto, vale `../issue-contract.md`.

`wave lint` cobra: a seção `## Fatia` com pelo menos uma story em lista, as seções `## Done when` e `## Handoff`, a linha `Parent: #N`, evidência `arquivo:linha` e as contradições de branch/`Closes`.

Parent: #N

A capacidade, com evidência que outro confira: o problema de hoje, a medição, `arquivo:linha`.

## Fatia

- <story 1: a primeira fatia observável>
- <story 2: a segunda>
- <story 3: a que fecha>

## Done when

- [ ] todas as stories fechadas por PR mergeado — owner: implementer

## ADR stub

A bifurcação de design concreta, contexto, opções e uma recomendação "para discordar". O PR registra a decisão em três frases.

## Out of scope

O que não se reabre, com o porquê.

## Handoff

- `git worktree add -b feat/<N>-<slug> ../<repo>-<N> origin/<base>` — só quando a feature for executada como folha; pais nunca recebem PR.
- `codegraph explore "<símbolos>"`
- O gate do repo, os skills, os inegociáveis, `Closes #N`.
