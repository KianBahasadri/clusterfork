#!/usr/bin/env bash

alias oc='ulimit -v 14206967 && opencode --continue'
alias cc='codex resume -c approvals_reviewer=auto_review'
alias cl='claude --dangerously-skip-permissions --effort max'
alias ag='agy --dangerously-skip-permissions --continue'

rotate-codex() {
  local codex_dir="${ROTATE_CODEX_CODEX_DIR:-$HOME/.codex}"
  local codex_auth="$codex_dir/auth.json"
  local requested_suffix="${1:-}"
  local path suffix current_suffix next_suffix
  local -A suffix_lookup=()
  local -a suffixes=()

  if [[ $# -gt 1 ]]; then
    echo "rotate-codex: usage: rotate-codex [name]" >&2
    return 1
  fi

  if [[ ! -d "$codex_dir" ]]; then
    echo "rotate-codex: missing Codex auth directory: $codex_dir" >&2
    return 1
  fi

  if [[ -f "$codex_auth" && ! -L "$codex_auth" ]]; then
    echo "rotate-codex: $codex_auth is not a symlink" >&2
    echo "  Choose an unused suffix N, then move the current file and link auth.json to it:" >&2
    echo "  mv \"$codex_auth\" \"$codex_auth.N\"" >&2
    echo "  ln -s auth.json.N \"$codex_auth\"" >&2
    return 1
  fi

  for path in "$codex_dir"/auth.json.*; do
    [[ -e "$path" || -L "$path" ]] || continue
    suffix="${path##*/auth.json.}"
    suffixes+=("$suffix")
    suffix_lookup["$suffix"]=1
  done

  if [[ -n "$requested_suffix" ]]; then
    if [[ -z "${suffix_lookup[$requested_suffix]:-}" ]]; then
      echo "rotate-codex: no matching auth.json.$requested_suffix file" >&2
      if [[ ${#suffixes[@]} -gt 0 ]]; then
        printf '  suffixes: %s\n' "$(printf '%s\n' "${suffixes[@]}" | sort | paste -sd ' ' -)" >&2
      fi
      return 1
    fi
    next_suffix="$requested_suffix"
  elif [[ ${#suffixes[@]} -lt 2 ]]; then
    echo "rotate-codex: need at least two auth.json.* files" >&2
    if [[ ${#suffixes[@]} -gt 0 ]]; then
      printf '  suffixes: %s\n' "$(printf '%s\n' "${suffixes[@]}" | sort | paste -sd ' ' -)" >&2
    fi
    return 1
  fi

  mapfile -t suffixes < <(printf '%s\n' "${suffixes[@]}" | sort)

  if [[ -z "$requested_suffix" ]]; then
    next_suffix="${suffixes[0]}"
    current_suffix="$(_rotate_codex_current_suffix "$codex_auth")"

    if [[ -n "$current_suffix" ]]; then
      for ((i = 0; i < ${#suffixes[@]}; i++)); do
        if [[ "${suffixes[$i]}" == "$current_suffix" ]]; then
          next_suffix="${suffixes[$(((i + 1) % ${#suffixes[@]}))]}"
          break
        fi
      done
    fi
  fi

  _rotate_codex_point_symlink "$codex_dir" "$next_suffix" || return 1

  echo "rotate-codex: selected auth.json.$next_suffix"
  echo "  Codex: $codex_auth -> $(readlink "$codex_auth")"
}

_rotate_codex_current_suffix() {
  local auth_path="$1"
  local target target_base

  [[ -L "$auth_path" ]] || return 0

  target="$(readlink "$auth_path")" || return 0
  target_base="${target##*/}"

  if [[ "$target_base" != auth.json.* ]]; then
    return 0
  fi

  printf '%s\n' "${target_base#auth.json.}"
}

_rotate_codex_point_symlink() {
  local auth_dir="$1"
  local suffix="$2"
  local tmp_link="$auth_dir/.auth.json.tmp.$$"

  rm -f "$tmp_link"
  ln -s "auth.json.$suffix" "$tmp_link" || return 1
  mv -Tf "$tmp_link" "$auth_dir/auth.json"
}

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

rotate-antigravity() {
  local restore_xtrace=""

  if [[ $- == *x* ]]; then
    restore_xtrace=1
    set +x
  fi

  _rotate_antigravity_main "$@"
  local status=$?

  if [[ -n "$restore_xtrace" ]]; then
    set -x
  fi

  return "$status"
}

_rotate_antigravity_main() {
  local state_dir="${ROTATE_ANTIGRAVITY_STATE_DIR:-$HOME/.gemini/antigravity-cli/rotate-auth}"
  local profiles_file="$state_dir/profiles"
  local current_file="$state_dir/current"
  local requested_suffix="${1:-}"
  local save_suffix="" current_suffix="" next_suffix="" profile
  local -A suffix_lookup=()
  local -a suffixes=()

  case "${1:-}" in
    -h|--help)
      _rotate_antigravity_usage
      return 0
      ;;
    --list)
      if [[ $# -ne 1 ]]; then
        _rotate_antigravity_usage >&2
        return 1
      fi
      _rotate_antigravity_list_profiles "$profiles_file" "$current_file"
      return
      ;;
    --save)
      if [[ $# -ne 2 ]]; then
        _rotate_antigravity_usage >&2
        return 1
      fi
      save_suffix="$2"
      _rotate_antigravity_validate_suffix "$save_suffix" || return 1
      _rotate_antigravity_require_secret_tool || return 1
      _rotate_antigravity_prepare_state "$state_dir" || return 1
      _rotate_antigravity_save_active_to_profile "$save_suffix" required || return 1
      _rotate_antigravity_add_profile "$profiles_file" "$save_suffix" || return 1
      _rotate_antigravity_write_current "$current_file" "$save_suffix" || return 1

      echo "rotate-antigravity: saved active keyring item as $save_suffix"
      echo "  Active: service=gemini username=antigravity"
      echo "  Profile: service=rotate-antigravity username=$save_suffix"
      return 0
      ;;
    --*)
      _rotate_antigravity_usage >&2
      return 1
      ;;
  esac

  if [[ $# -gt 1 ]]; then
    _rotate_antigravity_usage >&2
    return 1
  fi

  if [[ -n "$requested_suffix" ]]; then
    _rotate_antigravity_validate_suffix "$requested_suffix" || return 1
  fi

  _rotate_antigravity_require_secret_tool || return 1
  _rotate_antigravity_prepare_state "$state_dir" || return 1

  mapfile -t suffixes < <(_rotate_antigravity_profile_list "$profiles_file")
  for profile in "${suffixes[@]}"; do
    suffix_lookup["$profile"]=1
  done

  if [[ -n "$requested_suffix" ]]; then
    if [[ -z "${suffix_lookup[$requested_suffix]:-}" ]]; then
      echo "rotate-antigravity: no matching profile: $requested_suffix" >&2
      _rotate_antigravity_print_suffixes "  profiles" "${suffixes[@]}" >&2
      return 1
    fi
    next_suffix="$requested_suffix"
  elif [[ ${#suffixes[@]} -lt 2 ]]; then
    echo "rotate-antigravity: need at least two saved profiles" >&2
    _rotate_antigravity_print_suffixes "  profiles" "${suffixes[@]}" >&2
    echo "  Save the active account first: rotate-antigravity --save NAME" >&2
    return 1
  fi

  current_suffix="$(_rotate_antigravity_read_current "$current_file")"

  if [[ -z "$requested_suffix" ]]; then
    if [[ -z "$current_suffix" || -z "${suffix_lookup[$current_suffix]:-}" ]]; then
      echo "rotate-antigravity: no current profile marker" >&2
      echo "  Save the active account first: rotate-antigravity --save NAME" >&2
      return 1
    fi

    next_suffix="${suffixes[0]}"
    for ((i = 0; i < ${#suffixes[@]}; i++)); do
      if [[ "${suffixes[$i]}" == "$current_suffix" ]]; then
        next_suffix="${suffixes[$(((i + 1) % ${#suffixes[@]}))]}"
        break
      fi
    done
  elif [[ -n "$current_suffix" && -z "${suffix_lookup[$current_suffix]:-}" ]]; then
    echo "rotate-antigravity: warning: current profile marker is unknown; active item was not backed up before switching" >&2
    current_suffix=""
  fi

  if [[ -n "$current_suffix" ]]; then
    _rotate_antigravity_save_active_to_profile "$current_suffix" optional || return 1
  fi

  _rotate_antigravity_install_profile "$next_suffix" || return 1
  _rotate_antigravity_write_current "$current_file" "$next_suffix" || return 1

  echo "rotate-antigravity: selected profile $next_suffix"
  echo "  Active: service=gemini username=antigravity"
}

_rotate_antigravity_usage() {
  cat <<'EOF'
rotate-antigravity: usage:
  rotate-antigravity [name]
  rotate-antigravity --save name
  rotate-antigravity --list
EOF
}

_rotate_antigravity_require_secret_tool() {
  if ! command -v secret-tool >/dev/null 2>&1; then
    echo "rotate-antigravity: secret-tool is required" >&2
    return 1
  fi
}

_rotate_antigravity_prepare_state() {
  local state_dir="$1"
  local profiles_file="$state_dir/profiles"

  mkdir -p "$state_dir" || return 1
  chmod 700 "$state_dir" 2>/dev/null || true

  if [[ ! -e "$profiles_file" ]]; then
    : > "$profiles_file" || return 1
    chmod 600 "$profiles_file" 2>/dev/null || true
  fi
}

_rotate_antigravity_validate_suffix() {
  local suffix="$1"

  if [[ ! "$suffix" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "rotate-antigravity: profile names may only contain letters, numbers, dots, underscores, and dashes" >&2
    return 1
  fi
}

_rotate_antigravity_profile_list() {
  local profiles_file="$1"

  [[ -f "$profiles_file" ]] || return 0
  while IFS= read -r profile; do
    [[ -n "$profile" ]] || continue
    _rotate_antigravity_validate_suffix "$profile" >/dev/null 2>&1 || continue
    printf '%s\n' "$profile"
  done < "$profiles_file" | sort -u
}

_rotate_antigravity_list_profiles() {
  local profiles_file="$1"
  local current_file="$2"
  local current_suffix profile
  local -a suffixes=()

  mapfile -t suffixes < <(_rotate_antigravity_profile_list "$profiles_file")
  current_suffix="$(_rotate_antigravity_read_current "$current_file")"

  if [[ ${#suffixes[@]} -eq 0 ]]; then
    echo "rotate-antigravity: no saved profiles"
    return 0
  fi

  echo "rotate-antigravity: saved profiles"
  for profile in "${suffixes[@]}"; do
    if [[ "$profile" == "$current_suffix" ]]; then
      echo "  * $profile"
    else
      echo "    $profile"
    fi
  done
}

_rotate_antigravity_read_current() {
  local current_file="$1"
  local current_suffix=""

  if [[ -f "$current_file" ]]; then
    IFS= read -r current_suffix < "$current_file" || true
  fi

  _rotate_antigravity_validate_suffix "$current_suffix" >/dev/null 2>&1 || return 0
  printf '%s\n' "$current_suffix"
}

_rotate_antigravity_add_profile() {
  local profiles_file="$1"
  local suffix="$2"
  local tmp_file="${profiles_file}.tmp.$$"

  {
    [[ -f "$profiles_file" ]] && _rotate_antigravity_profile_list "$profiles_file"
    printf '%s\n' "$suffix"
  } | sort -u > "$tmp_file" || { rm -f "$tmp_file"; return 1; }

  chmod 600 "$tmp_file" 2>/dev/null || true
  mv -Tf "$tmp_file" "$profiles_file"
}

_rotate_antigravity_write_current() {
  local current_file="$1"
  local suffix="$2"
  local tmp_file="${current_file}.tmp.$$"

  printf '%s\n' "$suffix" > "$tmp_file" || return 1
  chmod 600 "$tmp_file" 2>/dev/null || true
  mv -Tf "$tmp_file" "$current_file"
}

_rotate_antigravity_print_suffixes() {
  local label="$1"
  shift

  if [[ $# -gt 0 ]]; then
    printf '%s: %s\n' "$label" "$(printf '%s\n' "$@" | sort -u | paste -sd ' ' -)"
  fi
}

_rotate_antigravity_lookup_active() {
  secret-tool lookup service gemini username antigravity
}

_rotate_antigravity_lookup_profile() {
  local suffix="$1"

  secret-tool lookup service rotate-antigravity username "$suffix"
}

_rotate_antigravity_store_active() {
  local secret="$1"

  printf '%s' "$secret" | secret-tool store --label="antigravity on gemini" service gemini username antigravity
}

_rotate_antigravity_store_profile() {
  local suffix="$1"
  local secret="$2"

  printf '%s' "$secret" | secret-tool store --label="antigravity on gemini ($suffix)" service rotate-antigravity username "$suffix"
}

_rotate_antigravity_save_active_to_profile() {
  local suffix="$1"
  local required="${2:-optional}"
  local secret

  secret="$(_rotate_antigravity_lookup_active)"
  if [[ $? -ne 0 ]]; then
    if [[ "$required" == "required" ]]; then
      echo "rotate-antigravity: missing active keyring item: service=gemini username=antigravity" >&2
      return 1
    fi

    echo "rotate-antigravity: warning: active keyring item was not backed up before switching" >&2
    return 0
  fi

  _rotate_antigravity_store_profile "$suffix" "$secret"
}

_rotate_antigravity_install_profile() {
  local suffix="$1"
  local secret

  secret="$(_rotate_antigravity_lookup_profile "$suffix")"
  if [[ $? -ne 0 ]]; then
    echo "rotate-antigravity: missing saved keyring item: service=rotate-antigravity username=$suffix" >&2
    return 1
  fi

  _rotate_antigravity_store_active "$secret"
}

rotate-cursor-cli() {
  local cursor_dir="${ROTATE_CURSOR_DIR:-$HOME/.config/cursor}"
  local cursor_auth="$cursor_dir/auth.json"
  local requested_suffix="${1:-}"
  local path suffix current_suffix next_suffix
  local -A suffix_lookup=()
  local -a suffixes=()

  if [[ $# -gt 1 ]]; then
    echo "rotate-cursor-cli: usage: rotate-cursor-cli [name]" >&2
    return 1
  fi

  if [[ ! -d "$cursor_dir" ]]; then
    echo "rotate-cursor-cli: missing Cursor auth directory: $cursor_dir" >&2
    return 1
  fi

  if [[ -f "$cursor_auth" && ! -L "$cursor_auth" ]]; then
    echo "rotate-cursor-cli: $cursor_auth is not a symlink" >&2
    echo "  Choose an unused suffix N, then move the current file and link auth.json to it:" >&2
    echo "  mv \"$cursor_auth\" \"$cursor_auth.N\"" >&2
    echo "  ln -s auth.json.N \"$cursor_auth\"" >&2
    return 1
  fi

  for path in "$cursor_dir"/auth.json.*; do
    [[ -e "$path" || -L "$path" ]] || continue
    suffix="${path##*/auth.json.}"
    suffixes+=("$suffix")
    suffix_lookup["$suffix"]=1
  done

  if [[ -n "$requested_suffix" ]]; then
    if [[ -z "${suffix_lookup[$requested_suffix]:-}" ]]; then
      echo "rotate-cursor-cli: no matching auth.json.$requested_suffix file" >&2
      if [[ ${#suffixes[@]} -gt 0 ]]; then
        printf '  suffixes: %s\n' "$(printf '%s\n' "${suffixes[@]}" | sort | paste -sd ' ' -)" >&2
      fi
      return 1
    fi
    next_suffix="$requested_suffix"
  elif [[ ${#suffixes[@]} -lt 2 ]]; then
    echo "rotate-cursor-cli: need at least two auth.json.* files" >&2
    if [[ ${#suffixes[@]} -gt 0 ]]; then
      printf '  suffixes: %s\n' "$(printf '%s\n' "${suffixes[@]}" | sort | paste -sd ' ' -)" >&2
    fi
    return 1
  fi

  mapfile -t suffixes < <(printf '%s\n' "${suffixes[@]}" | sort)

  if [[ -z "$requested_suffix" ]]; then
    next_suffix="${suffixes[0]}"
    current_suffix="$(_rotate_cursor_current_suffix "$cursor_auth")"

    if [[ -n "$current_suffix" ]]; then
      for ((i = 0; i < ${#suffixes[@]}; i++)); do
        if [[ "${suffixes[$i]}" == "$current_suffix" ]]; then
          next_suffix="${suffixes[$(((i + 1) % ${#suffixes[@]}))]}"
          break
        fi
      done
    fi
  fi

  _rotate_cursor_point_symlink "$cursor_dir" "$next_suffix" || return 1

  echo "rotate-cursor-cli: selected auth.json.$next_suffix"
  echo "  Cursor: $cursor_auth -> $(readlink "$cursor_auth")"
}

_rotate_cursor_current_suffix() {
  local auth_path="$1"
  local target target_base

  [[ -L "$auth_path" ]] || return 0

  target="$(readlink "$auth_path")" || return 0
  target_base="${target##*/}"

  if [[ "$target_base" != auth.json.* ]]; then
    return 0
  fi

  printf '%s\n' "${target_base#auth.json.}"
}

_rotate_cursor_point_symlink() {
  local auth_dir="$1"
  local suffix="$2"
  local tmp_link="$auth_dir/.auth.json.tmp.$$"

  rm -f "$tmp_link"
  ln -s "auth.json.$suffix" "$tmp_link" || return 1
  mv -Tf "$tmp_link" "$auth_dir/auth.json"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCODE_ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$OPENCODE_ENV_FILE" ]; then
  set -a
  . "$OPENCODE_ENV_FILE"
  set +a
fi
unset OPENCODE_ENV_FILE
unset SCRIPT_DIR

chrome() {
  nohup chromium --remote-debugging-port=9222 --user-data-dir="$HOME/.config/chromium" "$@" >/dev/null 2>&1 &
  disown
}
