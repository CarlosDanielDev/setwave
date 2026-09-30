The epic of one end-to-end run of setwave (`scripts/e2e.py`, run `{{run}}`), created by `wave plan` from `tests/e2e-plan/`.

Leaves: {{a}}, {{b}}, {{c}}. {{a}} and {{b}} both append to the end of `app.py`, so whichever lands second conflicts textually with the first and goes through `wave resolve`; {{c}} touches nothing the others touch.

## Done when

- [ ] every leaf closed by a merged PR, and this epic closed by `wave close-parents --include-epic` — owner: `scripts/e2e.py`
