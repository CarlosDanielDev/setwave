# TAREFA — issue #{{N}}: {{TITLE}}

Issue: {{URL}}
Repo: `{{REPO}}`, base `{{BASE}}`, {{ACCOUNT}}.

## 0. Modo de trabalho — antes de ler código

Invoque via Skill tool, nesta ordem, e siga pelo resto da sessão: `caveman` (nível ultra), `ponytail`, `graphify`, `architecture-designer`, `team-agent-orchestration`, `superpowers:test-driven-development` (test-first: escreva o teste, veja-o falhar pelo motivo certo, só então implemente). Antes de abrir o PR, `poka-yoke` re-auditando o fluxo tocado. Prosa comprimida; código, comentários, commits e PR em inglês normal, bem escrito, no tom dos arquivos vizinhos.

Regras inegociáveis:
- NUNCA atribuição de IA em commit ou PR (nada de `Co-Authored-By: Claude`, nada de "Generated with Claude Code").
- NUNCA: `git gc --prune`, `git reflog expire`, `git stash`, `git reset --hard`, `git clean -f`, `git push --force*`, `git branch -D`, `rm -rf`.
- NÃO remova a worktree. NÃO faça merge, NÃO use `gh pr merge`. O dono revisa e merga.
- NÃO toque em `{{ROOT}}` (checkout principal) nem em outras worktrees irmãs. Outros {{PARALLEL}} agentes trabalham em paralelo: compilações lentas — não mate processos, não compartilhe diretório de build. Todo arquivo temporário seu leva sufixo `-{{N}}` (o scratchpad é compartilhado).
- NUNCA execute ação destrutiva contra dados reais (deleção, purge, migração em banco real); testes usam dobles e tempdir.
- Evidência antes de afirmação: nada de "verde" sem a saída colada, exit code por passo (nada de `| tail` escondendo o código). Mutation-check em toda guarda nova: remova a proteção, veja o teste falhar, restaure, cole as duas saídas.

## 1. Onde

Worktree JÁ CRIADA: `{{WORKTREE}}`, branch `{{BRANCH}}`, nascida de `origin/{{BASE}}` = `{{SHA}}` em {{DATE}} — **carimbo de hora, não fato**. Primeiro comando:

```bash
cd {{WORKTREE}} && git fetch origin && git status --porcelain && git rev-list --left-right --count origin/{{BASE}}...HEAD
```
Atrás > 0 → `git rebase origin/{{BASE}}` só com árvore limpa; conflito → pare e relate. CodeGraph: {{CODEGRAPH_NOTE}}. Comece por `codegraph explore "{{CODEGRAPH_QUERY}}"`; quando o grafo discordar do disco, o disco ganha.

## 2. A tarefa

```bash
gh issue view {{N}} -R {{REPO}} --json title,body -q .body
gh issue view {{N}} -R {{REPO}} --json comments -q '.comments[].body'
```
Leia a issue inteira **e os comentários**: PRs anteriores deixam ali o que mudou desde que ela foi escrita (símbolos renomeados, APIs novas). Leia também o parent dela, se houver. Números de linha na issue são carimbos do dia em que foi escrita: confirme cada um. "Done when" com dono por item — você é o implementer; onde diz reviewer, confira e declare no PR. Só o que segue em **Remaining** é trabalho seu. "ADR stub": decida, três frases no PR. "Out of scope": não reabra. "Handoff": a worktree já existe, ignore o `git worktree add`.

### Remaining

{{REMAINING}}

Se uma premissa da issue estiver errada (linha que andou, tipo que mudou, arquivo que não existe), corrija e diga no PR — é trabalho seu, não desvio. Se a issue estiver **inviável** como escrita, pare, comente na issue o porquê, e relate; não invente escopo.

## 3. O que não pode mudar

- Caminhos protegidos deste repo: {{PROTECTED}}. Nada ali muda; o revisor confere o diff.
- Diff mínimo: outros agentes editam arquivos vizinhos em paralelo. Toque só no que a issue pede; sem refatoração adjacente, sem dependência nova sem justificativa no PR.
- Ao terminar, se você mudou uma API/símbolo que outra issue **aberta** cita, comente nessa issue em uma linha ("#{{N}} renomeou X para Y").

## 4. Gate — na worktree, cole tudo no PR

```bash
cd {{WORKTREE}}
{{GATE}}
```

## 5. Entrega

`git add <só seus arquivos>`; commit em inglês, imperativo, no estilo do log (`git log --oneline -15`), com `Closes #{{N}}` no corpo; `git push -u origin {{BRANCH}}`; `gh pr create --base {{BASE}} --title "<uma frase em inglês dizendo o que muda>" --body-file <arquivo>`. Corpo: o que mudou e por quê; ADR em três frases; premissas erradas da issue; saída do gate; mutation-check antes/depois; e o **ledger** — uma seção com o heading exato `## Done when` (no início da linha, nenhuma variante) e a lista da issue copiada verbatim, todos os itens, na ordem, cada um marcado:

```markdown
## Done when

- [x] <item feito, texto da issue>
- [ ] <item não feito, texto da issue> — not done: <uma linha do porquê>
- [ ] ~~<item fora, texto da issue>~~ — dropped: <motivo>
```

`verify` recusa o PR sem ele (`LEDGER-MISSING`) ou com itens que não batem com a issue (`LEDGER-MISMATCH`); `merge` aplica o ledger na issue, e o próximo agente recebe só o que falta. Depois de abrir o PR: `python3 {{WAVE}} verify {{REPO}}#<PR>` — corrija o corpo (`gh pr edit <PR> --body-file <arquivo>`) até nenhum `LEDGER-*` sobrar. Sem atribuição de IA.

## 6. Antes de chamar de pronto — auditoria do próprio diff

Antes do relatório final, re-audite o seu próprio diff contra a issue; o gate só prova que os testes passam, não que o escopo está certo:

- **Diff vs `Done when`:** percorra cada item do `Done when` e aponte o trecho do diff que o cumpre (`git diff origin/{{BASE}}...HEAD`). Um item sem diff correspondente não está feito: implemente o que falta, ou declare `- [ ] <item> — not done: <porquê>` no ledger.
- **Premissas corrigidas:** liste no PR cada premissa da issue que estava errada (linha que andou, tipo que mudou, arquivo que não existe) e o que você fez em vez disso.
- **O que você NÃO mexeu:** nomeie os arquivos vizinhos que ficaram de fora, as refatorações tentadas e abortadas, e qualquer escopo além dos itens — o diff deve conter só o que os itens pedem.

## 7. Relatório final (sua última mensagem)

Número e URL do PR; arquivos mudados; resultado do gate (exit codes); ADR escolhido; premissas corrigidas; issues abertas que você avisou; o que decidiu não mexer e por quê. Bloqueado → diga exatamente onde, não invente que terminou.
