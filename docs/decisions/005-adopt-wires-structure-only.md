# ADR 005 — adopt wires structure only and comments what is missing

2026-10-09 · issue #11

Adoption could rewrite the adopted bodies into the plugin's contract; the fork is between wiring the structure and editing someone else's words. Chosen: `wave adopt` wires only sub-issues and `blocked_by`, reports what it could not move or read, and asks for a missing `## Done when` in a comment — the body is never edited. The structure is the plugin's business and the words are the author's; `dispatch` already derives the branch and the CodeGraph query from the title when the body carries none, so an adopted issue runs without a handoff block.
