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
The installer updates only the Global mode's rewrite fields and places
`conversation-context` first in its transformations, preserving the other
transformations' order. It preserves replacements, mode vocabulary, app/site
rules, unrelated settings, the selected speech model, and microphone choice.
Settings are changed through `hex settings`, never by editing the running
app's `settings.json`. Seed vocabulary is merged through `hex vocabulary add`,
preserving existing terms, spellings, and additional aliases. A removed seed
term is restored on reinstall; remove it from `hex/vocabulary.json` to make
that removal persistent.

The vocabulary worker is enabled in the matching systemd user session after
live settings are applied. Changed worker code restarts an active worker;
unchanged reinstalls do not restart it. A disposable HOME cannot activate the
host user's services. Without a matching user manager, enable the installed
unit in the desktop session:

```bash
systemctl --user daemon-reload
systemctl --user enable --now hex-vocabulary-review.service
```

New or changed transformation configurations require **Allow** in Hex's Output
pane. The installer does not write Hex's approval state. Unchanged config
bytes are not rewritten, preserving an existing approval. The template retains
the old Luna description to preserve this machine's approved configuration;
the actual model comes from `hex/rewrite.json`, which selects Haiku.
Hex-owned `AGENTS.md`, `hex.d.ts`, and `tsconfig.json` are left alone. Run
`hex commands init` if you want Hex to generate or refresh its editor typings.

## Dictating Linear issues

Hold **numpad `/`**, say what to create or update, and release. Say “Make a
Clusterfork issue to add a relay health check, blocked by KIAN-123” or “Set
KIAN-123 to High priority and add that it needs to cover timeouts.” One
recording can contain several independent issue requests. The service copies
the transcript to the clipboard as soon as Hex transcribes it and verifies the
copy by reading it back, then saves the issues in the background. Hex History
retains the raw transcript as a **Printed** entry, with its recording when
Keep recordings is enabled. A copy failure shows a notification pointing to
History for recovery. Paste the transcript wherever you
meant to send it if you pressed the slash shortcut by accident. Each completion
notification shows the issue link and any confirmed dependency links.
If you request an issue under a new project, Luna can create the project first
when its name, team and purpose are clear.

Identify the issue by ID or by a clear name or description. You can edit its
title, description, project, status, priority, estimate, due date, and labels,
or add a blocker or clipboard image. Adding context appends to the description.
The service asks for a reply when it cannot identify the issue confidently.

If you get a clarification question, click that notification or its **Reply**
button to open the multiline answer box. Use your regular Hex shortcut to
dictate into the box, or type, then select **Send reply**. The answer goes to
that specific request. A separate numpad `/` recording starts a fresh request.
Pending replies are kept in memory while the service runs. A reply resumes
the same OpenCode session, preserving lookups and any completed changes.

Say **“Check the image in my clipboard”** to use a PNG, JPEG, or WebP image when
drafting. Say **“Attach the image in my clipboard”** to also display it in
Linear. When the service recognizes the instruction, it takes a copy before
placing the transcript on the clipboard and keeps that image with the draft.
Luna checks whether the image belongs with the issue. If the draft needs an
image, copy the intended image and click **Reply** to resume the same request.
If the clipboard has no usable image, **Reply** opens a dialog. Copy the image,
then select **Use Clipboard Image** in that dialog.

The integration uses the Global Shortcuts portal and its host app registry. On
GNOME, approve **Linear Dictation** and its numpad slash binding if the desktop
prompts. The installed `hex-linear-issue.service` runs in the graphical user
session and uses system Python with Gio, `notify-send`, `wl-clipboard`, and
OpenCode Go and Linear credentials. For each request the helper reads the shared
Linear credential from `~/.config/clusterfork/.env`, honoring an environment
override, and starts a private OpenCode server with it. Text replies use GTK 4.
Clipboard image replies use Zenity, and image validation uses Pillow. The
separate portal registration uses the same running Hex and selected
transcription model. Capture uses `hex dictation start --raw`, followed on
release by `hex dictation stop --print`. Hex owns History persistence and
applies its Skip Long Pauses setting to the captured audio. Printing returns
the raw transcript without pasting or running the Haiku rewrite. The service
then sends it to OpenCode and the clipboard.

The helper sends the verbatim transcript, foreground-window hint, local date,
clipboard metadata and follow-up answers. OpenCode Go runs `gpt-6-luna#max`
in an archived session with `hex/linear-agent.md` as its instructions. Luna
loads the current projects, summaries and descriptions through Linear MCP
before choosing a project. A workspace plugin presents project rows with their
names, statuses, teams, summaries, descriptions and URLs. Longer descriptions
can be read through `linear_get_project`. Luna queries issues, labels, workflow
states and other details as needed. Issue search results contain identifiers,
titles, projects, statuses, labels, priorities, estimates and dates. Luna reads
descriptions and relations for relevant issues, including older and cross-project
prerequisites. An open analysis or plan is a blocker when the new work depends
on its outcome.

Luna performs project and issue creation, edits, assignments and dependency
changes directly through MCP. Its instructions require preserving the dictated
context, supplying priority and estimate, using Backlog for new issues unless
requested otherwise, checking duplicates, verifying saved changes and correcting
recoverable errors. After an uncertain save, it checks the result before retrying.
These checks run in the agent. The Python helper runs the CLI and handles the
desktop interaction. Each private OpenCode invocation has a twenty-minute limit.

Luna calls `hex_notify` with a verified issue or project URL to queue a GNOME
notification. The Python service displays it with **Open in Linear** when the
run finishes, including any completed results from an interrupted run. Clicking
the notification itself also opens the link. The click handler stays active
until the notification is opened or dismissed, and forwards GNOME's activation
token when launching the browser. Browser-launch failures go to the service
journal. Luna uses
`hex_reply` to request missing details or a replacement image. The final response
is plain text. For image attachments, MCP prepares the signed upload request,
`hex_upload_clipboard` sends the captured bytes with those headers, and MCP
saves the image link in the description.

The service journal records the same dictation ID from recording through the
Linear save, alongside the OpenCode session ID and any created or updated
issue IDs. The `attempt=` entry points to a private directory under
`~/.local/state/hex/linear/runs/`, or the equivalent under `XDG_STATE_HOME`.
Each reply keeps the dictation ID and gets a new attempt directory.

`transcript.txt` holds the verbatim Hex transcript and is saved before the
Linear catalog is loaded. `prompt.txt` holds the exact bytes supplied on
OpenCode's stdin. `stdout.jsonl` records OpenCode's events, including Linear
lookups, project descriptions, write arguments and results, notification/reply
requests, and the final response. `stderr.txt` records CLI errors.
Both streams are written as OpenCode runs, preserving partial output on failure
or timeout. When a session ID is available, its archive also contains tool
results and any supplied clipboard image.

For a saved issue, search the journal by issue ID, then by dictation ID. For a
failed attempt, start with the recent journal entries and open its `attempt=` path.

```bash
journalctl --user -u hex-linear-issue.service -n 50
journalctl --user -u hex-linear-issue.service --grep 'KIAN-123'
journalctl --user -u hex-linear-issue.service --grep '<dictation-id>'
opencode api GET '/api/session/<session-id>/message'
```

To check the workflow with text, the dry run uses a separate OpenCode agent
whose permissions allow Linear reads and clarification requests. It prints
the proposed actions:

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
word within one message does not qualify. Only high-confidence names or
technical terms qualify; at most three are added, without phonetic aliases.
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
