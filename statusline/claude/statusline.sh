#!/usr/bin/env bash
# Claude Code statusLine: model · reasoning effort · account · context · 5h usage · weekly usage
# Fields come from the JSON Claude Code pipes in on stdin (v2.1.x schema).

input=$(cat)

# Keep ~/.claude/.usage-cache.json fresh so 5h/weekly still show under fast
# mode (which strips rate_limits from this payload). Harvests rate_limits for
# free when present; otherwise polls the API itself. Non-blocking.
USAGE_CACHE="$HOME/.claude/.usage-cache.json"
printf '%s' "$input" | python3 "$HOME/.claude/claude-usage-fetch.py" >/dev/null 2>&1 &

# One jq pass, one value per line so empty fields are preserved by mapfile.
mapfile -t F < <(
  echo "$input" | jq -r '
    .model.display_name // "?",
    .effort.level // "",
    .context_window.used_percentage // "",
    .rate_limits.five_hour.used_percentage // "",
    .rate_limits.seven_day.used_percentage // ""'
)
model=${F[0]}
effort=${F[1]}
ctx_pct=${F[2]}
h5_pct=${F[3]}
wk_pct=${F[4]}

# Fast mode omits rate_limits from the payload above, so fall back to the
# self-maintained cache for whichever usage field is missing.
if [[ -z $h5_pct || -z $wk_pct ]] && [[ -r $USAGE_CACHE ]]; then
  mapfile -t C < <(
    jq -r '.five_hour.used_percentage // "", .seven_day.used_percentage // ""' \
      "$USAGE_CACHE" 2>/dev/null
  )
  [[ -z $h5_pct ]] && h5_pct=${C[0]}
  [[ -z $wk_pct ]] && wk_pct=${C[1]}
fi

# --- Active Claude account (e.g. kian / sepehr) ---------------------------
# Show which stored account Claude Code is currently logged in as. This
# replicates conky-linear-HUP's account detection directly
# (scripts/fetch_claude_usage.py -> is_selected_credentials) instead of reading
# that project's cache file as a fallback, so the segment no longer breaks when
# conky has not run or its directory moves.
#
# Why the label can't come from the filename: Claude Code keeps the live login
# in ~/.claude/.credentials.json and rewrites it as a plain file on every login
# and OAuth token refresh. Any symlink placed there is destroyed by that
# rewrite, and the plain file's name carries no account label. (This is what
# silently removed the segment before: a token refresh replaced our symlink with
# a regular file.)
#
# How the label is recovered: each account is stored as a copy named
# .credentials.json.<label> (e.g. .credentials.json.kian). The selected account
# is the copy whose OAuth access token equals the live file's token -- the exact
# match conky uses. conky's fetcher keeps those copies' tokens in sync with the
# live file, so the matching copy's <label> suffix is the current account name.
# This also subsumes the old symlink scheme: a symlinked .credentials.json reads
# back the same token as its target copy, so it still matches. Empty (segment
# skipped) when no copy matches, e.g. briefly after a refresh before conky
# re-syncs the copies.
acct=""
live_token=$(jq -r '.claudeAiOauth.accessToken // empty' \
  "$HOME/.claude/.credentials.json" 2>/dev/null)
if [[ -n $live_token ]]; then
  for cred in "$HOME/.claude"/.credentials.json.*; do
    [[ -e $cred ]] || continue          # unmatched glob stays literal; skip it
    tok=$(jq -r '.claudeAiOauth.accessToken // empty' "$cred" 2>/dev/null)
    if [[ -n $tok && $tok == "$live_token" ]]; then
      acct=${cred##*/}                  # drop dir  -> .credentials.json.<label>
      acct=${acct#.credentials.json.}   # drop head -> <label>
      break
    fi
  done
fi

# 256-color helpers
c() { printf '\e[38;5;%sm' "$1"; }   # set fg color
r=$'\e[0m'                            # reset

# Pick a color for a usage percentage: green < 50, yellow < 80, else red.
usage_color() {
  local p=${1%%.*}                    # integer part only
  [[ -z $p ]] && { printf '245'; return; }
  if   (( p < 50 )); then printf '114'
  elif (( p < 80 )); then printf '221'
  else                    printf '203'
  fi
}

# Render "<label> <int%>" with a severity color, or a dim dash when absent.
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

out="$(c 117)${model}${r}"
[[ -n $effort ]] && out+="${sep}$(c 218)${effort}${r}"
[[ -n $acct ]] && out+="${sep}$(c 208)${acct}${r}"
out+="${sep}$(usage_seg ctx "$ctx_pct")"
out+="${sep}$(usage_seg 5h "$h5_pct")"
out+="${sep}$(usage_seg wk "$wk_pct")"

printf '%s' "$out"
