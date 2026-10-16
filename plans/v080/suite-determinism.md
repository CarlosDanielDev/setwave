Parent: {{epic}}.

# Suíte determinística: nem make real, nem relógio de parede

Dois testes quebram por ambiente, não por código — e cada vermelho inexplicado corrói a confiança na suíte inteira:

1. `test_profiles.FullRepo.test_sync_provider_runs_before_the_worktrees` — falhou em 2026-10-08 e 2026-10-10 no host do dono: o `make sync` do fixture cai no `make` real, que shells para o Xcode e morre no `MacOSX26.5.sdk` inexistente (`SDKROOT` do host). O executor do [#63](https://github.com/CarlosDanielDev/setwave/pull/63) precisou de `env -u SDKROOT` para ficar verde.
2. `test_liveness` — flake de minuto: um teste que compara minutos que viram durante o run (46 ≠ 45), visto em 2026-10-09.

A mudança: o fixture do sync usa um **stub `make` no PATH do sandbox** (o padrão fake-gh que o repo já tem) que registra a chamada e sai 0 — o teste prova que o provider roda, não que o make do host funciona; e o relógio do `test_liveness` vira injetável (o stamp aceita `now` passado, o teste trava o tempo perto da virada de minuto e afirma determinístico).

## Evidência

O teste que quebra por ambiente está em `tests/test_profiles.py:158` (`test_sync_provider_runs_before_the_worktrees`); o que depende do relógio de parede está em `tests/test_liveness.py:76` (`test_thresholds`), exercitando `scripts/wave.py:1363` (`liveness`).

## Done when

- [ ] `test_profiles` roda verde num host sem Xcode/`make` real (stub no PATH do sandbox, chamada registrada e afirmada) — owner: implementer
- [ ] `test_liveness` determinístico perto da virada de minuto (relógio injetado, sem `datetime.now` no caminho afirmado) — owner: implementer
- [ ] suíte roda 3× seguidas verde no host do dono SEM `env -u SDKROOT` — owner: implementer
- [ ] `wave guarantees` não muda (nada de comportamento novo) — owner: reviewer

## ADR stub

Bifurcação: stub de `make` vs pular o teste quando o host não tem toolchain. Contexto: pular = o teste para de provar justamente em hosts variados, o lugar onde ele mais importa; stub = prova o que é do plugin (que o provider chama sync) sem depender do que é do host (que o make funciona). Recomendação (para discordar): stub, sempre — igual ao fake gh.

## Out of scope

- `SDKROOT` do host do dono (fix de shell: `sudo xcode-select -s /Library/Developer/CommandLineTools`).
- Os demais usos de `datetime.now` em código de produção.

## Handoff

- `git worktree add -b feat/{{suite-determinism.n}}-suite-determinism ../setwave-{{suite-determinism.n}} origin/main`
- `codegraph explore "sync_provider test_sync_provider liveness"`; os corpos dos PRs [#63](https://github.com/CarlosDanielDev/setwave/pull/63) e [#66](https://github.com/CarlosDanielDev/setwave/pull/66) documentam as duas falhas
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees` (3× seguidas)
- `Closes {{suite-determinism}}`
