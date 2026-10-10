Parent: #{{epic}}.
Depends on #{{hooks}}, #{{templates}}, #{{define}}, #{{judge}}, #{{sweep}}, #{{advisor}}.

# As camadas, validadas: skill-lint no CI, GUARANTEES por guarda, ADR por guarda

Camada que não tem teste pinando a promessa é prosa. O repositório já vive dessa regra (`wave guarantees` lista cada promessa com o teste que a segura; a lição do README: "remove a guarda, veja o teste falhar, restaure, marque testada"). O kyte-ai-orquestration adicionou as duas peças que faltam aqui: **skill-lint no CI** (`scripts/validate_skills.py` + workflow — skill com frontmatter quebrado some silenciosamente da tooling) e **ADR numerado por guarda** (`docs/decisions/001`–`008`, cada um citando o incidente que justificou).

A mudança, fechando o épico:

1. **Skill-lint no CI**: valida o frontmatter de cada `skills/*/SKILL.md` do plugin (nome kebab = diretório, description ≥ 40 chars, sem link morto para template) — CI do repo roda no Ubuntu e macOS.
2. **GUARANTEES completas**: uma linha por recusa nova de [hooks](#{{hooks}}) (4 guardas fail-open), de [judge](#{{judge}}) (JUDGE-MISSING/FAIL/STALE) e de [sweep](#{{sweep}}) (falso done revertido, teto de retry) — cada uma com o teste que a pinava.
3. **`docs/decisions/`**: um ADR curto por guarda nova, citando o incidente ou acervo de origem (o `block-secret-read.sh` do demeter, o `agent-dispatch-contract.py` do kyte, o incidente #2999 do openclaw) — a justificativa vira história, não folclore.
4. **E2e**: o `scripts/e2e.py` passa a exercitar o judge (o fake agent recebe veredito `pass`) e o sweep (o fake agent deixa um falso done que o sweep reverte) — o loop inteiro com as camadas novas, no sandbox.

## Done when

- [ ] CI roda o skill-lint e falha com frontmatter quebrado (mutation-check: quebre um, veja vermelho, restaure) — owner: implementer
- [ ] `wave guarantees` cobre todas as recusas novas das camadas, todas com teste — owner: reviewer
- [ ] `docs/decisions/` com um ADR por guarda nova, cada um citando origem e incidente — owner: implementer
- [ ] `scripts/e2e.py` exercita judge e sweep no sandbox; cada passo com exit code esperado — owner: implementer
- [ ] release notes da v0.5.0 descrevem as 4 camadas sem prometer o que não existe (routing por tier e TTL lock ficam fora, ditos como fora) — owner: reviewer

## ADR stub

Bifurcação: ADRs em `docs/decisions/` vs seção no README. Contexto: README é o contrato com o usuário; decisões de guarda são contrato interno com incidentes e caminhos de outros acervos. Recomendação (para discordar): arquivos separados numerados, README linkando — o README atual já está no limite da densidade que um recém-chegado lê.

## Out of scope

- Cobertura de código por % — o gate continua sendo o suite inteiro + guarantees, como hoje.
- Publicar os ADRs fora do repo.

## Handoff

- `git worktree add -b feat/{{gates.n}}-layer-gates ../setwave-{{gates.n}} origin/main`
- `codegraph explore "cmd_guarantees GUARANTEES e2e"` e ler `scripts/wave.py:1702-1732` + `scripts/e2e.py` + `.github/workflows/ci.yml`
- Referências vivas: `~/kyte/ai/kyte-ai-orquestration/scripts/validate_skills.py` e `.github/workflows/validate-skills.yml`; `docs/decisions/001..008` do mesmo acervo
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`
- Inegociáveis: sem atribuição de IA; sem git destrutivo; mutation-check documentado no PR para cada guarda marcada "tested".
- `Closes #{{gates}}`
