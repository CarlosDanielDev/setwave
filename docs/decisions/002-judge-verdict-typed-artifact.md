# ADR 002 — the judge's verdict is a typed artifact the kernel re-validates, keyed to the head sha

2026-10-08 · issue #37

The failure this guard closes: the implementing agent graded its own diff. `verify` checks conformity — attribution, protected paths, CI, the ledger — but "the diff does what the issue asks" was answered by the same mind that wrote the diff, and a PR merged on that answer carried no trace of who said it was good. The provenance is two sibling collections that already live behind this separation: kyte-ai-orquestration's `.claude/agents/critical-reviewer.md` (a read-only agent — "find faults, don't validate" — that receives the diff as a file, never the repository) and demeter-workspace's kira-gates skill ("judge PRs, never implement", its verdict keyed to the SHA).

Chosen: the judgement enters the kernel only as `judge-<PR>.json` — `verdict` (pass/fail/concerns), `head_sha`, `findings[]` — schema-checked and sha-checked at merge time. The judge agent is read-only (Read/Grep/Glob, `templates/judge.md`), so the producer and the judge cannot be the same session. Keying to `head_sha` is the half that makes the artifact honest: any push invalidates the verdict (`JUDGE-STALE`), so the judgement on file is always of the exact diff being merged, never of an earlier shape of it. A prose recommendation was the rejected fork: the orchestrator can ignore prose without leaving a rastro; it cannot merge past a missing artifact.

`--no-judge` for trivial docs-only PRs is deferred until one actually needs it — `--force` with a reason on the PR is the explicit bypass today, and it is the owner's, not the orchestrator's.
