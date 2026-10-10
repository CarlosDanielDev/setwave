Parent: #{{epic}}.

# Checkpoints de escalação: o momento em que a onda chama reforço

Padrão do acervo de advisor (nota do dono, "Opus reviews, Sonnet builds, Haiku swarms"): o modelo forte não trabalha — fica **on-call** em checkpoints nomeados. Dois checkpoints previnem falha nomeada no loop atual e por isso entram; o resto (routing por tier, swarm permanente, advisor on-call total) fica fora até incidente medido — o conselho cobrou incidente concreto e não houve:

1. **Teste/erro quebra 2×** (`skills/wave/SKILL.md:27` já manda retomar o agente morto — mas nada manda mudar de estratégia): na segunda falha do MESMO teste ou erro de compilação, o orquestrador **respawna o agente com contexto cheio** (o diff do primeiro attempt, o erro repetido, a instrução de atacar a causa-raiz, não o sintoma) em vez de retomar o mesmo contexto enforcado.
2. **Pré-done**: antes do relatório final, o agente re-audita o próprio diff contra o `Done when` da issue (seção nova no `templates/agent.md` §6) — é o "antes de chamar de pronto: o diff introduziu regressão escondida?" do padrão advisor, na versão mais barata: o próprio agente, não um segundo modelo.

A âncora no kernel: o stamp do `dispatch` (`scripts/wave.py:795`) já registra o nascimento da worktree em `.setwave.json`, e o `cmd_prompt` (`scripts/wave.py:851`) é o ponto onde a seção nova chega a todo agente — mudança de política de prompt com ponto de fixação único.

## Done when

- [ ] `templates/agent.md` ganha a seção de auditoria pré-done (diff vs `Done when`, premissas corrigidas, o que NÃO mexeu) — owner: implementer
- [ ] `skills/wave/SKILL.md` passo 4 manda: mesma falha 2× → respawn com contexto de falha, nunca o terceiro retry idêntico — owner: implementer
- [ ] o contador de retomadas do [sweep](#{{sweep}}) distingue "retomada" de "respawn com nova estratégia" (campo no `.setwave.json`), para o `wave stats` medir os dois — owner: reviewer
- [ ] `wave prompt <issue>` inclui a seção nova (teste do `cmd_prompt`) — owner: implementer
- [ ] `wave stats` mostra respawns vs retomadas das ondas seguintes (a medição que decide se a tabela de routing por tier um dia entra) — owner: reviewer

## ADR stub

Bifurcação: respawn com **contexto de falha anexado** vs advisor separado consultado antes do respawn. Contexto: o advisor permanente custa tokens em toda onda e nenhum incidente o justifica ainda; o respawn com contexto é uma mudança de política de prompt, custo zero quando não há falha. Recomendação (para discordar): respawn primeiro, advisor modelado depois — se `wave stats` mostrar respawns que não convergem, aí existe o incidente que justifica o advisor de verdade.

## Out of scope

- Routing por tier (Haiku swarm, Sonnet lead, Opus advisor) como configuração do plugin — sem incidente medido.
- Advisor consultado antes de `order --plan` (o humano já é o advisor desse passo: ele dá o OK).
- Troca de modelo via API — o modelo é escolhido pelo host (parâmetro do `Agent`), o plugin só manda o prompt certo.

## Handoff

- `git worktree add -b feat/{{advisor.n}}-escalation ../setwave-{{advisor.n}} origin/main`
- `codegraph explore "cmd_prompt cmd_agents cmd_stats"` e ler `templates/agent.md:68-71` (§6 relatório) + `skills/wave/SKILL.md:27`
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`
- Inegociáveis: sem atribuição de IA; sem git destrutivo; mudança de prompt não muda recusa nenhuma do `verify`/`merge`.
- `Closes #{{advisor}}`
