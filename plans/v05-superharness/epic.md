Parent: -.

# v0.5.0 — o superharness: os padrões provados, embedados

Quatro acervos de engenharia já provados em produção (setwave, demeter-workspace, kyte-ai-orquestration, openclaw-luana-workspace) viram um plugin só: setwave deixa de ser só o executor de ondas e passa a carregar, em camadas instaláveis, o refinamento que cria issues Ready, as guardas de ambiente que seguram agente desgarrado, o juiz independente do produtor e a robustez de loop que impede onda enforcada em silêncio durante a noite.

## A decisão de arquitetura (conselho de 3 assentos, 2026-10-08, unânime)

**Camadas separadas (B)**, cada uma instalável e testável sozinha, com a fronteira do Ada: o que é generativo entra no kernel só como **artefato tipado re-validado**. Taxonomia do Aristotle:

| Camada | Categoria | Onde mora |
| --- | --- | --- |
| robustez de loop | máquina de estados | `scripts/wave.py` (kernel) |
| definição/autoria | procedimento | `skills/define` |
| guards de ambiente | imposição | hooks do plugin, fail-open |
| julgamento (produtor/juiz) | política | skill + artefato tipado que o `merge` valida |
| perfis do repo host | preenchimento | [#15](https://github.com/CarlosDanielDev/setwave/issues/15) (épico v0.4) |

O que NÃO entra (o Feynman cobrou incidente concreto e não houve): routing de modelo por tier como produto — só os 2 checkpoints de escalação que previnem incidente nomeado (teste quebra 2×, pré-done). TTL lock fica fora: o sweep do kernel torna a cunha de trava obsoleta.

## Done when

- [ ] cada folha fechada por um PR mergeado, com a linha de release notes — owner: implementer
- [ ] `wave hooks install` num clone limpo → `wave doctor` reporta as guardas ativas, e cada guarda tem linha em `wave guarantees` com teste que a pinava (mutation-check) — owner: implementer
- [ ] `/setwave:define` produz um diretório de plan que passa no `wave lint` sem edição manual — owner: implementer
- [ ] `wave merge` recusa plano sem veredito de juiz válido para os SHAs atuais — owner: reviewer
- [ ] v0.5.0 taggeada da main com notas — owner: implementer

## ADR stub

Bifurcação: (A) monólito — tudo em `wave.py` + skills; (B) camadas do plugin; (C) setwave execution-only, padrões em skills externas. Contexto: kernel de 1881 linhas stdlib com gate próprio; guards só fazem efeito fora do processo do modelo (hooks); skills são material de prompt que muda sem release. Opções: A força todos a instalar tudo e mistura política de julgamento com código verificável; C desliga as guardas do loop que elas guardam — o produto É o poka-yoke. Recomendação (para discordar): **B**, com a fronteira do artefato tipado — veredito de juiz, ledger Done-when, corpo de issue Ready são artefatos que o kernel valida antes de qualquer transição.

## Out of scope

- Perfis do repo host (#15) — já é folha do épico v0.4; este épico não a duplica.
- Routing de modelo por tier como produto (swarm Haiku, advisor on-call permanente): sem incidente medido, não entra; a escalação pontual de checkpoint entra ([advisor](#{{advisor}})).
- TTL lock de claim: o sweep substitui o problema.
- Trackers que não sejam GitHub; auto-merge; force push. Como sempre.

## Execução

- Este épico se executa com o próprio plugin: `/setwave:wave {{epic}}` (ou `CarlosDanielDev/setwave#<N>`), depois de `wave plan plans/v05-superharness --milestone "v0.5.0"`.
- Gate do repo: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`.
- Ordem: [hooks](#{{hooks}}), [templates](#{{templates}}), [judge](#{{judge}}), [sweep](#{{sweep}}), [advisor](#{{advisor}}) independentes; [define](#{{define}}) depende de templates; [gates](#{{gates}}) fecha validando tudo.
