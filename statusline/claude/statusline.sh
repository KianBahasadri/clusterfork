#!/usr/bin/env bash
# Claude Code statusLine: model · reasoning effort · account · context · usage
# Fields come from the JSON Claude Code pipes in on stdin (v2.1.x schema).
#
# Two modes. Normally this renders Anthropic state: the logged-in Claude
# account and that account's 5h/weekly quota. Under `occ`
# (shell/opencode-claude.sh) Claude Code is pointed at the OpenCode Go gateway
# instead, and all three of those segments would be wrong -- the Claude account
# is still logged in but unused, and rate_limits describe Anthropic's quota,
# not OpenCode's. Claude Code spawns this script as a child process, so it
# inherits the launcher's environment and can detect the mode from
# ANTHROPIC_BASE_URL.

input=$(cat)

occ=0
[[ ${ANTHROPIC_BASE_URL:-} == *opencode.ai* ]] && occ=1

# OpenCode Go publishes no usage over the API -- there is no /usage endpoint and
# /v1/messages returns no rate-limit headers -- so the only source is the
# authenticated web dashboard. conky-linear-HUP already scrapes it (Firefox auth
# cookie -> the 5h/weekly/monthly cards) and is the system of record, so read its
# cache instead of duplicating a fragile HTML parser here.
OCC_USAGE_CACHE="${OCC_USAGE_CACHE:-$HOME/conky-linear-HUP/cache/opencode-usage.json}"
OCC_USAGE_FETCHER="${OCC_USAGE_FETCHER:-$HOME/conky-linear-HUP/scripts/fetch_opencode_usage.py}"
OCC_USAGE_TTL="${OCC_USAGE_TTL:-300}"

USAGE_CACHE="$HOME/.claude/.usage-cache.json"

if (( occ )); then
  # conky repolls on its own (60-300s adaptive), so this normally does nothing.
  # Only when its cache has aged out -- conky stopped, or was never running --
  # do we drive its fetcher ourselves. Non-blocking; this render uses whatever
  # the cache already holds.
  if [[ -r $OCC_USAGE_FETCHER ]] && command -v python3 >/dev/null; then
    occ_mtime=$(stat -c %Y "$OCC_USAGE_CACHE" 2>/dev/null || echo 0)
    if (( $(date +%s) - occ_mtime > OCC_USAGE_TTL )); then
      python3 "$OCC_USAGE_FETCHER" >/dev/null 2>&1 &
    fi
  fi
else
  # Keep ~/.claude/.usage-cache.json fresh so 5h/weekly still show under fast
  # mode (which strips rate_limits from this payload). Harvests rate_limits for
  # free when present; otherwise polls the API itself. Non-blocking.
  printf '%s' "$input" | python3 "$HOME/.claude/claude-usage-fetch.py" >/dev/null 2>&1 &
fi

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
mo_pct=""
stale=""

if (( occ )); then
  # The dashboard's three windows are $12 / $30 / $60 spend caps. Anthropic's
  # own rate_limits are discarded: they describe an account this session isn't
  # billing against.
  mapfile -t O < <(
    jq -r '
      .accounts[0] as $a
      | (($a.windows // []) | map({(.label): .usedPercent}) | add) as $w
      | (if .ok == true then "ok" else "err" end),
        (if $a.staleCache == true then "~" else "" end),
        ($w["5h"] // ""), ($w["weekly"] // ""), ($w["monthly"] // "")' \
      "$OCC_USAGE_CACHE" 2>/dev/null
  )
  if [[ ${O[0]:-err} != "ok" ]]; then
    h5_pct="" wk_pct="" mo_pct=""      # no cache, or the last fetch failed
  else
    stale=${O[1]}
    h5_pct=${O[2]}
    wk_pct=${O[3]}
    mo_pct=${O[4]}
  fi
elif [[ -z $h5_pct || -z $wk_pct ]] && [[ -r $USAGE_CACHE ]]; then
  # Fast mode omits rate_limits from the payload above, so fall back to the
  # self-maintained cache for whichever usage field is missing.
  mapfile -t C < <(
    jq -r '.five_hour.used_percentage // "", .seven_day.used_percentage // ""' \
      "$USAGE_CACHE" 2>/dev/null
  )
  [[ -z $h5_pct ]] && h5_pct=${C[0]}
  [[ -z $wk_pct ]] && wk_pct=${C[1]}
fi

# --- Active account -------------------------------------------------------
acct=""
if (( occ )); then
  # Which profile rotate-opencode has selected. Unlike Claude's credentials
  # file this one is a stable symlink that nothing rewrites, so the label is
  # just the suffix it points at.
  occ_store="${ROTATE_OPENCODE_AUTH_STORE_DIR:-$HOME/.local/share/clusterfork-auth/opencode}"
  occ_link=$(readlink "$occ_store/current" 2>/dev/null)
  occ_link=${occ_link##*/}
  [[ $occ_link == auth.json.* ]] && acct=${occ_link#auth.json.}
else
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
# mark prefixes the number ("~" for a figure served from a stale cache).
usage_seg() {
  local label=$1 pct=$2 mark=${3:-}
  if [[ -z $pct ]]; then
    printf '%s%s %s—%s' "$(c 245)" "$label" "$(c 240)" "$r"
  else
    local col; col=$(usage_color "$pct")
    printf '%s%s %s%s%.0f%%%s' "$(c 245)" "$label" "$(c "$col")" "$mark" "$pct" "$r"
  fi
}

sep="$(c 240) · ${r}"

out="$(c 117)${model}${r}"
(( occ )) && out+="${sep}$(c 214)go${r}"
[[ -n $effort ]] && out+="${sep}$(c 218)${effort}${r}"
[[ -n $acct ]] && out+="${sep}$(c 208)${acct}${r}"
out+="${sep}$(usage_seg ctx "$ctx_pct")"
out+="${sep}$(usage_seg 5h "$h5_pct" "$stale")"
out+="${sep}$(usage_seg wk "$wk_pct" "$stale")"
(( occ )) && out+="${sep}$(usage_seg mo "$mo_pct" "$stale")"

printf '%s' "$out"
