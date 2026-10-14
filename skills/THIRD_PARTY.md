# Skill provenance

The plugin ships these skills so a bare host gets the full working stack (`setwave:<name>`). Each is vendored verbatim from its source; behavior the prompt needs is also embedded in `templates/agent.md` §0, so a host that disables them loses nothing but comfort.

| Skill | Origin | License |
| --- | --- | --- |
| caveman | Carlos Daniel (own work; the mode this repo's author runs) | MIT (with this repo) |
| ponytail | vendored from the author's `~/.claude/skills`, MIT header in file | MIT |
| graphify | Carlos Daniel (own work; pairs with `codegraph`) | MIT (with this repo) |
| architecture-designer | vendored; frontmatter credits https://github.com/Jeffallan | MIT (per file header) |
| team-agent-orchestration | vendored from the ECC collection (`metadata.origin: ECC`) | credited to its authors; removal on request |
| poka-yoke | vendored from the author's `~/.claude/skills` | MIT (with this repo) |

Not vendored, on purpose: `superpowers:test-driven-development` (obra/superpowers, its own license). Its behavior ships embedded: the test-first rule in `templates/agent.md` §0.

Take-down: if any skill here is yours and you do not want it shipped, open an issue and it leaves the next release.
