# Remote development recovery tools

Current repairs run as a second attempt on the original Linear issue, using
broker-supplied instructions and the original passing readiness review. They do
not create a generated child issue or check `repair_revision`. A pending repair
held after human title/description edits needs a fresh readiness review;
rebinding a generated Markdown hash cannot authorize those edits. The
[runtime](remote-development.md#single-attempt-auto-repair) owns the current lifecycle.

`automation/recover_repair_binding.py` is retained for legacy generated child
records and older brokers that denied dispatch because Linear's saved Markdown
link form differed from their recorded binding. Current broker reconciliation
can settle existing separate child records without that hash check. This tool
is unnecessary for current repairs and does not grant readiness approval. Pass
either the legacy parent or child issue identifier/UUID. The shared renderer in
`automation/azure/auto_repair.py` supplies the expected title and description.

Run from the checkout with the pinned Azure dependencies installed, an Azure CLI
login that can read the existing storage connection, and the normal ignored
root `.env` Linear configuration:

```bash
python3 automation/recover_repair_binding.py --issue KIAN-908
python3 automation/recover_repair_binding.py --issue KIAN-908 --apply
```

The default is read-only. `--apply` requires a healthy, unpaused broker whose
source digest matches the checkout. The tool verifies the original task against
its passing readiness review and compares the repair's complete title and
description to the generated content. The old hash must correspond to the known
unbracketed GitHub trigger-link form or an already-correct binding. Actual content
edits, inconsistent targets, completed/canceled/archived issues, an occupied
worker identity, or any repair dispatch/worker receipt refuse recovery.

Before changing state it saves a mode-0600 audit under
`~/.local/state/clusterfork/recovery/repair-bindings/`; `--backup-dir` overrides
that location and `--env-file` selects another dotenv file. Audits contain IDs,
hashes, ETags, and operation outcomes, without issue bodies or credentials. One
ETag-fenced transaction checks the parent, child, and slot and updates only the
parent's `repair_revision`. Repair budget, issue UUIDs, queue position, slot, and
dispatch receipts are retained. It then sends a normal Project reconciliation
event so the existing pipeline can dispatch the repair.

If queuing fails after the transaction, the audit distinguishes the applied
binding from the unqueued event. Inspect current state with a dry run and repeat
`--apply` if it still passes. A repair that has since crossed dispatch must be
investigated through its recorded attempt. This tool does not authorize retries
of failed repair workers or reset a generation.
