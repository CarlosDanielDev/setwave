Parent: #{{epic}}.

# prepare recusa issue pai: parent nunca recebe handoff

O passe de poka-yoke do [#56](https://github.com/CarlosDanielDev/setwave/pull/56) deixou declarado: `wave prepare <issue>` não recusa **issue pai** — um parent com sub-issues recebe um `## Handoff` como qualquer folha, e quem apanha o erro é o `lint` um passo depois ("a parent with a Handoff block: parents are never dispatched", `scripts/wave.py` `cmd_lint`), ou pior: ninguém, se ninguém rodar o lint. A regra do contrato é antiga — parents nunca recebem PR nem handoff; fecham quando as filhas fecham (`wave close-parents`). A recusa pertence ao `prepare`, na porta.

A mudança: `prepare_issue` (origin/main, `cmd_prepare`) recusa com mensagem nomeada quando a issue tem sub-issues — `PREPARE-PARENT: owner/name#N is a parent (N sub-issues); parents are never dispatched` —, teste com fake gh cobre o caminho, e o `dispatch` herda de graça (chama `prepare` nos corpos sem handoff).

## Done when

- [ ] `prepare` em issue com sub-issues sai com `PREPARE-PARENT` e o número de filhas, sem tocar o corpo — owner: implementer
- [ ] teste no suite com fake gh (parent → recusa; folha → passa) — owner: implementer
- [ ] mutation-check documentado no PR (guarda removida → teste falha → restaurada) — owner: implementer
- [ ] `wave guarantees` ganha a linha, pinned — owner: reviewer

## ADR stub

Bifurcação: recusa no `prepare` vs confiar no `lint` posterior. Contexto: o `prepare` escreve no corpo — escrever num parent é o erro, e o lugar de impedir a escrita é a porta que escreve; `lint` é auditoria, não guarda. Recomendação (para discordar): recusa no `prepare`, o `lint` continua como segunda rede.

## Out of scope

- `prepare` em épicos sem sub-issues ainda (a issue pode ganhar filhas depois; a recusa é no momento da escrita, com as filhas que existem).
- Mudar o `close-parents` ou a regra de despacho.

## Handoff

- `git worktree add -b feat/{{prepare-parent.n}}-prepare-parent ../setwave-{{prepare-parent.n}} origin/main`
- `codegraph explore "prepare_issue cmd_prepare cmd_lint"` e ler o comentário do passe de poka-yoke no [#56](https://github.com/CarlosDanielDev/setwave/pull/56)
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`
- Inegociáveis: sem atribuição de IA; sem git destrutivo; a recusa nomeia o número de sub-issues.
- `Closes #{{prepare-parent}}`
