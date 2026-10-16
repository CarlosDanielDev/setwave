Parent: {{epic}}.

# Um classificador de estado de worktree: doctor e cleanup deixam de discordar

Achado da revisão de arquitetura de 2026-10-16 (candidato #1, decidido pelo conselho com 3 membros e conferido no código): "em que estado está este worktree?" é respondido em quatro lugares com quatro conjuntos de predicados, e dois deles já divergem num caso real. `cmd_doctor` (`scripts/wave.py:2905`) classifica "mergeado" por `git branch -r --merged`, que não enxerga squash-merge nem branch remoto deletado — o mesmo predicado que o docstring de `Repo.prs_for_head` (`scripts/wave.py:482`) chama de errado. `cmd_cleanup` (`scripts/wave.py:2422`) decide pelo PR e remove corretamente. Para um worktree squash-merged, o doctor não o põe em "stale": `pr_for_issue` (`scripts/wave.py:948`) só olha PRs **abertos**, então o worktree cai em `working`, `quiet` ou `likely dead` conforme o tempo parado, enquanto o cleanup o remove.

A ordem de decisão que o código já pratica no cleanup (`scripts/wave.py:2415-2422`) é a que vale: **sujo, depois não-pushado, depois estado do PR, depois progresso**. Sujeira e commit não-pushado vencem o PR, porque "PR mergeado" nunca autoriza apagar trabalho que não está em lugar nenhum.

A mudança: um `worktree_state(repo, wt, branch, n)` que devolve um record de fatos (`dirty`, `unpushed`, `pr_state`, `progress`, `stamp`) mais um `state` derivado por lista de decisão ordenada — `dirty`, `unpushed`, `merged`, `closed`, `open`, `empty`, `unknown` (e `working | quiet | dead` quando o `agents` migra). A derivação é uma função pura sobre os fatos, testável sem git nem gh. O estado do PR vem de **uma** consulta `gh pr list --state all` cacheada no `Repo`; falha do gh produz `unknown`, nunca um palpite.

## Done when

- [ ] teste RED primeiro: num sandbox com um worktree squash-merged, `doctor` e `cleanup` concordam sobre ele (hoje divergem) — owner: implementer
- [ ] teste: worktree sujo cujo PR foi mergeado nunca é removido pelo cleanup e nunca é reportado como `merged` (sujo vence o PR) — owner: implementer
- [ ] teste: falha do gh produz `unknown`, o cleanup não remove nada em `unknown` e o doctor reporta `unknown` em vez de `likely dead` — owner: implementer
- [ ] `doctor`, `cleanup` e `agents` consomem `worktree_state`; os predicados inline que o substituem saem (o `--merged` do doctor inclusive) — owner: implementer
- [ ] `sweep_retries` e `candidates` ficam fora desta folha, com a razão no PR: sweep tem ADR-003, `candidates` classifica issues, não worktrees — owner: reviewer
- [ ] `wave guarantees` ganha a linha, pinned, com mutation-check documentado no PR — owner: reviewer

## ADR stub

Bifurcação: record só de fatos (cada consumidor deriva o veredito) vs fatos mais um `state` derivado único. Contexto: foi exatamente cada consumidor derivar o próprio veredito que produziu o drift de hoje; o Feynman do conselho mediu a objeção — o enum só se justifica onde 2+ consumidores precisam dos mesmos rótulos, e doctor, cleanup e agents precisam. Recomendação (para discordar): fatos mais `state`, construído em fatias e test-first: primeiro o que doctor e cleanup disputam (`dirty`, `unpushed`, `merged`, `closed`, `open`, `empty`, `unknown`), depois `working | quiet | dead` junto da migração do `agents`.

## Out of scope

- `sweep_retries` (ADR-003 o mantém comando próprio; consome o classificador numa folha futura) e `candidates` (máquina de estados de issues).
- Dividir `wave.py` em módulos: a função mora ao lado de `liveness`; a decisão de divisão é a folha `split-adr`.
- Mudar o que o cleanup remove ou mantém: o comportamento de hoje é o contrato, só a fonte do estado muda.

## Handoff

- `git worktree add -b feat/{{worktree-state.n}}-worktree-state ../setwave-{{worktree-state.n}} origin/main`
- `codegraph explore "cmd_doctor cmd_cleanup liveness prs_for_head issue_worktrees"` e ler `scripts/wave.py:2405-2445` (a ordem de decisão que vale) e `scripts/wave.py:2900-2930` (o doctor que diverge)
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`
- Inegociáveis: sem atribuição de IA; sem git destrutivo; o cleanup nunca remove em `unknown`, em `dirty` nem em `unpushed`.
- `Closes {{worktree-state}}`
