# Seat progress in your statusline

The setwave segment renders the wave's seats every turn, in any client, with zero
interaction: `wave agents --line` prints one line —

    setwave: 2 working · 1 stalled · 1 done(#99) · 0 dead

— no color, no line break, under 120 characters, exit 0 always. It reads only local
state (git, the dispatch stamps, the process probe, a local PR cache): **it never
calls `gh`**, so a render is fast and free. Outside a git repo, or in a repo with no
wave state, it prints an **empty line** and exits 0 — it degrades in silence and
never breaks the statusline it is chained into.

## Arm it (one paste, your statusline stays yours)

The segment **chains after your existing statusline — it never replaces it**. Edit
the `statusLine` key of your `~/.claude/settings.json` (create the key if you have
none) and set `command` to:

- **You already have a statusline** — wrap it, keep it first, append the segment:

```json
{
  "statusLine": {
    "type": "command",
    "command": "sh -c 'in=$(cat); out=$(printf \"%s\" \"$in\" | <YOUR_CURRENT_COMMAND>); [ -n \"$out\" ] && printf \"%s\\n\" \"$out\"; python3 <PATH-TO-SETWAVE>/scripts/wave.py agents --line'"
  }
}
```

  Replace `<YOUR_CURRENT_COMMAND>` with your current `statusLine` command and
  `<PATH-TO-SETWAVE>` with the plugin checkout (e.g. `~/projects/setwave`).

- **No statusline yet** — just the segment:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 <PATH-TO-SETWAVE>/scripts/wave.py agents --line"
  }
}
```

`wave init --yes` and `wave hooks install` print this offer when they run; `wave
dispatch` prints the segment command next to the stall watchdog. One consent is all
it takes: the paste above is the whole installation.

## Remove it

Restore your previous statusLine: drop the `statusLine` key, or unwrap the command
back to `<YOUR_CURRENT_COMMAND>` alone (delete the `sh -c '…'` wrapper and keep what
was there before). Nothing else was written — the segment adds no files, no hooks
and no settings beyond that one key.

## Honest limits

- The `done(#N)` count is as fresh as the last full `wave` command: every one
  refreshes a local PR cache the line reads (gh is never called at render time).
  An armed `wave agents --watch` keeps it current on every poll.
- The host's own native per-agent rows render only for **named** spawns — the
  orchestrator names every agent `wave-<N>` (owner screenshots, 2026-11-03) — but
  that rendering is the host UI's call, not the plugin's: best effort, never a
  guarantee. The statusline segment and the chat seat table are the guaranteed
  layers.
