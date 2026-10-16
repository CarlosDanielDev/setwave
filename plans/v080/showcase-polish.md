Parent: {{epic}}.

# Showcase: o cartão social que não existe

A página do Pages ([carlosdanieldev.github.io/setwave](https://carlosdanieldev.github.io/setwave/)) está no ar, mas compartilhar ela num chat ou rede social rende o card cinza padrão: sem `og:image`, sem `canonical`, sem sitemap — o aijail.io que inspirou o redesign tem os três. E o README não linka a página em lugar nenhum.

A mudança: (1) `og:image`/`twitter:image` — um card 1200×630 estático (SVG→PNG gerado uma vez, mesma identidade: ondas crest no abyss, o prompt de uma linha); (2) `<link rel="canonical">` + `og:url`; (3) `sitemap.xml` + robots; (4) badge do site na fileira de badges do README; (5) os `<details>` da FAQ ganham `itemprop`/schema.org `FAQPage` para busca.

## Evidência

A página já tem `og:title` em `docs/index.html:10`; faltam `og:image`, `og:url`, `canonical` e o sitemap ao lado dela; o gate que segue obrigatório é o passo de `.github/workflows/ci.yml:22` (`validate_skills.py`).

## Done when

- [ ] `og:image` 1200×630 servido em `docs/` e referenciado com dimensões — owner: implementer
- [ ] canonical + og:url apontando para a URL do Pages — owner: implementer
- [ ] `docs/sitemap.xml` + `robots.txt` — owner: implementer
- [ ] README: badge "showcase" na fileira — owner: implementer
- [ ] FAQ com schema.org FAQPage válido — owner: implementer

## ADR stub

Bifurcação: card como PNG commitado vs gerado em build. Contexto: GitHub Pages não roda build; PNG commitado é o que os showcases de referência fazem. Recomendação (para discordar): PNG estático commitado, gerado do SVG da identidade — regenerar é manual e raro.

## Out of scope

- Analytics na página (privacidade primeiro; a telemetria honesta é o log do plugin).

## Handoff

- `git worktree add -b feat/{{showcase-polish.n}}-showcase-polish ../setwave-{{showcase-polish.n}} origin/main`
- Referência de identidade: o próprio `docs/index.html` (tokens abyss/crest/sand)
- Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees` (página não tem testes; o gate é o de sempre)
- `Closes {{showcase-polish}}`
