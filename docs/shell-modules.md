# Shell Modules

`bash_profile.sh` sources every `shell/*.sh` on shell startup. Each module defines launch wrappers, aliases, or credential rotation functions for one agent.

## claude.sh

`cl` is an alias for `claude --dangerously-skip-permissions --effort xhigh`. `rotate-claude` switches between multiple saved Claude account credentials.

## codex.sh

`cc` is an alias for `codex resume -c approval_policy=never`. `rotate-codex` switches between saved Codex accounts via symlinks.

## cursor.sh

`ca` is an alias for `cursor-agent --yolo` (Run Everything / force-allow). `rotate-cursor-cli` switches between saved Cursor accounts via symlinks.

## opencode.sh

`oc` is an alias for `opencode --continue`. `o` is an alias for `opencode`.

## antigravity.sh

`ag` is an alias for `agy --dangerously-skip-permissions`. `rotate-antigravity` switches between saved Antigravity accounts using `secret-tool` (GNOME Keyring).

## chrome.sh

`chrome` launches Chromium with remote debugging on port 9222 for use with browser-automation MCP servers.
