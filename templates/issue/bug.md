# Bug — <uma frase: o que quebrou, para quem>

Label: `bug`. Um bug **precisa** de reprodução que se possa seguir às cegas — é o que o contrato comum não cobra e este template sim. Tudo o mais, vale `../issue-contract.md`.

`wave lint` cobra: as seções `## Reprodução`, `## Done when` e `## Handoff`, a linha `Parent: #N`, evidência `arquivo:linha` e as contradições de branch/`Closes`.

Parent: #N

O achado, com evidência que outro confira: `arquivo:linha` no SHA da auditoria e o sintoma observado.

## Reprodução

1. Primeiro passo.
2. Segundo passo.
   Saída observada (a real, não a esperada):

   ```
   <cole a saída>
   ```

## Done when

- [ ] observável — o teste que passa ou o comando que imprime X — owner: implementer

## ADR stub

A bifurcação de design, o contexto, as opções e uma recomendação "para discordar". Curto: um bug raramente precisa da profundidade de uma story.

## Out of scope

O que não se reabre, com o porquê.

## Handoff

- `git worktree add -b fix/<N>-<slug> ../<repo>-<N> origin/<base>`
- `codegraph explore "<símbolos>"`
- O gate do repo, os skills, os inegociáveis, `Closes #N`.
