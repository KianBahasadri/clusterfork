# Hex dictation

Clusterfork owns the reusable dictation setup in `hex/`. Edit that source and
reinstall it, rather than editing the installed helpers. The installer copies
files instead of linking the workspace: a Git checkout or pull does not change
the active dictation code. Hex also creates its own typings and instructions
in that workspace, so the directory itself is not replaced.

## Installation

With Hex installed, `./install-clusterfork.sh` includes this integration.
`--skip-hex` leaves it untouched. The installer does not download or launch Hex.
It validates the copied transformation locally. Start Hex before installing to
apply the rewrite profile and vocabulary; if Hex is stopped, the workspace is
copied and the installer asks you to rerun it when Hex is running.

To update only Hex from the repository root:

```bash
uv run --no-project python hex/install.py
```

The source files and destinations are listed in [Installation](installation.md).
The installer also writes `~/.config/hex/linear/opencode.json`, the workspace
configuration for Linear requests. That workspace enables `linear` and disables
the shared-roster servers plus `opencode_console`, so unrelated MCP servers do
not start or race Linear for the first model turn. Global OpenCode settings and
other workspaces are left alone.
The installer updates only the Global mode's rewrite fields and places
`conversation-context` first in its transformations, preserving the other
transformations' order. It preserves replacements, mode vocabulary, app/site
rules, unrelated settings, the selected speech model, and microphone choice.
Settings are changed through `hex settings`, never by editing the running
app's `settings.json`. Seed vocabulary is merged through `hex vocabulary add`,
preserving existing terms, spellings, and additional aliases. A removed seed
term is restored on reinstall; remove it from `hex/vocabulary.json` to make
that removal persistent.

The workers are enabled in the matching systemd user session after
live settings are applied. Changed worker code restarts an active worker;
unchanged reinstalls do not restart it. A disposable HOME cannot activate the
host user's services. Without a matching user manager, enable the installed
units in the desktop session.

```bash
systemctl --user daemon-reload
systemctl --user enable --now hex-vocabulary-review.service hex-linear-issue.service
```

New or changed transformation configurations require **Allow** in Hex's Output
pane. The installer does not write Hex's approval state. Unchanged config
bytes are not rewritten, preserving an existing approval. The transformation
template keeps its old Luna wording because changing its bytes would invalidate
this machine's approved configuration; Luna is no longer the rewrite model.
The actual model comes from `hex/rewrite.json`, which selects Claude Haiku 5.5.
The helper still bounds its history excerpt to 2000 words, matching the
template's total-word-limit description. Hex-owned `AGENTS.md`, `hex.d.ts`,
and `tsconfig.json` are left alone. Run
`hex commands init` if you want Hex to generate or refresh its editor typings.

## Typing

Hold **numpad `9`** to dictate into the field you were using when recording
began. On release, Hex transcribes and runs its Output steps, including the
contextual rewrite. The finished text is saved in Hex History as a **Printed**
entry. **Double-tap numpad `9`** to open the shared dialog on Typing, or
**double-tap numpad `/`** to open it on Linear. Both numpad 9 gestures work
with Num Lock on or off. Numpad minus runs Hex's regular paste action.

The tabs share one draft. Press either keypad shortcut while the dialog is
open to select its tab in the same window. Enter adds a line. **Ctrl+Enter**
closes the dialog and submits to the active tab. Typing restores the original
window and field. Use the two-option toggle to select a profile, then choose
**Train BioCadence** to open its training window. **Thinking typing** rotates
through 15 free-writing prompts, one per session. **Copy typing** shows the
passage with inline feedback as you type.
Each can be saved after at least 40 typed characters. Hex captures presses and
releases inside the training field and saves the fitted statistics and
selection in `~/.local/state/hex/typing/profiles.json` or under `XDG_STATE_HOME`.
An untrained selection shows a warning before the draft field and uses default
timing. The AI-writing estimate, focus-break control and speed slider are
disabled.

Both Typing routes use the selected profile's dwell, letter-pair timing,
boundary pauses and correction rate. Replay sends only keyboard events.
It uses Shift+Enter for line breaks, Tab for tabs and Ctrl+Shift+U with
hexadecimal digits for Unicode entry. The target field controls which of
these sequences it accepts. Press **numpad 9** again to stop replay. Playback
also stops if the destination loses focus or an unexpected modifier key is
held, leaving any partial text in place.

The installer enables the bundled **Hex Typing Focus** extension for GNOME
Shell 51. The shell loads new or changed extension code at the next sign-in.
The shared dialog and training work as soon as the worker starts. The Typing
tab explains when the destination is unavailable and keeps the draft editable.
The extension identifies and restores the destination window and guards key
delivery inside Mutter. It also renders bottom-right stacked, four-step
segmented progress bars (`Queued`, `Connecting to Linear`, `Creating Linear issue`,
and completion/error auto-dismiss) while Linear requests run, stacking newer
requests above older ones and hiding automatically on the lock screen or worker
exit. All extension D-Bus methods (`Get`, `Activate`, `Ready`, `Key`, `End`,
`Progress`, `ProgressClear`) accept calls only from the main process of
`hex-linear-issue.service`, and progress labels use plain sanitized text with
non-reactive click-through chrome. Key events travel over the local session
bus and are visible to processes with bus monitoring access. Field-level
focus tracking uses AT-SPI when the target exposes its editor. The worker
shares its Global Shortcuts registration with Linear dictation and requests
keypad Page Up (`KP_Prior`) for numpad 9.

When changing saved GNOME portal bindings programmatically, apply them to the
active session through `org.gnome.GlobalShortcutsRebind.RebindShortcuts` on
`/org/gnome/globalshortcuts` at `org.freedesktop.impl.portal.desktop.gnome`.
A dconf write alone leaves the active shortcuts unchanged.

### Keystroke benchmark

The offline benchmark uses the same draft with two synthetic training
profiles and a fixed replay seed. Each trace had four backspaces, 21 two-letter
bursts and 14 three-letter bursts within words at gaps of at most 100 ms.
The median within-word gap was 88 ms for both. Word and sentence gaps were
312 and 877 ms for thoughts, and 219 and 456 ms for copy typing.

| Training profile | is-human-cadence v1.6.0 | HIDSentry 0.1.0 |
|---|---|---|
| Thinking typing | Unknown (0.663) | Human (0.006) |
| Copy typing | Human (0.738) | Human (0.008) |

is-human-cadence flagged all 246 keydowns in each trace as synthetic events.
These scores describe the keystroke traces. A text-only estimator sees the
same finished draft from either profile. The [benchmark script](../tests/hex_typing_benchmark.py)
pins both detector revisions. The [BioCadence MIT notice](../hex/BIOCADENCE-LICENSE)
is included with the adapted code.

## Dictating Linear issues

The Slash agent is a Linear MCP bot for capturing work as issues and managing
Linear projects and issue fields. Actual task execution happens outside this
bot. The underlying draft or implementation details are unnecessary when the
request already supplies enough context to record the task.

**Double-tap numpad `/`** to open the Linear tab. Hold **numpad `/`** or do a single
quick tap does nothing. Inside the Linear tab, type or use **Typing** (e.g.
numpad `9`) to dictate the request, choose an optional project suggestion chip
(**Auto** by default), and select **Send request**. **Ctrl+Enter** also sends
the request; Enter adds a new line. Both taps must be shorter than a quarter
second, with at most 350 ms between them. Repeated double-taps while the
window is open bring the existing window forward on the Linear tab without
creating additional windows. Cancel, Escape, or closing the box discards an
unsent request.

The text entry grows as you write up to a scrollable height cap. Select the
top-right **`?`** button to open a separate keyboard-shortcuts window. **Auto**
project selection lets the assistant pick the project from Linear, while
selecting a project chip passes it as a non-binding hint (`Suggested project`).
Cached projects load immediately from
`~/.local/state/hex/linear/projects_cache.json` and refresh in the background
from Linear's GraphQL API.

Paste screenshots or images with **Ctrl+V**, **Ctrl+Shift+V**, or **Paste
image**. Each image has a preview and a remove button. You can paste up to six
images at a time, at most 5 MiB each and 12 MiB combined. Images are captured
when pasted and stay with the request even if the clipboard changes. Pasted
images are attached to the resulting issues unless you ask to use them only as
reference.

Select **History** on the Linear tab to browse recent Linear requests, their
timestamps, original transcripts, direct Linear links, and attached screenshots
saved under `~/.local/state/hex/linear/history.json` and `history-images/` (up
to 50 entries, pruning orphaned screenshots on eviction). The helper also passes
up to five non-expired prior requests (under 1 hour old, bounded to 1500
characters) as reference-only background context when drafting new requests.

One request can contain several independent issue requests. Each completion
notification shows the issue link and any confirmed dependency links.
If you request an issue under a new project, the assistant can create the
project first when its name, team and purpose are clear.

Identify the issue by ID or by a clear name or description. You can edit its
title, description, project, status, priority, estimate, due date, and labels,
or add a blocker or attached image. Adding context appends to the description.
The service asks for a reply when it cannot identify the issue confidently.

If you get a clarification question, click that notification or its **Reply**
button to open the GTK 4 reply box. Use your regular Hex shortcut to dictate
into the box, type, or paste one or more images (including image-only replies
with no text), then select **Send reply**. The answer goes to that specific
request. Ctrl+Enter also sends replies. Another Linear request starts a fresh
OpenCode session; replies resume the pending session in memory, preserving
lookups and any completed changes.

Notifications pass `--hint=string:desktop-entry:io.github.KianBahasadri.Clusterfork.LinearDictation`
so GNOME Shell groups them under **Linear Dictation**, and escape only `&`,
`<`, and `>` (`html.escape(..., quote=False)`) so apostrophes and quotes render
literally instead of entity codes.

The integration uses the Global Shortcuts portal and its host app registry. On
GNOME, approve **Linear Dictation** and its numpad slash binding if the desktop
prompts. The installed `hex-linear-issue.service` runs in the graphical user
session and uses system Python with Gio, `notify-send`, GTK 4, Libadwaita, Pillow, and
OpenCode Go and Linear credentials. For each request the helper reads the shared
Linear credential from `~/.config/clusterfork/.env`, honoring an environment
override, and starts a short-lived private OpenCode server with it. Before
submitting the prompt, the helper waits up to 45 seconds for that server's
`/api/mcp` status to report Linear connected with its tools loaded. A missing,
failed, unauthenticated or disabled Linear server produces a specific
notification and attempt diagnostic instead of sending the request. The private
server is stopped after success, failure or timeout. Text and image replies use
GTK 4 (`hex/linear_reply.py`). The separate portal registration for Typing
uses the same running Hex and selected transcription model (`hex dictation
start`, followed on release by `hex dictation stop --print`), while Linear
requests are submitted directly through the GTK 4 dialog.

The helper sends the request text, foreground-window hint, local date,
optional project hint, image metadata, bounded recent requests, and follow-up
answers. OpenCode Go runs
`deepseek-v4.1-flash#max` in an archived session with `hex/linear-agent.md` as
its instructions. The workspace plugin removes the skill loader; this bot uses
its supplied role and Linear MCP tools directly. The assistant loads the
current projects, their summaries and descriptions through Linear MCP before choosing
a project. A workspace plugin presents project rows with their
names, statuses, teams, summaries, descriptions and URLs. Longer descriptions
can be read through `linear_get_project`. The assistant queries issues, labels,
workflow states and other details as needed. Issue search results contain
identifiers, titles, projects, statuses, labels, priorities, estimates and
dates. The assistant reads descriptions and relations for relevant issues,
including older and cross-project prerequisites. An open analysis or plan is a
blocker when the new work depends on its outcome.

The assistant performs project and issue creation, edits, assignments and
dependency changes directly through MCP. Its instructions require preserving
the dictated context, supplying priority and estimate, using Backlog for new
issues unless requested otherwise, checking duplicates, verifying saved changes
and correcting recoverable errors. After an uncertain save, it checks the
result before retrying. These checks run in the agent. The Python helper runs
the CLI and handles the desktop interaction. Each private OpenCode invocation
has a twenty-minute limit.

The assistant calls `hex_notify` with a verified issue or project URL to queue a
GNOME notification. The Python service displays it with **Open in Linear** when
the run finishes, including any completed results from an interrupted run.
Clicking the notification itself also opens the link. The click handler stays
active until the notification is opened or dismissed, and forwards GNOME's
activation token when launching the browser. Browser-launch failures go to the
service journal. The assistant uses
`hex_reply` to ask for missing details or clarification. The final response
is plain text. For an attachment, `hex_upload_clipboard` sends the current
request's image using the signed URL and headers that Linear MCP returned for
its filename in the same session. It returns the image link for the description.
Hex grants access to the upload directory only for attachment requests that can
write to Linear.

The service journal records the same request ID from submission through the
Linear save, alongside the OpenCode session ID and any created or updated
issue IDs. The `attempt=` entry points to a private directory under
`~/.local/state/hex/linear/runs/`, or the equivalent under `XDG_STATE_HOME`.
Each reply keeps the request ID and gets a new attempt directory.

`transcript.txt` holds the verbatim Hex transcript or typed request and is saved
before the Linear catalog is loaded. `prompt.txt` holds the exact bytes supplied on
OpenCode's stdin. `stdout.jsonl` records OpenCode's events, including Linear
lookups, project descriptions, write arguments and results, notification/reply
requests, and the final response. `stderr.txt` records CLI errors.
Both streams are written as OpenCode runs, preserving partial output on failure
or timeout. `mcp.json` records the Linear MCP startup outcome, the configured
server statuses and the private server's exit status; credentials are redacted.
When a session ID is available, its archive also contains tool results and any
supplied images.

For a saved issue, search the journal by issue ID, then by dictation ID. For a
failed attempt, start with the recent journal entries and open its `attempt=` path.

```bash
journalctl --user -u hex-linear-issue.service -n 50
journalctl --user -u hex-linear-issue.service --grep 'KIAN-123'
journalctl --user -u hex-linear-issue.service --grep '<dictation-id>'
opencode api GET '/api/session/<session-id>/message'
```

To check the workflow with text, the dry run uses a separate OpenCode agent
whose permissions allow Linear reads and clarification requests. It uses the
same Linear MCP readiness gate and private server lifecycle. It prints the
proposed actions:

```bash
printf '%s\n' 'Make a Clusterfork issue to add a relay health check' |
  /usr/bin/python3 ~/.config/hex/linear/linear_issue.py --dry-run
journalctl --user -u hex-linear-issue.service
```

## Editing behavior

`hex/prompt.md` asks for concise, direct messages in the speaker's voice:
remove filler, repetition, unnecessary lead-ins, and abandoned false starts;
combine overlapping statements; keep a final explicit correction instead of
its superseded wording. Preserve distinct requests, constraints, meaningful
uncertainty, quotations, identifiers, and intended numbers. Preserve unresolved
contradictions instead of guessing. `@HOME@` is expanded during installation
in the personal Guaranine repository reference.

`hex/rewrite.json` selects Claude Haiku 5.5 on OpenCode Go, base variant `none`,
with a 30-second deadline. The helper reads these values from the saved Global
profile for each dictation. With that model and base variant, a current
dictation of **100 words or more** requests `medium` reasoning for that rewrite;
shorter dictations keep `none`. Only the current text counts toward the
threshold, not history, vocabulary, or instructions. `LONG_DICTATION_WORDS` in
the helper owns the threshold. The selection is per request: it does not write
settings or change the background vocabulary worker's effort. Other models and
explicit reasoning variants are preserved. The helper logs the current word
count and selected variant. Reasoning shares the existing deadline and failure
fallback. Built-in post-processing stays disabled because the
`conversation-context` transformation performs the rewrite once.

The helper includes up to 2000 whitespace-separated words from earlier
nonempty dictations retained in Hex History. There is no age or message-count
cutoff. Newest entries take priority; the oldest included entry can be trimmed
to its final words. It uses `final_text`, after the previous rewrite, and sends
entries oldest first as untrusted spelling/reference context. A known
foreground app restricts history to that app; an unknown app permits context
from multiple apps. Only the current dictation is rewritten.

Requests go through the local OpenCode service on loopback. If the connection
is refused or the service registration is missing, the helper starts OpenCode
and retries once with the same model and reasoning variant within the original
deadline. The provider receives the current dictation, selected history
excerpt, shared vocabulary, prompt, and available foreground context. If the
rewrite still fails or reaches its deadline, Hex keeps the original transcript
and shows a desktop notification.
The template uses an argument array and stdin for dictated text, with no shell
interpolation.

## Vocabulary

`hex/vocabulary.json` is the curated starting list, including the original
terms and phonetic aliases. To add a permanent term, edit that list and
reinstall. For an immediate local addition:

```bash
hex vocabulary add 'Kobo Clara' --sounds-like 'kobo clone'
hex vocabulary list --json
```

Names belong in vocabulary. Fixed substitutions and deletions belong in
replacements; this integration does not add any.

The separate vocabulary worker polls saved History once per second, after Hex
has delivered the text. It requests `low` reasoning from the selected rewrite
model. A new name must appear in both the current original and
rewritten text and in an earlier, separate original dictation. Repeating a
word within one message does not qualify. Only high-confidence uncommon, custom,
or easily-misheard proper names and technical terms qualify (everyday English
words, mainstream tech/brand terms, and determiner or generic phrases are
excluded); at most three are added per dictation, without phonetic aliases, and
automatic additions stop once live vocabulary reaches 80 entries.
Typed, printed, and raw dictations do not learn vocabulary.

The worker starts after existing history on its first launch. Its private
watermark survives restarts; failures do not block later reviews. Removed
auto-added terms are not added again by the worker. Automatic additions stay
local until deliberately added to the curated source list.

## Optional PCM2902 microphone profile

This laptop uses a PCM2902 / C-Media USB PnP microphone instead of the built-in
microphone. Its hardware-specific rules are opt-in:

```bash
./install-clusterfork.sh --hex-microphone pcm2902
# Or update only Hex and this audio profile:
uv run --no-project python hex/install.py --microphone pcm2902
```

The profile prefers the USB capture nodes, hides capture on the laptop's
`pci-0000_00_1f.3` controller, and sets Hex's microphone to
`PipeWire Sound Server`. A WirePlumber service drop-in reapplies an output-only
profile and hardware capture mute, retaining built-in playback. These IDs are
specific to this hardware; review them before using the profile elsewhere.
Installation does not restart audio. The rules apply at the next login or
WirePlumber restart. Once installed, they persist until explicitly removed.

To remove the audio profile, delete its WirePlumber fragment and service
drop-in, then reload the user manager and restart WirePlumber. Restore the
controller's duplex profile and hardware capture level if built-in capture is
wanted again. The original machine's pre-change backup remains local under
`~/.local/state/audio-input-backups/`.

## Private state and diagnostics

History, recordings, downloaded models, credentials, local API registrations,
Hex approval state, vocabulary review watermarks, and automatic-addition logs
are runtime data and are not installed from or exported to this repository.
The installer never reads or copies dictation history. Seed vocabulary and the
personal spelling reference are intentionally tracked; review additions before
committing them.

```bash
hex commands check
systemctl --user status hex-vocabulary-review.service
journalctl --user -u hex-vocabulary-review.service
systemctl --user stop hex-vocabulary-review.service
```

Run the focused tests using the Python environment in [Conventions](conventions.md):
`python -m unittest discover -s tests -p 'test_hex_*.py' -v`.
