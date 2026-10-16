Parent: {{epic}}.

# wave release: o ritual que hoje é manual do dono

Quatro releases nesta sessão (v0.7.0, v0.7.1 e as anteriores) seguiram o mesmo ritual à mão: bump dos dois manifests `serial` num PR titulado `Release <v>`, notas redigidas dos PRs mergeados desde a última tag, merge, `git tag`, `gh release create`. O [#58](https://github.com/CarlosDanielDev/setwave/issues/58) transformou a regra em guarda; falta a ferramenta que executa o ritual sem errar o passo.

A mudança: `wave release <version>` prepara TUDO e executa o que pode: (1) confere que a árvore está limpa e que `<version>` > última tag; (2) bumpa os manifests num branch `release/<version>`; (3) redige o corpo do PR com as notas — uma linha por PR mergeado desde a última tag, do `git log` — e abre o PR; (4) imprime os passos que continuam humanos (revisar notas, merge, tag, `gh release create`). **Nada é mergeado ou taggeado sem o dono** — o helper mata o erro de digitação de versão e o esquecimento de PR, não a decisão.

## Evidência

O ritual manual toca exatamente duas linhas: `.claude-plugin/plugin.json:4` e `.claude-plugin/marketplace.json:12`, ambas `"version": "0.7.1"` hoje.

## Done when

- [ ] `wave release 0.8.1 --dry-run` imprime branch, diff dos manifests e rascunho de notas (PRs desde a última tag, uma linha cada) — owner: implementer
- [ ] sem `--dry-run`: cria branch, commita o bump, abre o PR `Release <version>: ...` — e para; merge/tag continuam do dono — owner: implementer
- [ ] recusas: árvore suja, versão ≤ última tag, workspace de outro repo — cada uma com mensagem nomeada — owner: implementer
- [ ] testes com fake gh cobrindo os 4 caminhos — owner: implementer
- [ ] README "Developing" documenta o ritual novo — owner: implementer

## ADR stub

Bifurcação: helper prepara e para vs helper mergeia e taggeia. Contexto: tag publica para todo usuário do plugin — a classe de ação que o plugin nunca toma sozinho (o adopt não reabre, o sweep não fecha, o release não publica). Recomendação (para discordar): prepara e para; a tag é um comando do dono, e o Done when do épico cobra a disciplina.

## Out of scope

- Bump/tag automáticos pós-merge (CI com secrets).
- Changelog file — as notas vivem no release do GitHub.

## Handoff

- `git worktree add -b feat/{{release-helper.n}}-wave-release ../setwave-{{release-helper.n}} origin/main`
- `codegraph explore "cmd_init cmd_plan GUARD_HOOKS"` e ler o corpo do [#69](https://github.com/CarlosDanielDev/setwave/pull/69) (o ritual manual que vira comando)
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`
- `Closes {{release-helper}}`
