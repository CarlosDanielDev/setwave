Parent: {{epic}}.
Depends on {{no-clone}}, {{dogfood}}, {{suite-determinism}}, {{skills-substitution}}, {{release-helper}}, {{hooks-drift}}, {{vendored-references}}, {{showcase-polish}}.

# O wave.py passa dos 3500: a decisão de dividir (ou de ficar)

`scripts/wave.py` nasceu com 450 linhas no dia 0, tinha 1881 na v0.5.0 e fechou v0.7.1 com **3546 linhas e 171 funções** — o único arquivo que toda a ferramenta executa, stdlib only, por decisão escrita no README ("Everything is Python because the orchestration loop must not depend on the user's shell" e "stdlib only, tests included: nothing to install"). Arquivo único tem propriedades reais: `python3 wave.py` roda de qualquer lugar, zero imports relativos, o judge e o linter leem um arquivo. Mas 3546 é o dobro do tamanho em que a decisão foi tomada, e a auditoria de /simplify já achou regras vivendo em duas superfícies.

Esta folha não divide nada. Ela **decide**, como ADR, com um dos dois planos concretos:

1. **Ficar único, com fronteira medida**: manter, mas com um teste de arquitetura que congele as fronteiras internas (seções do arquivo — plumbing, repo, prompts, hooks, cmd_*) e um limite acordado (ex.: nenhuma seção passa de 800 linhas sem ADR novo).
2. **Dividir em pacote stdlib**: `wave/` com `__main__.py` shim — `python3 scripts/wave.py` continua funcionando, os imports continuam stdlib, os testes continuam offline; o plano nomeia os módulos (repo, prompts, guards, commands/*) e a ordem de extração, começando pelo que os /simplify tocaram.

## Evidência

Dois exemplos de acoplamento a mapear no ADR: `scripts/wave.py:1231` (`candidates`, a máquina de estados de issues) e `scripts/wave.py:2826` (`cmd_doctor`, 145 linhas que chamam metade do arquivo).

## Done when

- [ ] `docs/decisions/00X-wave-module-threshold.md` com a decisão, os números (linhas por seção hoje) e o plano escolhido — owner: implementer
- [ ] se "ficar": o teste de arquitetura existe (conta linhas por seção, falha acima do limite acordado) — owner: implementer
- [ ] se "dividir": o PR da primeira extração (a mais trivial) mergeado com a suíte verde e o shim funcionando — owner: implementer
- [ ] README "Layout" reflete a decisão — owner: reviewer

## ADR stub

Esta folha É o ADR — o stub dela é o compromisso de medir antes de opinar: rodar contagem por seção, listar os cinco pontos de acoplamento (GUARANTEES, HELPERS de teste, o parser, os imports circulares potenciais), e só então escolher. Recomendação (para discordar): **ficar único com limite medido** até a primeira feature que não caiba — dividir por número redondo é estética, não engenharia.

## Out of scope

- Qualquer mudança de comportamento — a decisão não muda o que o plugin faz.
- Dividir os testes (a separação por concern já existe lá).

## Handoff

- `git worktree add -b feat/{{split-adr.n}}-module-threshold ../setwave-{{split-adr.n}} origin/main`
- `codegraph explore "GUARANTEES cmd_merge cmd_doctor"` + `grep -c 'def ' scripts/wave.py` + a contagem por seção
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`
- `Closes {{split-adr}}`
