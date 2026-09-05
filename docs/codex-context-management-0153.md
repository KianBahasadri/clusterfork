# Codex experimental context management (0.153.0) — wanted, deferred

Investigated 2026-09-05. Nothing enabled and nothing changed in the repo.
Recorded because this is a feature worth adopting, deliberately deferred, and
the reasons for waiting expire on a schedule that is easy to forget.

## What it changes

Codex's default history handling is **compaction**: when a thread outgrows the
context window, older turns are summarized into a compact object and the
originals are dropped. The summary then occupies context permanently, and a
long session compacts repeatedly — a summary of a summary. Detail erodes at
each pass and is unrecoverable. The model does not know something is missing.

The experimental mode replaces that with **notes plus searchable history**:

| Default (compaction) | Experimental context management |
|---|---|
| Old turns squashed into one summary object | Active context, structured notes, and full history kept separately |
| Dropped detail is permanently lost | Original history retained, fetched back on demand via the `new_context` tool |
| Repeated compaction causes summary drift | Notes carry state; history carries evidence |
| Whole summary always occupies context | Only currently relevant history is pulled back |
| Fails by forgetting | Fails by not retrieving |

Also activates token-budget context management alongside the history notes.

The failure mode is the point. Compaction fails *loudly* — the model
contradicts itself and the gap is obvious. Retrieval fails *quietly* — the
information still exists, the model just did not fetch it, so the output looks
like confident work on complete context. Quieter is not automatically better.

## Upstream status as of 2026-09-05

- Shipped in **codex-cli 0.153.0 on 2026-09-02** — three days before this
  investigation. No release since mentions it again.
- Off by default. Documented as experimental.
- The entire official documentation is one line: *"Enable experimental context
  management (off by default)."* This is the same boilerplate used for
  `features.network_proxy` and `features.prevent_idle_sleep` — there is no
  signal in the wording, it is a template.
- **No recommendation either way from the Codex team.** No usage guidance, no
  known-limitations note, no endorsement.
- The adding PR ([openai/codex#42385](https://github.com/openai/codex/pull/42385))
  was opened by a bot account and calls the configuration
  "under-development". Its tests cover eligibility, backend restriction, and
  config resolution — plumbing, not behavior quality.
- **No stated timeline for becoming the default.** Nothing on a roadmap,
  nothing in the changelog. Any claim about when it graduates is a guess.

Nothing guarantees the key keeps its name, stays present, or behaves the same
across releases.

## Eligibility — this account qualifies

The feature is gated. It requires ChatGPT sign-in on **Plus, Pro, or Pro
Lite**. Excluded: API-key sessions, custom providers, non-Codex endpoints, and
temporary structured threads. On an excluded setup the flag silently does
nothing rather than erroring.

Verified 2026-09-05 that this account is eligible — `auth_mode = "chatgpt"`,
plan type `plus`. Re-check without printing secrets:

```bash
python3 - "$(readlink -f ~/.codex/auth.json)" <<'PY'
import json, sys, base64
d = json.load(open(sys.argv[1]))
print("auth_mode =", d.get("auth_mode"))
tok = d["tokens"]["id_token"]
p = tok.split(".")[1]; p += "=" * (-len(p) % 4)
claims = json.loads(base64.urlsafe_b64decode(p))
print("plan =", claims["https://api.openai.com/auth"].get("chatgpt_plan_type"))
PY
```

Note the JWT's subscription window is a cached snapshot and can read stale;
`auth_mode` and `chatgpt_plan_type` are the fields that matter here.

## Present in the installed build

Confirmed in 0.153.4 — the config key is real for this version, not a future
release. The schema carries exactly one field (string tables run together in the
binary, so the output is concatenated):

```bash
strings -n 6 ~/.codex/packages/standalone/current/bin/codex \
  | grep -o 'ContextManagementConfigToml[^ ]\{0,40\}' | sort -u
# ContextManagementConfigToml
# ContextManagementConfigTomlexperimental_modeCurrentTimeReminderConf
# ContextManagementConfigTomlstruct
```

Supporting machinery is compiled in alongside it: `new_context` and
`read_context` tools, plus internals named `use_history_notes_extension`,
`ad_hoc_notes`, `thread_hint`, and `omitted_thread_count`. The binary carries
no stability metadata — `features.context_management` appears in a flat list
of feature keys with no tier or label — so the binary cannot tell you whether
a flag is experimental. Only the docs can.

## The "saves tokens" claim is a misread

The community claim that prompted this — a forum post reporting "ran an hour
and only used 2%" — is measuring the wrong number, and it will keep
circulating. Recorded so it does not get re-believed later.

Codex's status line reports **context-window usage**: how full the model's
working memory is right now. That is not **token spend**, which is what counts
against the rate limit.

This feature drives context-window usage down *by construction* — moving
history out of the window into retrievable storage is the entire feature. A
low context reading is the feature working, not evidence of savings. Spend can
plausibly go **up**: each retrieval is an extra round trip, and retrieved
history re-enters context anyway.

The local status line is already configured to show both
(`context-used` and `five-hour-limit` in `[tui] status_line`). **Judge this on
`five-hour-limit`, not `context-used`.** If only the latter drops, the context
benefit is real and the savings claim is not.

## How to enable, when the time comes

`[features]` already exists in `~/.codex/config.toml`; add the key to it:

```toml
[features]
terminal_resize_reflow = true
apps = false
context_management = { experimental_mode = true }
```

Fully restart Codex — a reload is not enough. To revert, delete the line and
restart. It changes context assembly only; sandbox, approvals, and per-project
trust levels are untouched, so the blast radius is small.

## Where the setting belongs — live config, not the template

Put it in `~/.codex/config.toml` directly. **Do not add it to
`agents/codex.toml` while it is experimental** — an experimental key can be
renamed or dropped, and the template would then carry a dead setting into
every install.

This is safe because the installer preserves it. The codex config merge
replaces only the top-level tables that the template itself defines
(`wanted_tables` in `install-clusterfork.sh`), and `agents/codex.toml` defines
only `[mcp_servers]` and `[hooks]`. Every other table in the destination —
`[features]`, `[tui]`, `[notice]`, `[plugins]`, `[projects.*]` — is kept. The
existing `terminal_resize_reflow` surviving reinstalls is the standing proof.

If it graduates out of experimental and gets adopted permanently, *then* it
moves into `agents/codex.toml` so the repo stays the source of truth.

## Decision

Decision on 2026-09-05: **do not enable yet. Revisit on or after 2026-09-19.**

Not a rejection — the design argument is good, and it fits this setup
specifically. Sessions here run `gpt-6-astra` at `model_reasoning_effort =
"max"`, which means long turns and many compactions per thread: exactly where
summary drift compounds. Trading "I forgot" for "I did not retrieve it" is a
favorable trade for that workload.

Waiting because the feature was three days old at investigation time, carries
no team endorsement, and has no public track record. Day-one Codex features
have cost real time here before — see
[codex-rewind-bug-0147.md](codex-rewind-bug-0147.md), a 0.147.0 regression
that took an investigation to trace to the binary rather than to clusterfork.

### Re-check list

1. **Still there, still named the same?** Re-run the `strings` check above
   against the then-current build. A rename or removal answers the question.
2. **Changed status?** Check the
   [config reference](https://learn.chatgpt.com/docs/config-file/config-reference)
   for whether the line still reads "experimental ... off by default", and the
   [releases](https://github.com/openai/codex/releases) for any entry that
   expands it, flips the default, or graduates it.
3. **Any word from the team?** The bar is anything beyond the one-line doc
   entry — usage guidance, a known-limitations note, a roadmap mention.
4. **Early-adopter reports?** Search openai/codex issues for
   `context_management`. Specifically look for silent-retrieval-failure
   reports, which is the predicted failure mode.
5. **Did anyone measure it properly?** Cost claims are only credible if they
   report rate-limit consumption, not context-window percentage.

If it is still experimental with no word but also no bug reports, that is a
reasonable point to enable it on low-stakes sessions and measure
`five-hour-limit` directly rather than keep waiting on an announcement that
may never come.
