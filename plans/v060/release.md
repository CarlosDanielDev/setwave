Parent: #{{epic}}.

# Release 0.6.0: os manifests dizem a versão que existe

Os manifests `.claude-plugin/plugin.json` e `.claude-plugin/marketplace.json` (par `serial` de `.wave.json:8`) hoje dizem **0.6.1** — número que nunca foi taggeado, producto de sete bumps fantasma fora de release (#50–#56). A versão publicada mais recente é [v0.5.0](https://github.com/CarlosDanielDev/setwave/releases/tag/v0.5.0). Este PR corrige os manifests para **0.6.0** e prepara o release desta leva: as notas cobrem os 8 PRs de [#50](https://github.com/CarlosDanielDev/setwave/pull/50) a [#57](https://github.com/CarlosDanielDev/setwave/pull/57) (clone com limiar, `wave epics`, perfis, `wave adopt`, token só do stdout, cleanup por PR, `wave prepare`, wizard de primeiro run).

O ritual, igual ao do [#49](https://github.com/CarlosDanielDev/setwave/pull/49): um PR só de manifests (são `serial`), título `Release 0.6.0: ...`, notas de release rascunhadas no corpo; **tag + release no GitHub são passo do dono depois do merge** (o Done when do épico cobre).

## Done when

- [ ] `.claude-plugin/plugin.json` e `marketplace.json` em **0.6.0**, um commit só, nenhum outro arquivo tocado — owner: implementer
- [ ] notas de release rascunhadas no corpo do PR: uma linha por PR de [#50](https://github.com/CarlosDanielDev/setwave/pull/50)–[#57](https://github.com/CarlosDanielDev/setwave/pull/57), sem prometer o que não existe (routing por tier e TTL lock ditos como fora) — owner: implementer
- [ ] gate completo colado no PR (suite inteira + `wave guarantees`) — owner: implementer
- [ ] título do PR começa com `Release 0.6.0` (a convenção que a [#58](https://github.com/CarlosDanielDev/setwave/issues/58) vai cobrar) — owner: reviewer

## ADR stub

Bifurcação: **v0.6.0** (downgrade dos manifests) vs v0.6.1 (seguir o fantasma). Contexto: tag que nunca existiu não é versão para ninguém; downgrade de um número não-publicado é correção, não regressão. Recomendação (para discordar): v0.6.0 — mesmo nome do milestone, primeira execução honesta da convenção de Release.

## Out of scope

- Tag e release no GitHub (dono, pós-merge).
- A guarda de versão em si (é a [#58](https://github.com/CarlosDanielDev/setwave/issues/58), folha irmã).
- Notas para PRs anteriores à [#50](https://github.com/CarlosDanielDev/setwave/pull/50) (já estão na v0.5.0).

## Handoff

- `git worktree add -b feat/{{release.n}}-release-060 ../setwave-{{release.n}} origin/main`
- `codegraph explore "cmd_verify GUARANTEES"` e ler `.wave.json:8` (o par `serial`) + o corpo do [#49](https://github.com/CarlosDanielDev/setwave/pull/49) (o formato do PR de Release)
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`
- Inegociáveis: sem atribuição de IA; sem git destrutivo; nenhum arquivo além dos dois manifests.
- `Closes #{{release}}`
