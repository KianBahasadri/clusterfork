# Remote account ownership

Saved Codex profiles come from
`~/.local/share/clusterfork-auth/codex/auth.json.*`. Enrollment includes
**all saved profiles by default, including the currently active account**, and
deduplicates aliases by provider account identity. Existing cloud credentials
are retained when enrollment runs again, so stale local copies cannot overwrite
refreshed cloud tokens.

The local `remote-profiles.json` manifest records cloud ownership. The Codex
rotator skips those profiles for new local selections. Enrollment preserves the
current symlink and local credential files; it does not stop or lease an already
running local session. Remote leases coordinate cloud jobs only. Simultaneous
local and cloud OAuth refreshes are not synchronized and can still conflict;
reclaim a profile before starting further local use of its credentials.

## Enroll and reclaim

Run these commands from the checkout on the writable auth-store host, with
Azure CLI authenticated to the configured subscription. Use an isolated Python
environment with the Azure dependencies:

```bash
python3 -m venv ~/.config/clusterfork/automation/venv
~/.config/clusterfork/automation/venv/bin/pip install -r automation/azure/requirements.txt
~/.config/clusterfork/automation/venv/bin/python automation/accounts.py enroll
```

Use `--profile NAME` to enroll particular profiles, including the active one.
`list` reports the number reserved. Reclaim a profile before using it locally again:

```bash
~/.config/clusterfork/automation/venv/bin/python automation/accounts.py reclaim --profile NAME
```

Reclaim refuses busy accounts or changed local credentials, persists the latest
cloud credentials locally, then removes cloud ownership. Keep the installed
rotator updated when enabling this feature.

## Leases and refresh

Private Azure Table `ClusterforkAccounts` holds auth caches. Each provider account
has one 120-second lease with a random fencing token and ETag updates. Jobs renew
every 20 seconds and persist refreshed auth during execution and on exit. A lost
lease stops the agent. A crash between OAuth refresh and persistence can require
re-login if the refreshed token was never saved.

## Worker credential boundary

The broker keeps its App private key in Azure and does not send it to the runner.
It verifies GitHub's OIDC signature, issuer, audience, expiry, repository,
main-branch workflow path, hosted-runner type,
run ID/attempt, and a durable dispatch receipt. It also checks the run through
GitHub's API and requires dispatch by `clusterfork[bot]`. PR workflows, forks,
other workflows, and unmapped repositories cannot obtain account credentials.
Publication tokens are scoped to one repository with Contents and Pull requests
write permission, plus Workflows write when the installation currently grants it.
The broker checks that grant at worker start and again before issuing publication
credentials; checkout tokens stay read-only. Tokens are issued again before
publication and never passed in workflow inputs. Account caches and agent
transcripts are not uploaded as artifacts.

The storage resource is currently named in `automation/accounts.py`; see
[remote setup](remote-development-setup.md#azure-deployment) before relocating it.
Local rotation commands and container mounts are covered by
[Auth Rotation](auth-rotation.md).
