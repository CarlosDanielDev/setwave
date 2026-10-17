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
- [ ] `hooks status` e `doctor` reconhecem a entrada project-relative `python3 "$CLAUDE_PROJECT_DIR/hooks/guards.py" <guard>` quando ela resolve para o `hooks/guards.py` do próprio projeto, e `hooks install` não duplica entradas já cobertas por ela — owner: implementer
- [ ] `status`/`doctor` nunca executam um caminho lido do `settings.json`: só o `HOOKS_SCRIPT` do plugin é pingado. Qualquer outro alvo recebe apenas `is_file()` e um veredito `untrusted`/`missing`, sem `python3 <alvo>` — owner: implementer
- [ ] uma entrada project-relative conta como ativa só se resolve para o `hooks/guards.py` do próprio projeto **e** o conteúdo (sha256) é idêntico ao do `HOOKS_SCRIPT` do plugin; um `guards.py` de qualquer outro lugar, ou com conteúdo diferente, é reportado `untrusted`, nunca `active` — owner: implementer
- [ ] testes: um `settings.json` apontando para um script-sentinela que cria um arquivo ao rodar → `hooks status` e `doctor` não criam o arquivo; um `guards.py` com o mesmo nome e conteúdo diferente → `untrusted` — owner: implementer

## ADR stub

Bifurcação: path estável (copiar guards.py para ~/.config/setwave/) vs detectar drift. Contexto: copiar cria segunda fonte de verdade (a guarda do projeto diverge da do plugin); detectar mantém fonte única e o re-install é um comando. Recomendação (para discordar): detectar + documentar — a cópia é o tipo de conveniência que vira dois códigos разных em produção.

## Contexto do #72

O PR do dogfood (#72) descobriu que o `settings.json` commitado não pode carregar o path absoluto do dono: em outro clone o script não existe, `python3` sai com 2 e o hook `PreToolUse` bloqueia todo Bash. A forma project-relative funciona, mas `status`/`doctor` hoje comparam a string absoluta de `guard_command(name)` e a leem como `missing`. O #72 espera este item.

## Segurança

Revisão de segurança automática do PR deste leaf (2026-10-17) achou dois problemas na primeira versão da branch: `status` executava `python3 <path do settings.json> <guarda>` para qualquer alvo existente (um repo clonado controla esse arquivo), e contava como instalada qualquer entrada cujo basename fosse `guards.py`. O `doctor` roda fora do Claude Code e é diagnóstico: não pode executar código do repo que diagnostica. Os três itens acima fecham isso.

## Out of scope

- Instalar por link simbólico (quebra em setups de sync de dotfiles).

## Handoff

- `git worktree add -b feat/{{hooks-drift.n}}-hooks-drift ../setwave-{{hooks-drift.n}} origin/main`
- `codegraph explore "HOOKS_SCRIPT cmd_hooks cmd_doctor"` e ler o modelo de cache do plugin
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`
- `Closes {{hooks-drift}}`
