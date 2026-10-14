# Skill provenance

The plugin vendors three skills (`setwave:<name>`), each vendored with its upstream copyright notice beside it (`<name>/LICENSE-NOTICE`). The other skills the prompt names are **not** vendored: their behavior is embedded in `templates/agent.md` §0, so a host that lacks them loses comfort, not correctness.

| Skill | Upstream | License | Shipped as |
| --- | --- | --- | --- |
| caveman | [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) | MIT, © 2026 Julius Brussee | `setwave:caveman` (description inlined for the skill-linter; README not shipped) |
| ponytail | [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) | MIT (file header) | `setwave:ponytail` |
| architecture-designer | [Jeffallan](https://github.com/Jeffallan) | MIT (file header) | `setwave:architecture-designer` (cites references/ files not shipped) |

Not vendored, license or right not established:

- **graphify** — upstream [safishamsi](https://github.com/safishamsi) project; redistribution right not verified, so it does not ship. Its behavior needs `codegraph`, and the prompt already covers the absence (Grep/Read fallback).
- **team-agent-orchestration** — no license on file (all rights reserved by default). Its behavior (an owner on every acceptance criterion) is prompt rule five.
- **poka-yoke** — no author and no license on file (all rights reserved by default). Its behavior (the pre-PR mistake-proofing audit) is prompt rule six.
- **superpowers:test-driven-development** — obra/superpowers, its own license; test-first is prompt rule three.

Take-down: if any skill here is yours and you do not want it shipped, open an issue and it leaves the next release.
