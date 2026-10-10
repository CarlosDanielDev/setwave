Parent: #{{epic}}.

# wave judge: o produtor nunca é o juiz do próprio PR

Hoje o `verify` (`scripts/wave.py:955`) checa conformidade (atribuição, caminhos protegidos, CI, ledger) mas quem diz "o diff faz o que a issue pede" é o próprio agente implementador — autoavaliação. Os dois acervos que provaram a separação: o `critical-reviewer.md` do kyte-ai-orquestration (`.claude/agents/`, read-only, "ache falhas, não valide", recebe o diff num arquivo, nunca o repo inteiro) e o kira-gates do demeter-workspace (`skills/software-development/kira-gates/SKILL.md`: "judge PRs, never implement", veredito **chaveado ao SHA** — qualquer push novo invalida).

A mudança: `wave judge <PR...>` vira etapa do loop entre `verify` verde e o OK do merge:

1. Escreve o diff com escopo (`git diff origin/<base>...head -- <arquivos tocados>`) num arquivo `.patch` no diretório de handoffs.
2. O orquestrador spawn **um agente read-only** (prompt de `templates/judge.md`: procurar falha, veredito enumerado) com o patch e o corpo da issue.
3. O veredito é **artefato tipado**: `../<repo>-handoffs/judge-<PR>.json` com `verdict` (`pass|fail|concerns`), `head_sha`, `findings[]`. O `wave.py` valida o schema e o SHA.
4. `wave order --plan` inclui no plano o veredito de cada PR; `wave merge` **recusa** plano com PR sem veredito `pass` cujo `head_sha` bata com o head atual — push novo invalida, refaz o judge. A fronteira do conselho: o julgamento é generativo, mas entra no kernel só como artefato que o kernel re-valida.

## Done when

- [ ] `wave judge <PR>` gera `.patch` com escopo e `judge-<PR>.json` validado contra schema (SHA, enum de veredito, findings com `file:line`) — owner: implementer
- [ ] `wave merge --plan` recusa: veredito ausente, `fail`, ou `head_sha` ≠ head atual (`JUDGE-MISSING`/`JUDGE-FAIL`/`JUDGE-STALE`) — testes com fake gh — owner: implementer
- [ ] `templates/judge.md` (agente read-only: Read/Grep/Glob, "ache falhas", sem editar nada) e a etapa no `skills/wave/SKILL.md` entre verify e o OK — owner: implementer
- [ ] `wave order --plan` grava os vereditos no arquivo do plano — owner: reviewer
- [ ] `wave guarantees` documenta as 3 recusas novas, todas testadas — owner: reviewer

## ADR stub

Bifurcação: veredito como **artefato validado pelo kernel** vs mera recomendação em prosa no OK. Contexto: poka-yoke do repo prefere recusa a aviso (`README.md` "Mistake-proofing"); veredito em prosa o próprio orquestrador pode ignorar sem deixar rastro. Recomendação (para discordar): artefato tipado obrigatório, com `--no-judge` explícito para PRs triviais (docs-only), e o uso ficando visível no `status --post`.

## Out of scope

- Segundo revisor por stack (security-QA por linguagem do kyte) — só quando o judge único deixar passar falha de stack num incidente real.
- Judge automático de PRs fora de épico.
- Revisão semântica além do diff da issue (o `order --run-gate` já cobre conflito semântico entre PRs).

## Handoff

- `git worktree add -b feat/{{judge.n}}-judge ../setwave-{{judge.n}} origin/main`
- `codegraph explore "cmd_verify cmd_order cmd_merge apply_ledger ledger_check"` e ler `scripts/wave.py:955-1330`
- Referências vivas: `~/kyte/ai/kyte-ai-orquestration/.claude/agents/critical-reviewer.md`; demeter `skills/software-development/kira-gates/SKILL.md`
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`
- Inegociáveis: sem atribuição de IA; sem git destrutivo; o agente juiz é read-only — o prompt precisa negar edição.
- `Closes #{{judge}}`
