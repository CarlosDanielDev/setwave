Parent: #{{epic}}.
Depends on #{{templates}}.

# /setwave:define: a sessão de refinamento que só sai de si com issue Ready

A metade que falta do plugin: hoje o setwave **executa** épicos, mas quem escreve os corpos é uma sessão de auditoria sem procedimento — e um épico mal definido vira onda de agentes adivinhando. O kyte-ai-orquestration provou o procedimento (`~/.claude/skills/define/SKILL.md` adaptado de Matt Pocock): **grilling em rodadas**, cada pergunta numerada e com resposta recomendada; a **fronteira** = decisões cujos pré-requisitos já fecharam, recalculada a cada resposta; **fato de ambiente o sub-agente acha** (Explore read-only), nunca o usuário; sensores de escalada de incerteza; a sessão só termina quando um implementador começaria sem fazer pergunta nenhuma.

A mudança: `skills/define/SKILL.md` no plugin, destilada para o contexto setwave:

1. Carrega a issue (ou cria o rascunho local se não existe), o parent, os comentários; nunca re-pergunta o que o parent decidiu.
2. Rodadas de perguntas com recomendação (formato ❓/➡️ do original); fact-finding vai para sub-agente read-only sem travar a rodada.
3. Sensores: resultado não observável, 2+ "não sei", escopo crescendo em 2+ frentes → sinalizar promoção para épico com `wave plan` (não converter sozinho).
4. **Saída local primeiro**: escreve os corpos num diretório de plan (`index.tsv` + `<key>.md`, os mesmos do `wave plan`) e roda `wave lint` neles. **Nada é criado no GitHub sem OK explícito** — o `wave plan` é a única porta, e ele é o passo que o dono aprova.
5. Pendência que a sessão não fecha vira `## Pendências (bloqueiam o Ready)` no corpo, cada item com dono.

## Done when

- [ ] `skills/define/SKILL.md` com frontmatter de triggers ("define a #N", "refina", "deixa pronta") e a regra "não use para incerteza alta → sugira épico" — owner: implementer
- [ ] uma sessão real documentada no PR: 3+ rodadas, respostas recomendadas, fact-finding via sub-agente, e o diretório de plan resultante passando no `wave lint` — owner: reviewer
- [ ] sensores de escalada implementados como lista explícita no skill (as 5 condições do original, adaptadas) — owner: reviewer
- [ ] nenhum comando do skill escreve no GitHub sem passar por `wave plan` com OK — verificado e escrito como garantia — owner: reviewer
- [ ] README: seção "Planning an epic" aponta para `/setwave:define` como o procedimento de autoria — owner: implementer

## ADR stub

Bifurcação: skill escreve issues direto via `gh` vs **skill produz diretório de plan e o `wave plan` cria**. Contexto: o `wave plan` já recusa rodar duas vezes, já liga sub-issues, já wiring `blocked_by`, e é o passo que o dono vê (`--dry-run`); skill escrevendo via `gh` sozinha duplicaria tudo isso e criar issues é ação externa. Recomendação (para discordar): plan-dir como única saída — o grilling produz o mesmo artefato que a auditoria manual produz hoje, com procedimento no lugar de talento.

## Out of scope

- Boards, squads, glossário e method-map do kyte (específicos do kyte-brain); fica o esqueleto portável: rodadas, fronteira, sensores, Ready.
- `/upstream` (incerteza alta, apostas) — o sensor sugere épico; a sessão de aposta é outro skill, depois.
- Design alternativo visual (o `design-alternatives` do kyte depende do Folha DS).

## Handoff

- `git worktree add -b feat/{{define.n}}-define-session ../setwave-{{define.n}} origin/main`
- `codegraph explore "cmd_plan cmd_lint fill_refs"` e ler `scripts/wave.py:1501-1800` + `templates/issue/` (os tipos que o skill preenche)
- Referência viva: `~/.claude-glm/skills/issue-handoff/SKILL.md` (as regras de qualidade de prompt que o skill herda: fatos coletados, não chutados)
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`
- Inegociáveis: sem atribuição de IA; sem git destrutivo; nenhuma escrita no GitHub sem OK do dono.
- `Closes #{{define}}`
