# Steering a running agent

Post a new issue comment beginning with plain text `@clusterfork`, followed by
the instruction, for example `@clusterfork Keep the historical investigation
notes, but move them out of the setup guide.` No Linear bot user or actual mention
is required; the prefix is case insensitive. Discussion, quoted/code examples,
bot-authored comments, status comments, and description edits are not instructions.
The Linear personal API key's `viewer.id` is authorized by default. To trust other
people, explicitly add their Linear user UUIDs to the top-level
`"steering": {"trusted_user_ids": ["<user UUID>"]}` in
`automation/azure/projects.json`. Names, email addresses, and issue assignment
do not grant steering permission.

Enable **Issue comments** (`Comment`) alongside Issues/Projects in the existing
[Linear webhook](https://linear.app/developers/webhooks). Azure fetches current
comment authors and text itself. While coding, the authenticated runner polls
Azure every **10 seconds**, delivering at most one instruction per poll. Normal
latency is one poll plus service and agent safe-point latency, even without
Comment webhooks. Initialization/account selection can queue instructions until
coding starts. The shared **15-minute**
timer also scans mapped issues and known instruction records, recovering missed
events and late comments when no runner is polling.

Azure Table `ClusterforkInstructions` (partition `instructions`) freezes the first
observed addressed comment, its issue, dispatch key, exact GitHub run/attempt, and
worker attempt UUID. Atomic issue sequences preserve observation order; comments
in one complete paginated scan sort by creation time and UUID. Comment IDs dedupe
webhooks/polls globally, including comments moved between issues. Editing or
deleting a queued/accepted comment does not change or resend its frozen text;
post a new comment for another instruction. Empty instructions and text over
16,000 UTF-8 bytes are rejected, as are unauthorized authors.

One stable acknowledgement per instruction links the affected run attempt and
changes from **queued** (no agent confirmation) to **agent accepted** only after
Codex confirms `turn/steer`. The pinned CLI's [app-server interface](https://developers.openai.com/codex/app-server)
uses the existing thread and expected active turn ID, retaining files, context,
and the selected account lease. GitHub OIDC still authenticates every broker
request; Linear credentials stay in Azure. Validation, CI-gated PR publication,
and auto-merge continue after that turn ends. Testing/publication, a finished or
missing agent, or a comment predating the current attempt produces a clear
non-delivery response. No comment starts a workflow or changes issue state.

Explicit agent rejection reports **failed**. Unconfirmed handoffs expire after
**90 seconds** and become **uncertain** at the next poll or reconciliation. An
uncertain instruction is never automatically replayed or redirected to another
attempt; a late confirmation from the same
live session can correct uncertainty. Acceptance confirms receipt, not completion
of the requested change. Broker/Linear outages delay observation or reporting;
result acknowledgements retry without resending agent input, and stable comment
IDs plus timer refresh repair failed/ambiguous Linear writes. The running agent
continues during polling outages while its existing leases remain valid. Pause,
cancel, resume, and automatic job restarts are outside this interface.

[Remote runtime](remote-development.md) owns dispatch, readiness, publication, and lifecycle behavior.
