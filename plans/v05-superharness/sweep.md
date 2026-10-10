Parent: #{{epic}}.

# wave sweep: o estado dito feito re-verificado no GitHub, com teto de retry e escalação

Duas falhas de silêncio que a onda ainda não detecta. (1) **Estado mentiroso**: um leaf marcado `done-unclosed` (todas as caixas do ledger tickadas) pode estar apoiado num PR que nunca mergeou, ou num issue fechado sem PR — o openclaw-luana-workspace já levou esse incidente (o `check-ready-issues.sh` de `skills/squad-rise-worker/scripts/` re-verifica "processado" contra PR real a cada ciclo e devolve à fila o falso positivo). (2) **Claim eterno**: `wave agents` (`scripts/wave.py:1545`) classifica `likely dead` mas ninguém teta o loop: um agente morto retomado N vezes sem progresso segura a onda para sempre.

A mudança: `wave sweep <epic>` (e `wave doctor` chamando-o) faz as duas varreduras deterministicamente — zero token, só gh+git:

1. **State sweep** — para cada leaf `done-unclosed` ou fechado: o PR que o fecha existe, está merged, o ledger foi aplicado? Descasado → reabre o estado (comenta na issue o porquê, cita o PR) e o `next` volta a vê-lo.
2. **Teto de retry** — contador de retomadas sem novo commit no `.setwave.json` da worktree (o stamp do `dispatch` já existe). Ao atingir o teto (default 3): para de retomar, comenta na epic e na issue com a evidência (último commit, minuto desde a última mudança), marca `agent-exhausted` no status. **Nunca fecha nem abandona sozinho** — escala para o dono.

## Done when

- [ ] `wave sweep <epic>` roda as duas varreduras, read-only por default, `--fix` aplica as correções de estado — owner: implementer
- [ ] falso `done-unclosed` (PR não merged, ledger não aplicado) é detectado, revertido com comentário e volta ao `next` — coberto por teste com fake gh — owner: implementer
- [ ] teto de retry: 3 retomadas sem commit novo → escalação comentada, sem novo dispatch automático — teste cobre — owner: implementer
- [ ] `wave doctor` reporta `sweep` na saída; `wave guarantees` ganha as linhas novas — owner: reviewer
- [ ] `wave status` mostra `agent-exhausted` como estado exclusivo e exaustivo da máquina — owner: reviewer

## ADR stub

Bifurcação: sweep dentro do `doctor` vs comando próprio. Contexto: `doctor` já é o preflight obrigatório do dispatch (`scripts/wave.py:1557`) e recusa em ✗; sweep é diagnóstico de estado, não de ambiente. Recomendação (para discordar): comando próprio, chamado pelo `doctor` como sub-checagem — mantém cada um testável sozinho (a lição da camada B do conselho) e evita um `doctor` que faz tudo. TTL lock de claim fica de fora: com o sweep re-verificando estado contra PR real, a trava vira redundância.

## Out of scope

- Agendar sweep em cron — o dono roda `wave next`/`doctor`; automação de agendamento é mundo do host (#15).
- Matar processo de agente — a classificação continua vinda de arquivos e git, nunca de processo.
- TTL lock de claim (deferido pelo conselho até haver loop concorrente real).

## Handoff

- `git worktree add -b feat/{{sweep.n}}-state-sweep ../setwave-{{sweep.n}} origin/main`
- `codegraph explore "cmd_agents cmd_doctor cmd_next cmd_status .setwave.json stamp"` e ler `scripts/wave.py:1545-1700`
- Referência viva: `~/kyte/ai/openclaw-luana-workspace/skills/squad-rise-worker/scripts/check-ready-issues.sh` (state sweep pós-incidente)
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`
- Inegociáveis: sem atribuição de IA; sem git destrutivo; sweep nunca deleta — reverte estado e comenta.
- `Closes #{{sweep}}`
