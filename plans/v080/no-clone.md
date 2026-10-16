Parent: {{epic}}.

# Comando de leitura nunca clona: wave epics puxou um repo sem ninguém pedir

Em 2026-10-08, um `wave epics` — comando de leitura, visão dos épicos — **clonou o setwave-sandbox** do nada: a iteração de épicos passa por `Repo.get` (`scripts/wave.py:228` na origem), e `Repo.get` clona repo sem checkout (o [#50](https://github.com/CarlosDanielDev/setwave/pull/50) colocou o clone aí, certo para `dispatch`, errado para leitura). O log da sessão registra: "cloned CarlosDanielDev/setwave-sandbox into ~/projects/setwave-sandbox". Comando que o dono roda só para OLAR não pode escrever em disco nem no registry.

A mudança: clone explícito só onde o trabalho exige o checkout — `next`, `dispatch`, `prepare`, `prompt` (caminhos de execução). Comandos de leitura (`epics`, `status`, `why`, `stats`, `guarantees`) recebem de `Repo.get` um modo `read`: repo sem checkout aparece como `not cloned` na saída, e a linha manda o comando que resolve (`wave epics --slug owner/name` a partir de qualquer checkout, ou o `gh repo clone` impresso). Nada é clonado, nada é registrado.

## Reprodução

1. Ter no registry (`~/.config/setwave/repos.json`) um repo que não tem checkout local — o `setwave-sandbox` estava assim.
2. Rodar `wave epics` (comando só de leitura) de qualquer diretório.
3. Saída observada: `CarlosDanielDev/setwave-sandbox is 0.0 MB on GitHub` e `cloned CarlosDanielDev/setwave-sandbox into /Users/carlos/projects/setwave-sandbox`; o `repos.json` ganhou o path. Esperado: nenhuma escrita em disco nem no registry.

## Done when

- [ ] `wave epics`/`status`/`why`/`stats`/`guarantees` nunca chamam `clone_repo` — repo ausente aparece como `not cloned`, com o comando de resolução — owner: implementer
- [ ] `dispatch`/`next`/`prepare`/`prompt` mantêm o clone (é o trabalho deles) — owner: implementer
- [ ] testes com fake gh: leitura com repo ausente → `not cloned`, zero writes no registry; execução com repo ausente → clona — owner: implementer
- [ ] mutation-check documentado no PR (a guarda removida → `epics` clona de novo → teste falha) — owner: implementer
- [ ] `wave guarantees` ganha a linha, pinned — owner: reviewer

## ADR stub

Bifurcação: modo `read` no `Repo.get` vs parâmetro `allow_clone` em cada comando. Contexto: o default do `Repo.get` hoje é clonar; inverter o default para leitura e deixar o clone explícito no chamador de execução é a fronteira que o incidente desenha. Recomendação (para discordar): `Repo.get(slug, write=False)` por padrão — quem precisa de clone pede.

## Out of scope

- Apagar o sandbox clonado na sessão (disco do dono).
- `--clone` flags novas em comandos de leitura.

## Handoff

- `git worktree add -b feat/{{no-clone.n}}-read-never-clones ../setwave-{{no-clone.n}} origin/main`
- `codegraph explore "clone_repo Repo.get cmd_epics cmd_status"` e ler o log da sessão de 2026-10-08 (o incidente)
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`
- `Closes {{no-clone}}`
