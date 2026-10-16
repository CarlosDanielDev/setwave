Parent: {{epic}}.

# architecture-designer inteiro: as references/ que ficaram para trás

O judge da v0.7.0 aceitou o skill com ressalva: a Reference Guide cita cinco `references/*.md` que não shipam, e o banner avisa o agente em runtime de que aquilo é ponteiro, não arquivo. MIT permite trazer — o gap é que o skill shipado é uma versão amputada da que o upstream distribui.

A mudança: baixar as `references/` do upstream ( Jeffallan, MIT — o mesmo direito que já cobre o SKILL.md), shipar em `skills/architecture-designer/references/`, remover o banner (a condição dele acabou) e atualizar o LICENSE-NOTICE ("vendored verbatim" volta a ser verdade — com as references). O skill-lint valida que nenhum `references/*.md` citado deixe de existir no diretório — a checagem que o gate não pegava.

## Evidência

O banner que esta folha remove está em `skills/architecture-designer/SKILL.md:43`, no topo da seção Reference Guide; a regra nova entra em `problems` de `scripts/validate_skills.py:35`.

## Done when

- [ ] `skills/architecture-designer/references/` com os 5 arquivos citados, verbatim upstream — owner: implementer
- [ ] banner de "not shipped" removido do SKILL.md; LICENSE-NOTICE atualizado — owner: implementer
- [ ] `validate_skills.py` ganha a regra: referência citada tem que existir no diretório do skill — owner: implementer
- [ ] mutation-check da regra nova (remova um reference → linter falha) — owner: implementer
- [ ] README/THIRD_PARTY.md atualizados — owner: implementer

## ADR stub

Bifurcação: trazer as references vs manter o banner. Contexto: banner = débito visível para o agente em todo uso; references = ~KB de conteúdo MIT que completa o skill. Recomendação (para discordar): trazer — a regra nova no linter é o valor permanente; o conteúdo é quase detalhe.

## Out of scope

- Skill de upstream que não seja MIT confirmado.

## Handoff

- `git worktree add -b feat/{{vendored-references.n}}-references ../setwave-{{vendored-references.n}} origin/main`
- Upstream: https://github.com/Jeffallan (o LICENSE-NOTICE do skill cita a fonte)
- Gate: `python3 scripts/validate_skills.py && python3 -m unittest discover -s tests -v`
- `Closes {{vendored-references}}`
