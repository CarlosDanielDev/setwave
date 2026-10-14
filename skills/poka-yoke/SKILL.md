---
name: poka-yoke
description: Poka-Yoke usability gate. Audits commands, interaction flows, and system workflows for missing error-prevention — no confirmation before destructive actions, weak input validation, silent failures, no way back or cancel — and emits a severity-ordered JSON audit report with concrete fixes. Use when the user runs /poka-yoke, asks to "mistake-proof", "poka yoke", "usability audit", "UX gap audit", "error-prevention review", "is this command safe to misuse", or asks to add confirmations/guardrails to a command or flow. Suggests only UX, validation, and error-handling changes — never business logic.
metadata:
  version: 1.0.0
  scope: global
---

# Poka-Yoke Usability Gate

Map interaction flows, find where a user can make a mistake the system should have prevented, and produce mistake-proofed modifications.

Poka-yoke = the error is impossible or caught before it costs anything. Not "warn the user harder" — remove the way to get it wrong.

## Invocation

`/poka-yoke [target=<command>] [strictness=1|2|3]`

- `target` — one command/flow to audit (e.g. `/poka-yoke target=/delete-user`). If omitted, audit the current session context: the user's **last 5 interactions** and the commands they touched.
- `strictness` — `1` Critical only, `2` Critical + Medium (**default**), `3` full OCD: Critical + Medium + Low + polish.

Everything after the command is captured as input. If a target is named but not found in the repo, say so and audit what you did find — never invent a flow.

## Step 1 — Load the command registry

Global scope means you must discover flows dynamically, not from memory.

```bash
# Discord.js / slash commands
grep -rl "SlashCommandBuilder\|new SlashCommandBuilder" --include=*.ts --include=*.js src/ 2>/dev/null
# Telegram
grep -rn "bot.command\|onText\|InlineKeyboard" --include=*.ts --include=*.js src/ 2>/dev/null
# HTTP routes
grep -rnE "@(Get|Post|Put|Patch|Delete)\(|(app|router)\.(get|post|put|patch|delete)\(" src/ 2>/dev/null
# CLI
grep -rn "argparse\|commander\|yargs\|clap" 2>/dev/null | head
# Claude Code skills/commands
ls ~/.claude/commands/ .claude/commands/ 2>/dev/null
```

Read the actual handler for each audited command. **No finding without a `file:line`.** A guess is not a finding.

Latency budget: < 1.5s of aggregation. Grep the registry, read only the handlers you audit. Do not read the whole repo.

## Step 2 — Map the interaction

For each audited command, write down:

1. **Entry points** — how the user starts it (slash command, button, deep link, cron, direct API call). Count every one; the guarded path is useless if an unguarded second entry exists.
2. **Input fields** — name, type, expected format, required vs optional, bounds.
3. **Transitional states** — loading, processing, success, error. Which are visible to the user?
4. **Exit points** — completion, cancel, timeout, force-quit / browser close / bot restart mid-flow.

If any of the four is empty for a flow that should have it, that's already a finding.

## Step 3 — Gap detection checklist

Run every check. A check that passes gets no output; a check that fails becomes a finding.

### Destructive_Actions
- Confirmation prompt before DELETE, UPDATE, KICK, BAN, PURGE, overwrite?
- Is the confirmation *proportional*? A one-click button for "delete 400 records" is not confirmation — require typed `CONFIRM` or the resource name.
- Timeout / expiration on critical operations (a confirm button live forever is a landmine)?
- Undo window, soft delete, or is the data gone?
- Can the confirming user be someone other than the initiator? (button click hijack)

### Input_Validation
- Data types strictly enforced at the boundary, not assumed?
- Boundary values (max length, min value, empty string, zero, negative, huge) explicitly checked — with a **user-friendly** message naming the field and the accepted range?
- Mandatory fields clearly marked as required at the UI layer, not only rejected server-side?
- Are invalid options *presented* at all? Best poka-yoke: a picker/choices list instead of free text.

### Feedback_Loops
- Clear success/failure messages — not `Error 500`, not silence, not a stack trace?
- Does the failure message say what to do next?
- Loading spinner / progress / deferred reply for anything > 2 seconds?
- Does the user learn *which* item was acted on? ("Deleted." vs "Deleted @XYZ.")

### Navigation_Recovery
- Can the user go back a step without losing entered data?
- Cancel / Abort available during long-running processes?
- What happens on force-quit mid-flow — orphaned state, half-written record, stuck lock?
- Is a retry safe (idempotent), or does double-submit double-charge?

At `strictness=1` report only Critical. At `2` Critical + Medium. At `3` everything including Low.

## Step 4 — Modification strategy

**Flow exists** → incremental fixes. Add guardrails around the existing path; keep the happy path's call signature intact.

**Flow does not exist** → generate the complete interaction from scratch with poka-yoke built in:
`Step 1: Select → Step 2: Preview (show exactly what will change) → Step 3: Confirm & Execute → Step 4: Receipt + Undo`

Output **specific code snippets** in the stack that's actually installed. Check `package.json` / imports first, then use its components:

- **discord.js** → `ActionRowBuilder`, `ButtonBuilder` (`ButtonStyle.Danger` for destructive), `ModalBuilder` + `TextInputBuilder` for typed confirmation, `StringSelectMenuBuilder` instead of free text, `interaction.deferReply({ ephemeral: true })` for > 2s, `collector` with an explicit `time:` and `i.user.id === interaction.user.id` filter.
- **Telegram** → `InlineKeyboardMarkup` with callback data, `answerCallbackQuery` for feedback, `editMessageText` for state transitions.
- **CLI** → `inquirer`/`prompts` confirm, `--yes` escape hatch for scripts, `--dry-run` printing the diff.
- **Web** → native `<input type=...>` + `required` + `min/max` before any JS validator; disabled submit while in-flight.

Never invent a library that isn't in the project.

## Step 5 — Emit the report

Output a single fenced `json` block, type `Poka_Yoke_Audit_Report_JSON`. **Critical first, always** — ordering is part of the contract.

```json
{
  "audit_timestamp": "ISO 8601",
  "audited_commands": ["/delete-user"],
  "critical_risks": [
    { "issue": "Description", "location": "file:line", "suggested_fix": "Actionable step-by-step fix" }
  ],
  "medium_risks": [
    { "issue": "Description", "location": "file:line", "suggested_fix": "Actionable step-by-step fix" }
  ],
  "low_risks": [
    { "issue": "Description", "suggested_fix": "Actionable step-by-step fix" }
  ],
  "new_flow_design": {
    "if_applicable": "Mermaid.js flowchart or JSON step spec of the corrected interaction"
  }
}
```

Get the timestamp from `date -u +%Y-%m-%dT%H:%M:%SZ` — do not fabricate it.

Empty severity arrays stay as `[]`. Omit `new_flow_design` only when no flow was redesigned.

After the JSON, add the code snippets for the fixes. The JSON is the report; the snippets are the patch.

## Hard rules

1. **Never alter core business logic.** Only UX, validation, and error-handling layers. If the fix requires changing what the command *does*, that's not a poka-yoke finding — state it as an out-of-scope note.
2. **No breaking changes.** New params are optional with safe defaults. Existing callers keep working. A confirmation step must have a documented bypass for scripted/admin use (`--yes`, `force: true`) or it breaks automation.
3. **Reference installed libraries only.** Read `package.json`/imports before recommending components.
4. **Critical outranks everything** in output order, regardless of count.
5. **Evidence or nothing.** Every finding carries `file:line`. No `file:line` → do not report it.
6. Admin-gating: if the audited surface has an admin/permission concept, note whether `/poka-yoke` itself should be admin-only or debug-mode in that deployment.
7. Report; don't patch. Apply the fixes only when the user asks.

## Example

`/poka-yoke target=/delete-user`

```json
{
  "audit_timestamp": "2026-08-27T14:02:11Z",
  "audited_commands": ["/delete-user"],
  "critical_risks": [
    {
      "issue": "No confirmation step before deleting user data.",
      "location": "src/commands/deleteUser.ts:34",
      "suggested_fix": "Wrap the deletion in a 2-step modal: Step 1 - 'Are you sure? Type CONFIRM', Step 2 - 'Deletion scheduled. Undo available for 5 mins.' Use ModalBuilder + TextInputBuilder, validate input === 'CONFIRM', then enqueue a 5-minute delayed job instead of calling db.delete() inline."
    }
  ],
  "medium_risks": [
    {
      "issue": "No feedback after deletion—user doesn't know if it worked.",
      "location": "src/commands/deleteUser.ts:41",
      "suggested_fix": "Add an ephemeral message: '✅ User @XYZ has been deleted.' via interaction.editReply({ content, ephemeral: true })."
    }
  ],
  "low_risks": [],
  "new_flow_design": {
    "if_applicable": "flowchart TD; A[/delete-user] --> B[Preview: show target user + owned records]; B --> C{Modal: type CONFIRM}; C -->|mismatch| B; C -->|match| D[Soft delete + 5min undo button]; D -->|Undo| E[Restore]; D -->|timeout| F[Hard delete + receipt]"
  }
}
```
