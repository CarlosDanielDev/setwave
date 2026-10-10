Parent: #{{epic}}.

# Templates tipados de issue + lint por tipo

O `templates/issue-contract.md` é um contrato único para qualquer trabalho; um bug não precisa de ADR com a mesma profundidade de uma story, e um bug **precisa** de reprodução que hoje ninguém cobra. O kyte-ai-orquestration resolve isso com um template por tipo de trabalho (`.claude/issue-templates/{story,chore,bug}.md`) e a regra "ler o arquivo do template antes de escrever o corpo, não parafrasear de memória". O `wave lint` (`scripts/wave.py:1501`) já sabe cobrar `file:line`, `Done when`, `ADR stub`, `Out of scope`, `Handoff` — falta saber **o tipo**.

A mudança: `templates/issue/{story,chore,bug,feature}.md` derivados do contrato atual, cada um com as seções obrigatórias do tipo; `wave lint` descobre o tipo pelo label (bug → template bug) e cobra o extra: **bug** = seção `## Reprodução` com passos e saída; **story** = cada item do `Done when` observável e com dono; **chore** = justificativa de por que não é story; **feature** = `## Fatia` nomeando as stories que a decompõem. O `lint` imprime qual template está usando e o que falta contra ele — a mesma honestidade de premissas do `wave why`.

## Done when

- [ ] 4 templates em `templates/issue/`, o contrato único vira índice que aponta para eles (nada de duas fontes de verdade) — owner: implementer
- [ ] `wave lint` resolve o tipo por label e cobra as seções específicas; sem label de tipo, cobra o contrato comum e avisa — testes cobrem os 4 caminhos — owner: implementer
- [ ] o lint imprime o template usado e cada item faltante com o nome da seção — owner: reviewer
- [ ] os corpos de `tests/e2e-plan/` continuam passando no lint (ou são migrados) — owner: implementer
- [ ] README "What an issue needs" aponta para os templates por tipo — owner: reviewer

## ADR stub

Bifurcação: tipos por **label** vs por campo no corpo (`Type: bug`). Contexto: label é o que o `wave plan` já cria via `index.tsv` (coluna labels) e o que humanos já filtram; campo no corpo duplica estado. Recomendação (para discordar): label como fonte única, com o lint tratando "sem tipo" como aviso, não erro — issue híbrida existe.

## Out of scope

- Board, coluna ou milestone — o épico continua sendo a unidade (#1).
- Templates de Aposta/upstream (o `/define` da camada de refinamento cobre o nível de incerteza alto depois).

## Handoff

- `git worktree add -b feat/{{templates.n}}-typed-issues ../setwave-{{templates.n}} origin/main`
- `codegraph explore "cmd_lint lint_issue"` e ler `scripts/wave.py:1501-1533` + `templates/issue-contract.md`
- Referência viva: `~/kyte/ai/kyte-ai-orquestration/.claude/issue-templates/`
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`
- Inegociáveis: sem atribuição de IA; sem git destrutivo; os 4 templates derivam do contrato atual — nenhuma seção obrigatória today desaparece.
- `Closes #{{templates}}`
