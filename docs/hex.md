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

## Editing behavior

`hex/prompt.md` asks for concise, direct messages in the speaker's voice:
remove filler, repetition, unnecessary lead-ins, and abandoned false starts;
combine overlapping statements; keep a final explicit correction instead of
its superseded wording. Preserve distinct requests, constraints, meaningful
uncertainty, quotations, identifiers, and intended numbers. Preserve unresolved
contradictions instead of guessing. `@HOME@` is expanded during installation
in the personal Guaranine repository reference.

`hex/rewrite.json` selects Claude Haiku 5.5 on OpenCode Go, variant `none`, with
a 30-second deadline. The helper reads these values from the saved Global
profile for each dictation. Built-in post-processing stays disabled because
the `conversation-context` transformation performs the rewrite once.

The helper includes up to 2000 whitespace-separated words from earlier
nonempty dictations retained in Hex History. There is no age or message-count
cutoff. Newest entries take priority; the oldest included entry can be trimmed
to its final words. It uses `final_text`, after the previous rewrite, and sends
entries oldest first as untrusted spelling/reference context. A known
foreground app restricts history to that app; an unknown app permits context
from multiple apps. Only the current dictation is rewritten.

Requests go through the already running OpenCode service on loopback. The
provider receives the current dictation, selected history excerpt, shared
vocabulary, prompt, and available foreground context. A rewrite error or
deadline preserves the original input. The template uses an argument array
and stdin for dictated text, with no shell interpolation.

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
has delivered the text. It uses the selected rewrite model independently of
the paste process. A new name must appear in both the current original and
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
