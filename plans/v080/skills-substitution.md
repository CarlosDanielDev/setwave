Parent: {{epic}}.

# A lista de skills tem um dono: o placeholder de skills no mesmo canal de GATE e PROFILE

Skip declarado no [/simplify](https://github.com/CarlosDanielDev/setwave/pull/68) e virado gap: `DEFAULT_SKILLS` (`scripts/wave.py:84`: caveman, ponytail, superpowers:tdd) alimenta a linha "Skills:" do handoff (`wave.py:2669`) enquanto o §0 do `templates/agent.md` nomeia sete skills com enquadramento próprio — duas superfícies, duas listas, zero mecanismo amarrando. O v0.7.0 mudou o §0 (setwave:* preferido) e a lista do handoff não acompanhou.

A mudança: o §0 recebe a lista por substituição — `"SKILLS"` no dict de `render_prompt` (o mesmo canal de `GATE`, `PROTECTED`, `PROFILE`), composta de `profile_skills(repo.profile())` com fallback `DEFAULT_SKILLS` já existente. O constante `DEFAULT_SKILLS` passa a refletir o que o plugin shipa (`setwave:caveman`, `setwave:ponytail`, `setwave:architecture-designer`) mais as host-only que o prompt cita, e o texto do §0 referencia `o placeholder de skills` em vez de nomear em pedra. O teste de autosuficiência continua pinando os fragmentos de regra — o que muda é que a LISTA não pode mais divergir do handoff.

## Done when

- [ ] `render_prompt` recebe `"SKILLS"`; o §0 renderiza a lista por substituição — owner: implementer
- [ ] `DEFAULT_SKILLS` reflete o shipado em v0.7.0 (`setwave:*` + host-only citadas) e a linha "Skills:" do handoff usa a mesma fonte — owner: implementer
- [ ] o teste de autosuficiência ganha: §0 renderizado contém cada skill que o `DEFAULT_SKILLS`/profile declara — owner: implementer
- [ ] `wave guarantees` ganha a linha, pinned — owner: reviewer

## ADR stub

Bifurcação: substituição no template vs constante Python importada pelo render. Contexto: GATE/PROFILE já são o canal de fatos por-repo; skills são fato do plugin+repo exatamente da mesma classe. Recomendação (para discordar): substituição — o template continua legível por humano e a lista fica testável no render.

## Out of scope

- Mudar quais skills shipam (decisão do v0.7.0, julgada em 6 rodadas).

## Handoff

- `git worktree add -b feat/{{skills-substitution.n}}-skills-channel ../setwave-{{skills-substitution.n}} origin/main`
- `codegraph explore "DEFAULT_SKILLS render_prompt profile_skills handoff_block"` e o skip anotado no commit e522489
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`
- `Closes {{skills-substitution}}`
