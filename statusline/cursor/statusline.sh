#!/usr/bin/env bash
# Cursor CLI statusLine: model · params/max · account · context · auto · api
# Layout mirrors ~/.claude/statusline-command.sh.

input=$(cat)

# Keep ~/.cursor/.usage-cache.json fresh (auto/api monthly usage).
printf '%s' "$input" | python3 "$HOME/.cursor/cursor-usage-fetch.py" >/dev/null 2>&1 &

mapfile -t F < <(
  echo "$input" | jq -r '
    .model.display_name // "?",
    .model.param_summary // "",
    (if .model.max_mode == true then "max" else "" end),
    .context_window.used_percentage // ""'
)
model=${F[0]}
param=${F[1]}
max_mode=${F[2]}
ctx_pct=${F[3]}

# Active Cursor account: suffix of the file ~/.config/cursor/auth.json resolves to
# (e.g. auth.json.ida -> ida), labeled like the conky overlay. Empty when there
# is no suffixed multi-account file; segment is skipped then.
CURSOR_AUTH="${CURSOR_HOME:-$HOME/.config/cursor}/auth.json"
acct=$(readlink -f "$CURSOR_AUTH" 2>/dev/null)
acct=${acct##*/}
acct=${acct#auth.json}
acct=${acct#.}

if [[ -z $acct ]]; then
  acct=$(jq -r 'first(.accounts[]? | select(.isSelected) | .label) // empty' \
    "$HOME/live-wallpaper/conky-linear-HUP/cache/cursor-usage.json" 2>/dev/null)
fi

USAGE_CACHE="$HOME/.cursor/.usage-cache.json"
auto_pct=""
api_pct=""
if [[ -r $USAGE_CACHE ]]; then
  mapfile -t U < <(jq -r '.auto.used_percentage // "", .api.used_percentage // ""' "$USAGE_CACHE" 2>/dev/null)
  auto_pct=${U[0]}
  api_pct=${U[1]}
fi

# 256-color helpers (same palette as Claude status line)
c() { printf '\e[38;5;%sm' "$1"; }
r=$'\e[0m'

usage_color() {
  local p=${1%%.*}
  [[ -z $p ]] && { printf '245'; return; }
  if   (( p < 50 )); then printf '114'
  elif (( p < 80 )); then printf '221'
  else                    printf '203'
  fi
}

usage_seg() {
  local label=$1 pct=$2
  if [[ -z $pct ]]; then
    printf '%s%s %s—%s' "$(c 245)" "$label" "$(c 240)" "$r"
  else
    local col; col=$(usage_color "$pct")
    printf '%s%s %s%.0f%%%s' "$(c 245)" "$label" "$(c "$col")" "$pct" "$r"
  fi
}

sep="$(c 240) · ${r}"

effort=$max_mode
if [[ -z $effort && -n $param ]]; then
  effort=${param#(}
  effort=${effort%)}
fi

out="$(c 117)${model}${r}"
[[ -n $effort ]] && out+="${sep}$(c 218)${effort}${r}"
[[ -n $acct ]] && out+="${sep}$(c 208)${acct}${r}"
out+="${sep}$(usage_seg ctx "$ctx_pct")"
out+="${sep}$(usage_seg auto "$auto_pct")"
out+="${sep}$(usage_seg api "$api_pct")"

printf '%s' "$out"
