# Codex account-scoped history and the abandoned custom build

Investigated on 2026-11-13 with Codex CLI **0.169.0** on Kian's Linux
operator computer. This records the incident, evidence, attempted fix and
decision to abandon that fix. It is not an instruction to install a custom
Codex binary. [Shell modules](../shell-modules.md) owns the installed launcher
behavior; [Auth rotation](../auth-rotation.md) owns account selection.

## Why this investigation happened

Work appeared to disappear from Codex after a usage limit and restart.
Resuming showed older Guaranine conversations instead of the conversation that
had just been active. It happened again later the same evening, after work on
Grafana and application storage. Finding a conversation by its saved ID was
possible, but repeatedly recovering IDs did not address the cause.

The desired behavior was straightforward: local work should remain discoverable
when switching among the operator's saved accounts. The user wanted the account
filter disabled, not account rotation removed or a separate session-management
system introduced.

The sessions recovered during the incident included:

| Saved name | Thread ID | Context |
| --- | --- | --- |
| Set up first worker | `01a1d6fd-daa6-7df3-9bd0-6714caf672b5` | Guaranine CLI menus and KVM image-CI work; later monitoring work |
| Resume Grafana setup | `01a1d7af-421c-7591-b83c-becd361b0c8f` | Grafana, then KIAN-1006/OpenEBS application storage |
| Find recent Guaranine project | `01a1d73f-6679-75a2-b5f4-08feaaaa000a` | The recovery and account-scoping investigation |
| Resume main merge | `01a1d795-89a1-7ed0-a24f-0615ef9dddd7` | Argument Arena session recovered during the investigation |

The first two missing Guaranine sessions had ended on recorded usage-limit
errors. Their transcript files still existed and the corresponding records
were not archived. Their code changes also remained in the working checkout.
The original CLI/CI session and the later Grafana session were separate
conversations; recovering one did not recover the other.

## Evidence and cause

### Local state

The investigation read Codex's task-list/task-read tools, the local
`~/.codex/state_5.sqlite` database in read-only mode, and JSONL session headers
under `~/.codex/sessions/`. It compared creator identities without printing
account IDs, user IDs or authentication tokens.

Both missing Guaranine conversations contained `account_scoped: true` in their
`session_meta` payload. They and the recovery conversation had **three different
creator account/user identity pairs**. The task-list tool omitted the missing
sessions, while direct reads by thread ID and the local files still exposed
their saved history.

A later inventory found 1,782 thread records pointing to `.jsonl` paths and
28 indexed paths whose first session header was marked account-scoped. These
were measurements of this machine at that moment, not supported product limits
or a complete count of every physical rollout file.

### Upstream behavior

Upstream introduced the account discovery filter in
[commit 8806313](https://github.com/openai/codex/commit/8806313db4c82c89efcae3e6ac682bed6f4d6a66),
dated 2026-11-02, titled “Scope local history discovery to the current ChatGPT
account” (#58918).

In the investigated version:

- `thread/list` and `thread/search` accept `currentAccountOnly`, defaulting to
  `false` at the API boundary.
- When a client requests `true`, local discovery checks the saved creator
  user/account IDs against the active ChatGPT login for scoped sessions.
- New threads record the scope marker. Older unscoped threads and threads
  without creator IDs remain shared across account switches.
- API-key authentication sees all local history. Direct access by thread ID
  remains available; this is a discovery filter rather than deletion.
- Resume and revert preserve the distinction. For some reverted paths the
  implementation consults the original rollout header as well.

The release's [account-history documentation](https://github.com/openai/codex/blob/d32063f96c2dff1dc96af839b3952abb6acfeda4/codex-rs/app-server/README.md#local-history-by-account)
and [local account matching implementation](https://github.com/openai/codex/blob/d32063f96c2dff1dc96af839b3952abb6acfeda4/codex-rs/thread-store/src/local_history_account.rs)
establish those details. In particular, matching account IDs alone is not the
entire rule: the stored scope marker also matters.

The TUI hardcodes `current_account_only: true` in its
[resume picker](https://github.com/openai/codex/blob/d32063f96c2dff1dc96af839b3952abb6acfeda4/codex-rs/tui/src/resume_picker.rs)
and [latest-session lookup](https://github.com/openai/codex/blob/d32063f96c2dff1dc96af839b3952abb6acfeda4/codex-rs/tui/src/lib.rs).
Other TUI history surfaces and `codex exec resume --last` also request scoped
history.

### Interaction with Clusterfork

The installed `cc` implementation ran `rotate-codex` before opening
`codex resume --yolo`. Selecting another account therefore changed the set
of scoped conversations the native picker displayed. Older shared history
remained visible, explaining why the picker could look populated but omit the
conversation that had just been active.

Rotation exposed the problem; the filtering itself was in Codex. Earlier
responses correctly recovered the session IDs but initially stopped short of
diagnosing that account-filter interaction.

`--all` removes the working-directory restriction. It does not disable account
filtering. Searching across directories or resuming the latest *visible*
conversation therefore did not solve this incident.

## Checks for a native off switch

The investigation inspected the installed CLI's help and feature list, generated
its experimental app-server JSON schemas, checked the official configuration
reference, and read upstream source and account-history documentation. Some
initial source URLs returned 404; the relevant files were subsequently retrieved
from the repository and the exact release checkout.

`cli_resume_account_check` was present but already **false**. Its source
description concerns restricting CLI resume of attributed threads. It did not
control the hardcoded discovery requests: launching the unmodified picker with
`--disable cli_resume_account_check` still omitted other accounts' scoped
conversations.

The generated `ThreadListParams` and `ThreadSearchParams` schemas confirmed the
request-level `currentAccountOnly` switch. No user configuration switch was found
that made the stock 0.169.0 TUI send `false` instead of its hardcoded `true`.
An API request parameter is not automatically a `config.toml` option.

This conclusion is version-specific. Recheck later releases before assuming a
custom build or a metadata adjustment is necessary.

## Verification before attempting a build

A short-lived local `codex app-server --stdio` process handled read-only
`thread/list` requests. Initialization used the `codex-tui` client identity and
`capabilities.experimentalApi = true`. The test did not create a model turn.

Both requests selected the Guaranine directory, CLI sources, a limit of 100,
`sortKey: "updated_at"` and `useStateDbOnly: true`. Only
`currentAccountOnly` differed:

| Request | Returned threads | Original CLI session | Grafana session | Recovery session |
| --- | ---: | --- | --- | --- |
| `currentAccountOnly: true` | 16 | Missing | Missing | Present |
| `currentAccountOnly: false` | 18 | Present | Present | Present |

The script asserted that the scoped result was a subset of the unfiltered result
and that all three Guaranine IDs appeared in the unfiltered result. The server
was terminated after the check. Counts describe that observation, not the
current contents of the history database.

A separate PTY check opened the **unmodified** native resume picker, searched
for `grafana` and `worker`, and found neither target session under the active
login used for that run. It exited with Ctrl+C without selecting a conversation
or starting a model turn. This established a UI baseline for the proposed
replacement.

## The custom-build experiment

### Source and patch

The user first requested disabling the behavior in Codex itself. Because no
configuration switch was found, the attempted solution was a locally patched
client of the same release, leaving the server's optional account filter
available to other clients.

The checkout was cloned from `openai/codex`, tag `rust-v0.169.0`, at commit
`d32063f96c2dff1dc96af839b3952abb6acfeda4`. The source change was ten assignments
from `current_account_only: true` to `false` across eight files:

| File under `codex-rs/` | History surface | Assignments |
| --- | --- | ---: |
| `tui/src/resume_picker.rs` | Resume/fork picker | 1 |
| `tui/src/lib.rs` | Latest-session lookup | 1 |
| `tui/src/dynamic_tools.rs` | Agent-visible task list | 1 |
| `tui/src/task_mentions.rs` | Content and title searches for task mentions | 2 |
| `tui/src/analytics/chats.rs` | Local chat analytics | 1 |
| `tui/src/app/agents_overview_discovery.rs` | Pinned and recent task discovery | 2 |
| `tui/src/app_server_session/side_chat_history.rs` | Side-chat history | 1 |
| `exec/src/lib.rs` | Latest-session lookup in non-interactive resume | 1 |

The local checkout's existing `docs/install.md` was also annotated to explain
the intended visibility change and the fact that an unpatched upstream update
would restore filtering. That annotation describes the intended patch; it is
not evidence that a patched executable was built or installed.

No creator IDs, scope markers, conversation messages or SQLite history were
rewritten during this experiment. Clusterfork's launcher and rotation code
were not changed.

### Build environment and commands

The machine had Rust/Cargo 1.99.1, matching the release's Rust toolchain, plus
Clang, CMake, pkg-config and protoc. It had 20 logical CPUs and about 31 GiB RAM.
The build used three jobs at a lower scheduling priority. Release debug data,
LTO and incremental compilation were disabled to reduce build resource use.

The attempted build, from the checkout's `codex-rs/` directory, was:

```bash
CARGO_PROFILE_RELEASE_DEBUG=0 \
CARGO_PROFILE_RELEASE_STRIP=symbols \
CARGO_PROFILE_RELEASE_LTO=false \
CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16 \
CARGO_INCREMENTAL=0 \
CARGO_NET_GIT_FETCH_WITH_CLI=true \
STABLE_GIT_COMMIT=d32063f96c2dff1dc96af839b3952abb6acfeda4-unscoped \
nice -n 10 cargo build --locked --release -p codex-cli --bin codex -j 3
```

The initial build fetched Git dependencies and submodules, including Rusty V8
and its Chromium/V8 dependencies. Even this small behavioral patch therefore
required a substantial first source build.

The first build stopped because `--locked` rejected a release-tag mismatch:
the workspace manifests were version 0.169.0, while local workspace entries
in `Cargo.lock` still said 0.0.0. The corrective command was:

```bash
cargo update --workspace --offline
```

That updated **173 local workspace package versions**. A parsed before/after
comparison confirmed that all **1,310 external dependency records were
unchanged**, including versions, sources and checksums. The release build was
then restarted with `--locked` and reached compilation of Codex's own components
and further dependencies.

### Checks completed and work not completed

- The ten source assignments were changed and inspected.
- `git diff --check` passed.
- A targeted `rustfmt --check` passed. The stable formatter warned that the
  upstream `imports_granularity = Item` preference requires nightly support;
  no formatting changes were made to work around that warning.
- The stock server's unfiltered API requests returned both missing sessions.
- The stock native picker reproduced the missing-history symptom.
- **Compilation was not completed.** The second build was deliberately stopped
  before producing and validating the replacement executable.
- No Rust test suite or patched native picker was run successfully.
- No patched executable was installed, and no active daemon was restarted.

## Installation preparation and remaining artifacts

A runtime directory was staged separately from the active installation. It used
hard links to the original release's helper files and resources. Its copied
package manifest was replaced with a separate file and given the GNU Linux
target for the planned local build, leaving the original manifest intact.

**The staged `runtime/bin/codex` still contains the upstream executable.** It
was never replaced by a completed custom build. Its presence does not mean the
history patch is running.

The planned next steps had been to copy a successful build into this staged
runtime, verify both recovered sessions in the real picker, and atomically
switch the user-facing executable link with an original-client rollback path.
Those steps were not performed.

Artifacts retained when the custom-build attempt was abandoned:

| Local path | Contents/status |
| --- | --- |
| `~/.local/share/codex-unscoped/` | Exact release checkout, source edits, version-normalized lockfile and partial build output |
| `~/.local/share/codex-unscoped/runtime/` | Staged upstream runtime/resources; no compiled replacement installed |
| `~/.local/share/codex-unscoped.patch` | Exported eight-file source patch and local build-guide annotation; does not include the lockfile normalization |
| `~/.cargo/git/` | Git dependency cache populated by the build; about 3.6 GiB at abandonment |
| `/tmp/codex-unscoped-build.log` | Build log, including compilation progress before termination |
| `/tmp/codex-unscoped-lock.log` | Workspace-version normalization log |
| `/tmp/codex-unscoped-picker-check.py` | PTY visibility checker used for the upstream baseline |
| `/tmp/codex-account-source/` | Downloaded upstream source excerpts, tree listing and discovery-change evidence |
| `/tmp/codex-account-schema-qaz4ucsb/` | Experimental app-server schemas generated from the installed executable |

The source/staging directory used about 2.3 GiB at the abandonment check.
Hard-linked runtime files make directory-size totals unsuitable for calculating
additional disk use by simple addition. Temporary files may disappear on reboot;
the retained checkout is the durable source of the attempted patch.

The active launcher remained:

```text
~/.local/bin/codex
  -> /home/kian/.codex/packages/standalone/current/bin/codex
```

`codex --version` still reported `codex-cli 0.169.0`. The build's Cargo process
and its remaining descendants were explicitly terminated; unrelated Codex
sessions and other running work were left alone. No custom-build cleanup had
been performed when this record was written.

## Why the approach was abandoned

The source edit itself was small, but distributing it meant compiling a large
application, managing a separate executable and resources, keeping a rollback
path, and accounting for upstream updates restoring the filter. The user
explicitly stopped this direction because it was becoming too complicated for
the problem being solved.

The user proposed normalizing existing local sessions whenever `cc` starts,
instead of maintaining a custom Codex build. That fits the immediate goal more
closely: preserve the upstream executable and make existing local history
discoverable before opening its picker.

Reassigning every saved creator ID to the active account was considered. With
several accounts open concurrently, doing that at each launch would change
which account sees the sessions again and would replace useful provenance.
The proposed refinement was to clear the local `account_scoped` discovery marker
while retaining creator IDs. Upstream's matching code explicitly treats
unscoped headers as shared history.

At the end of the custom-build attempt, the launcher-based repair had not yet
been implemented or installed. It was a separate follow-up, not a result of
successfully building the patched CLI.

## Subsequent launcher repair and verification

The smaller repair was implemented in Clusterfork's existing
`scripts/account_sessions.py`, with a call from `cc` after rotation and before
opening the picker. [Shell modules](../shell-modules.md) owns its installed
behavior, backup location, limits and use.

The verification proceeded from copies to real history:

1. Copies of the two recovered Guaranine transcripts were loaded by a stock
   local app-server with no authenticated account. A scoped listing returned
   neither copied session; an unfiltered listing returned both. After clearing
   their discovery markers, the same scoped listing returned both, without
   changing the server's identity or restarting it.
2. Message hashes, creator IDs and header lengths were unchanged in those
   copied transcripts. A second repair run changed zero files.
3. The installed helper shared **30 real saved session headers**. All 30 original
   message prefixes were hash-verified afterward, allowing new appended records
   from active conversations. Header lengths and remaining metadata were
   preserved. The marker backup had mode 0600. A second run changed zero files.
4. The unmodified native picker then displayed **Resume Grafana setup** and
   **Set up first worker**. The existing agent task-list tool also returned
   both Guaranine sessions and **Resume main merge** from Argument Arena.

Targeted tests cover message/offset/inode preservation, continued appends,
private and reversible marker backups, idempotence, concurrent repair runs,
backup failures, archived/reverted paths, legacy headers, timestamp preservation
and launch ordering. **73 targeted history, launcher and statusline tests
passed** in the final combined run.

This workaround is verified against stock Codex 0.169.0 and the plain JSONL
history present on the machine. It does not establish compatibility with a
future upstream history format. The custom executable was never completed or
installed; the stock executable remained selected throughout.

After the replacement was verified, the abandoned checkout's `codex-rs/target/`
and staged `runtime/` directories were removed. The source checkout, exported
patch and diagnostic logs were retained as investigation artifacts. The shared
Cargo Git cache was also retained; no build or extra service remained running.
