Parent: {{epic}}.

# hooks drift: o path do guards.py morre quando o plugin atualiza

`HOOKS_SCRIPT = HERE.parent / "hooks" / "guards.py"` (`scripts/wave.py:2976`): o `wave hooks install` grava no settings do projeto um comando apontando para o `guards.py` **de onde o wave.py rodou** — tipicamente o cache versionado do plugin (`~/.claude/plugins/cache/setwave/setwave/<versão>/...`). O plugin atualiza → o cache troca de diretório de versão → o hook do projeto aponta para um path que não existe mais → a guarda morre silenciosamente (fail-open: a ausência é `exit 0` por construção). O `hooks status` checa o settings, não o alvo.

A mudança: (1) `wave hooks status` resolve o path gravado no settings e reporta `missing target` quando o `guards.py` não existe mais — o doctor ganha a mesma checagem; (2) o README documenta o modelo de path (cache versionado = reinstalar `hooks install` após update do plugin); (3) `hooks install --check` imprime para cada guarda o alvo atual e se ele existe.

## Done when

- [ ] `hooks status` reporta `missing target` com o path gravado quando o `guards.py` sumiu — owner: implementer
- [ ] `doctor` reclama (`!`) de hook com alvo inexistente — owner: implementer
- [ ] `hooks install --check` lista alvo + existência por guarda — owner: implementer
- [ ] testes com settings apontando para path inexistente — owner: implementer
- [ ] README documenta o modelo e o re-install pós-update — owner: implementer

## ADR stub

Bifurcação: path estável (copiar guards.py para ~/.config/setwave/) vs detectar drift. Contexto: copiar cria segunda fonte de verdade (a guarda do projeto diverge da do plugin); detectar mantém fonte única e o re-install é um comando. Recomendação (para discordar): detectar + documentar — a cópia é o tipo de conveniência que vira dois códigos разных em produção.

## Out of scope

- Instalar por link simbólico (quebra em setups de sync de dotfiles).

## Handoff

- `git worktree add -b feat/{{hooks-drift.n}}-hooks-drift ../setwave-{{hooks-drift.n}} origin/main`
- `codegraph explore "HOOKS_SCRIPT cmd_hooks cmd_doctor"` e ler o modelo de cache do plugin
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`
- `Closes {{hooks-drift}}`
