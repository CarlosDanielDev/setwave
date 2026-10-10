Parent: #{{epic}}.

# Guards de ambiente como hooks fail-open do plugin

Hoje as regras inegociáveis dos agentes vivem só em prosa de prompt (`templates/agent.md:11-16`, `skills/wave/SKILL.md:57`): um agente desgarrado que ignore o prompt não encontra nenhuma barreira de ambiente. Nos acervos que já provaram isso, a barreira é um **hook**: o `block-secret-read.sh` do demeter-workspace (`.claude/settings.json` + `scripts/block-secret-read.sh`) nega no PreToolUse qualquer comando que leia segredo, e o `agent-dispatch-contract.py` do kyte-ai-orquestration (`.claude/hooks/`) nega despacho de Agent cujo prompt não traz as chaves do contrato. A lição dos dois: **fail-open** — guarda quebrada sai `exit 0`, nunca enforca o loop; e filtra-se em código, não no campo `if` do hook.

A mudança: o plugin passa a **carregar hooks próprios** (Python stdlib, protocolo de stdin JSON do Claude Code) e um `wave hooks install|status` que os registra por projeto, idempotente, com backup e `--dry-run`. Hoje a contraparte prompt-only é o grep de atribuição do `verify` (`scripts/wave.py:955`) e as linhas de GUARANTEES (`scripts/wave.py:1702`) — é lá que as guardas ganham linhas testadas. Quatro guardas, cada uma prevenindo uma falha nomeada:

1. `attribution-guard` — commit/PR body com `Co-Authored-By: Claude` ou "Generated with Claude Code" é negado (hoje: só `verify` vê, depois que já foi).
2. `forbidden-git-guard` — `gc --prune`, `reflog expire`, `stash`, `reset --hard`, `clean -f`, `push --force*`, `branch -D`, `rm -rf` no Bash é negado com a alternativa segura na mensagem.
3. `secret-read-guard` — denylist de leitura de segredo (`~/.config/setwave/repos.json` vale: tem tokens de conta; `cat` em `.env`, `credentials`, `auth.json`), nomeando `ls -la`/`stat`/`grep -c` como via segura.
4. `dispatch-contract-guard` — prompt de `Agent` sem as seções obrigatórias do `templates/agent.md` (onde, tarefa, gate, entrega, ledger) é negado antes da spawn.

## Done when

- [ ] `wave hooks install` grava os 4 hooks no settings do projeto com backup e é idempotente; `--dry-run` imprime o diff; `wave hooks status` (e uma linha no `wave doctor`) mostram ativo/ausente/quebrado — owner: implementer
- [ ] cada hook: teste no suite offline (stdin JSON → exit code) **e** mutation-check documentado no PR (guarda removida → teste falha → restaurada) — owner: implementer
- [ ] todos fail-open por construção: exceção no hook = exit 0, coberto por teste que injeta crash — owner: reviewer
- [ ] `wave guarantees` ganha 4 linhas, uma por guarda, todas com teste — owner: reviewer
- [ ] README descreve install/status e o que cada guarda nega — owner: implementer

## ADR stub

Bifurcação: hooks no **manifest do plugin** (sempre-on para quem instala) vs **`wave hooks install` por projeto** (opt-in). Contexto: plug-and-play é a promessa do épico, mas hooks sempre-on mudam a sessão de quem só queria o `/wave`. Recomendação (para discordar): manifest do plugin para 1–2 (attribution, forbidden-git — universais e baratas), `install` por projeto para 3–4 (secret-read e dispatch-contract dependem do projeto). Registrar em `docs/decisions/`.

## Out of scope

- Hooks que chamam LLM ou rede: guarda de ambiente é determinística ou não existe.
- Negar `AskUserQuestion`, WebSearch ou qualquer tool não-Bash.
- Suporte a hooks de plataformas que não o Claude Code.

## Handoff

- `git worktree add -b feat/{{hooks.n}}-guard-hooks ../setwave-{{hooks.n}} origin/main`
- `codegraph explore "cmd_doctor GUARANTEES main"` e ler `templates/agent.md:11-16` (as regras que viram ambiente)
- Referências vivas: `~/kyte/ai/demeter-workspace/scripts/block-secret-read.sh`, `~/kyte/ai/kyte-ai-orquestration/.claude/hooks/agent-dispatch-contract.py`
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`
- Inegociáveis: sem atribuição de IA; sem git destrutivo; evidência antes de afirmação; fail-open testado.
- `Closes #{{hooks}}`
