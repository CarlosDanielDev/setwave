# ADR 004 — the skill-lint runs in CI, refusing a broken frontmatter at the push

2026-10-08 · issue #41

The failure this guard closes is silent by nature: a skill whose frontmatter is broken does not error anywhere — it simply stops being offered to the tooling, and nothing else in the loop notices a skill going missing. The provenance is kyte-ai-orquestration, which already runs this exact guard (`scripts/validate_skills.py` with `.github/workflows/validate-skills.yml`); the check is ported to what this plugin's skills need, and the fail-loud place is CI — the one surface every change to `skills/` crosses before it can reach a session.

Chosen: CI (both matrix OSes) runs `python3 scripts/validate_skills.py` beside the tests and the guarantees, and the guard refuses three ways, each named in the refusal: a `name` that is not kebab-case or does not match its directory, a `description` under 40 characters (a skill that cannot be summarized cannot be chosen from a list), and a dead `templates/...` link (the skill's own prose promising a file the plugin does not carry). The tests pin the linter itself (`tests/test_skill_lint.py`), so removing a check fails the suite before CI ever has to.
