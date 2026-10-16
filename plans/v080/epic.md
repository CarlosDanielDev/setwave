Parent: -.

# v0.8.0 — a auditoria do harness: os gaps, fechados

Auditoria de 2026-10-16 sobre o plugin inteiro (probes do próprio `doctor`/`guarantees` + os skips com razão anotados nas iterações v0.7.x). Nove gaps, nenhum inventado: cada um foi observado em execução ou declarado como skip com motivo. O épico os fecha sem abrir nenhuma feature nova — routing por tier e TTL lock continuam trancados até incidente medido.

## Done when

- [ ] cada folha fechada por um PR mergeado, com release notes — owner: implementer
- [ ] `wave doctor` neste repo sem nenhum `!` de hooks (0/4 → 4/4 ativos no próprio setwave) — owner: implementer
- [ ] nenhum comando read-only do `wave` clona repositório — owner: implementer
- [ ] a suíte roda verde num host sem `make`/Xcode real e sem flake de minuto — owner: implementer
- [ ] `wave guarantees` cobre cada recusa nova, pinned — owner: reviewer

## ADR stub

Bifurcação: fechar os gaps como **épico único de hardening** vs espalhar em cycles separados. Contexto: nove gaps pequenos e independentes; um épico só dá release única, notas únicas e uma onda a mais de dogfooding. Recomendação (para discordar): épico único v0.8.0, ondas de 4, sem nenhuma feature — a variante "espalhar" só faria sentido se um gap crescesse, e nenhum tem esse risco.

## Out of scope

- Routing de modelo por tier e TTL lock — sem incidente medido; `wave stats` continua guardando os respawns.
- Split do `wave.py` em pacote — vira [decisão com ADR]({{split-adr}}), não execução: o arquivo único é decisão documentada e só sai dela com plano concreto.
- Trackers que não sejam GitHub; auto-merge; force push. Como sempre.

## Execução

- `/setwave:wave {{epic.n}}` depois de `wave plan plans/v080 --milestone "v0.8.0"`.
- Ordem: [no-clone]({{no-clone}}), [dogfood]({{dogfood}}), [suite-determinism]({{suite-determinism}}), [skills-substitution]({{skills-substitution}}), [worktree-state]({{worktree-state}}) na onda 1; [release-helper]({{release-helper}}), [hooks-drift]({{hooks-drift}}), [vendored-references]({{vendored-references}}), [showcase-polish]({{showcase-polish}}) na onda 2; [split-adr]({{split-adr}}) fecha decidindo.
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`.
