Parent: #59.

# adopt recusa recriar épico de título que já existe

O ADR do [#53](https://github.com/CarlosDanielDev/setwave/pull/53) deixou o gap documentado: `wave adopt` com `--milestone`/`--label` cria o épico pelo título sem conferir se um épico **fechado** já o carrega — rodar adopt duas vezes sobre o mesmo milestone histórico cria um épico duplicado (aberto) ao lado do original fechado, e a árvore do plugin passa a ver duas raízes para o mesmo trabalho. O `cmd_plan` já recusa título duplicado (`plan` confere `issue list --state all`); o `adopt` é a porta que ficou aberta.

A mudança: antes de criar o épico, `cmd_adopt` (`scripts/wave.py:3277` na origem; carimbo) procura o título entre TODAS as issues do repo (abertas e fechadas, `--state all`); achou → recusa com o ref — `ADOPT-TITLE-EXISTS: "v0.4.0 — ..." is CarlosDanielDev/setwave#1 (closed) — pass --epic 1 to adopt into it explicitly` —, e `--epic N` explícito continua funcionando para anexar folhas a um épico existente fechado ou aberto. Teste com fake gh cobre: título de épico fechado → recusa com ref; `--epic N` → anexa.

## Done when

- [ ] `adopt` que criaria épico com título já existente (aberto ou fechado) recusa com `ADOPT-TITLE-EXISTS` e o ref — owner: implementer
- [ ] `--epic N` explícito anexa folhas ao épico existente sem recusar por título — owner: implementer
- [ ] testes com fake gh: os dois caminhos + o caminho livre (título novo → cria) — owner: implementer
- [ ] mutation-check documentado no PR — owner: implementer
- [ ] `wave guarantees` ganha a linha, pinned — owner: reviewer

## ADR stub

Bifurcação: recusar vs ressuscitar (reabrir o épico fechado automaticamente). Contexto: reabrir sozinho muda estado de issue fechada — exatamente a classe de ação que o plugin nunca toma sozinho (o sweep reverte mentiras, nunca fecha; o adopt liga estrutura, nunca reabre). Recomendação (para discordar): recusa com o ref e o `--epic N` como porta explícita — a decisão de reabrir é do dono, um comando dele de distância.

## Out of scope

- Renomear/deduplicar épicos existentes.
- `wave plan` (já recusa título duplicado; nada a mudar).

## Handoff

- `git worktree add -b feat/62-adopt-title-exists ../setwave-62 origin/main`
- `codegraph explore "cmd_adopt textual_deps pick_by_selector"` e ler o ADR `docs/decisions/005-adopt-wires-structure-only.md` (o gap declarado)
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`
- Inegociáveis: sem atribuição de IA; sem git destrutivo; o adopt nunca reabre issue fechada sozinho.
- `Closes #62`
