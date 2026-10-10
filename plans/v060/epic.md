Parent: -.

# v0.6.0 — o release atrasado e as guardas que a sessão dele produziu

Catorze PRs mergearam desde a v0.5.0 (clone, epics, perfis, adopt, token, cleanup, prepare, wizard) e nenhum está taggeado — enquanto isso sete PRs bumparam os manifests serial com valores divergentes (0.5.1×2, 0.6.0×2, 0.6.1×3), o `order` marcou 4 pares de conflito e a normalização foi à mão. O épico faz três coisas: **solta o release** (v0.6.0, corrigindo os bumps fantasma), **fecha a brecha** com a guarda de versão ([#58](https://github.com/CarlosDanielDev/setwave/issues/58), já escrita com contract), e **duas endurecidas pequenas** que os passes de poka-yoke deixaram documentadas e ninguém fechou.

## Done when

- [ ] PR de Release mergeado, manifests em 0.6.0, **v0.6.0 taggeada da main com notas cobrindo #50–#57** — owner: implementer + dono
- [ ] [#58](https://github.com/CarlosDanielDev/setwave/issues/58) fechada por PR mergeado, com mutation-check — owner: implementer
- [ ] `prepare` recusa issue pai (parents nunca recebem handoff) — owner: implementer
- [ ] `adopt` recusa recriar épico de título igual a um que já existe, nomeando o ref — owner: implementer
- [ ] `wave guarantees` com as linhas novas, todas pinned — owner: reviewer

## ADR stub

Bifurcação: release como **v0.6.0** (downgrade dos manifests 0.6.1→0.6.0) vs v0.6.1 (seguir o número fantasma). Contexto: os 0.6.1 nunca foram taggeados — não existem para quem consome o plugin; a versão publicada mais recente é v0.5.0, e o próximo tag natural é 0.6.0 (o nome do milestone desta leva). Recomendação (para discordar): **v0.6.0**, manifests corrigidos no PR de Release — é a primeira execução real da convenção que a [#58](https://github.com/CarlosDanielDev/setwave/issues/58) transforma em guarda.

## Out of scope

- Routing de modelo por tier e TTL lock — continuam fora até incidente medido (`wave stats` guarda os respawns; nenhum padrão não-convergente até agora).
- Automação de release (bump/tag automáticos pós-merge) — o bump pertence ao PR de Release, a tag ao dono, como no [#49](https://github.com/CarlosDanielDev/setwave/pull/49).
- Qualquer feature nova — este épico é dívida e endurecimento, não escopo.

## Execução

- Executa com o próprio plugin: `/setwave:wave {{epic}}` depois de `wave plan plans/v060 --milestone "v0.6.0"`.
- [#58](https://github.com/CarlosDanielDev/setwave/issues/58) já existe com contract completo — o `wave plan` não a recria; o épico a adota como sub-issue pós-criação.
- Ordem: [release](#{{release}}) primeiro (menos janela para bumps fora de guarda), depois [guard](#58 via blocked_by manual), [prepare-parent](#{{prepare-parent}}) e [readopt](#{{readopt}}) em paralelo.
- Gate do repo: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`.

