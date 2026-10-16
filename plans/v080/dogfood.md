Parent: {{epic}}.

# Dogfood: o próprio setwave roda com as guardas ativas

`wave doctor` no repo do plugin, 2026-10-16: **"guard hooks installed: 0/4 active"** — o projeto que vende as guardas de ambiente não roda com elas. O [#35](https://github.com/CarlosDanielDev/setwave/pull/35) fez as guardas opt-in por projeto, e ninguém as instalou aqui. Além do constrangimento, é o único caminho de descobrir na pele o que o dono de qualquer projeto vai sentir ao instalar.

A mudança: `wave hooks install` neste repo (e o commit do `.claude/settings.json` resultante — as guardas passam a valer para todo contribuidor que abrir o projeto), mais a linha no README "Developing": instalar as guardas é parte do setup de quem desenvolve o plugin. O `doctor` passa a reportar 4/4 e o épico cobra no Done when.

## Evidência

`scripts/wave.py:3023` é o `cmd_hooks` que instala as guardas; `scripts/wave.py:2826` é o `cmd_doctor` que reporta `guard hooks installed: 0/4 active` neste repo, em 2026-10-16.

## Done when

- [ ] `wave hooks install` rodado neste repo; `.claude/settings.json` commitado com as 4 guardas — owner: implementer
- [ ] `wave doctor` reporta 4/4 sem o `!` — owner: implementer
- [ ] README "Developing" ganha o passo de setup — owner: implementer
- [ ] um commit de teste real da guarda: tentar commit com atribuição de IA num branch descartável → negado pelo hook (saída colada no PR) — owner: implementer

## ADR stub

Bifurcação: commitar o settings com as guardas vs deixar opt-in por dev. Contexto: guardas fail-open e baratas; contributor que não as quer desliga no próprio settings local. Recomendação (para discordar): commitar — o projeto que não come o próprio dogfood não descende os buracos do caminho.

## Out of scope

- Mudar o mecanismo de install (o [{{hooks-drift}}]({{hooks-drift}}) cuida do drift de path).

## Handoff

- `git worktree add -b feat/{{dogfood.n}}-dogfood-hooks ../setwave-{{dogfood.n}} origin/main`
- `codegraph explore "cmd_hooks GUARD_HOOKS"`; rodar `python3 scripts/wave.py hooks install` no checkout principal ANTES de abrir o PR (este leaf escreve no repo host de propósito)
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`
- `Closes {{dogfood}}`
