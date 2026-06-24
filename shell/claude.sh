export CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1
alias cl='claude --dangerously-skip-permissions --effort xhigh'

rotate-claude() {
  local claude_dir="${ROTATE_CLAUDE_DIR:-$HOME/.claude}"
  local credentials_name=".credentials.json"
  local claude_auth="$claude_dir/$credentials_name"
  local requested_suffix="${1:-}"
  local path suffix current_suffix next_suffix
  local -A suffix_lookup=()
  local -a suffixes=()

  if [[ $# -gt 1 ]]; then
    echo "rotate-claude: usage: rotate-claude [name]" >&2
    return 1
  fi

  if [[ ! -d "$claude_dir" ]]; then
    echo "rotate-claude: missing Claude auth directory: $claude_dir" >&2
    return 1
  fi

  for path in "$claude_dir"/.credentials.json.*; do
    [[ -e "$path" || -L "$path" ]] || continue
    suffix="${path##*/.credentials.json.}"
    suffixes+=("$suffix")
    suffix_lookup["$suffix"]=1
  done

  if [[ -n "$requested_suffix" ]]; then
    if [[ -z "${suffix_lookup[$requested_suffix]:-}" ]]; then
      echo "rotate-claude: no matching .credentials.json.$requested_suffix file" >&2
      if [[ ${#suffixes[@]} -gt 0 ]]; then
        printf '  suffixes: %s\n' "$(printf '%s\n' "${suffixes[@]}" | sort | paste -sd ' ' -)" >&2
      fi
      return 1
    fi
    next_suffix="$requested_suffix"
  elif [[ ${#suffixes[@]} -lt 2 ]]; then
    echo "rotate-claude: need at least two .credentials.json.* files" >&2
    if [[ ${#suffixes[@]} -gt 0 ]]; then
      printf '  suffixes: %s\n' "$(printf '%s\n' "${suffixes[@]}" | sort | paste -sd ' ' -)" >&2
    fi
    return 1
  fi

  mapfile -t suffixes < <(printf '%s\n' "${suffixes[@]}" | sort)

  if [[ -z "$requested_suffix" ]]; then
    next_suffix="${suffixes[0]}"
    current_suffix="$(_rotate_claude_current_suffix "$claude_auth")"

    if [[ -n "$current_suffix" ]]; then
      for ((i = 0; i < ${#suffixes[@]}; i++)); do
        if [[ "${suffixes[$i]}" == "$current_suffix" ]]; then
          next_suffix="${suffixes[$(((i + 1) % ${#suffixes[@]}))]}"
          break
        fi
      done
    fi
  fi

  _rotate_claude_install_credentials "$claude_dir" "$next_suffix" || return 1

  echo "rotate-claude: selected .credentials.json.$next_suffix"
  echo "  Claude: $claude_auth is now a copy of $credentials_name.$next_suffix"
}

_rotate_claude_current_suffix() {
  # Claude Code replaces .credentials.json with a new regular file at login and
  # on every OAuth token refresh, so the active account cannot be tracked with
  # a symlink. The active account is the suffixed file that holds the same
  # access token as .credentials.json.
  local auth_path="$1"
  local auth_dir token candidate candidate_token

  auth_dir="${auth_path%/*}"
  token="$(jq -r '.claudeAiOauth.accessToken // empty' "$auth_path" 2>/dev/null)"
  [[ -n "$token" ]] || return 0

  for candidate in "$auth_dir"/.credentials.json.*; do
    [[ -f "$candidate" ]] || continue
    candidate_token="$(jq -r '.claudeAiOauth.accessToken // empty' "$candidate" 2>/dev/null)"
    if [[ -n "$candidate_token" && "$candidate_token" == "$token" ]]; then
      printf '%s\n' "${candidate##*/.credentials.json.}"
      return 0
    fi
  done
}

_rotate_claude_install_credentials() {
  # Copy the selected account's file over .credentials.json instead of
  # symlinking it: Claude Code rewrites that path whenever it stores tokens,
  # so a symlink would not survive the next token refresh. The conky usage
  # fetcher copies rotated tokens back into the suffixed file, keyed by
  # account email, so the suffixed file stays usable.
  local auth_dir="$1"
  local suffix="$2"
  local source_path="$auth_dir/.credentials.json.$suffix"
  local tmp_copy="$auth_dir/.rotate-claude-credentials.tmp.$$"

  rm -f "$tmp_copy"
  cp -- "$source_path" "$tmp_copy" || return 1
  chmod 600 "$tmp_copy" || { rm -f "$tmp_copy"; return 1; }
  mv -Tf "$tmp_copy" "$auth_dir/.credentials.json"
}
