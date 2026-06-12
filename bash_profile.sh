#!/usr/bin/env bash

alias o='ulimit -v 14206967 && opencode'
alias oc='o --continue'
alias cc='codex resume'

rotate-codex() {
  local codex_dir="${ROTATE_CODEX_CODEX_DIR:-$HOME/.codex}"
  local opencode_dir="${ROTATE_CODEX_OPENCODE_DIR:-$HOME/.local/share/opencode}"
  local codex_auth="$codex_dir/auth.json"
  local opencode_auth="$opencode_dir/auth.json"
  local requested_suffix="${1:-}"
  local path suffix codex_suffix opencode_suffix current_suffix next_suffix
  local -A codex_suffixes=()
  local -A paired_suffix_lookup=()
  local -a paired_suffixes=()

  if [[ $# -gt 1 ]]; then
    echo "rotate-codex: usage: rotate-codex [name]" >&2
    return 1
  fi

  if [[ ! -d "$codex_dir" ]]; then
    echo "rotate-codex: missing Codex auth directory: $codex_dir" >&2
    return 1
  fi

  if [[ ! -d "$opencode_dir" ]]; then
    echo "rotate-codex: missing OpenCode auth directory: $opencode_dir" >&2
    return 1
  fi

  if [[ -f "$codex_auth" && ! -L "$codex_auth" ]]; then
    echo "rotate-codex: $codex_auth is not a symlink" >&2
    echo "  Choose an unused suffix N, then move the current file and link auth.json to it:" >&2
    echo "  mv \"$codex_auth\" \"$codex_auth.N\"" >&2
    echo "  ln -s auth.json.N \"$codex_auth\"" >&2
    return 1
  fi

  if [[ -f "$opencode_auth" && ! -L "$opencode_auth" ]]; then
    echo "rotate-codex: $opencode_auth is not a symlink" >&2
    echo "  Choose an unused suffix N, then move the current file and link auth.json to it:" >&2
    echo "  mv \"$opencode_auth\" \"$opencode_auth.N\"" >&2
    echo "  ln -s auth.json.N \"$opencode_auth\"" >&2
    return 1
  fi

  for path in "$codex_dir"/auth.json.*; do
    [[ -e "$path" || -L "$path" ]] || continue
    suffix="${path##*/auth.json.}"
    codex_suffixes["$suffix"]=1
  done

  for path in "$opencode_dir"/auth.json.*; do
    [[ -e "$path" || -L "$path" ]] || continue
    suffix="${path##*/auth.json.}"
    if [[ -n "${codex_suffixes[$suffix]:-}" ]]; then
      paired_suffixes+=("$suffix")
      paired_suffix_lookup["$suffix"]=1
    fi
  done

  if [[ -n "$requested_suffix" ]]; then
    if [[ -z "${paired_suffix_lookup[$requested_suffix]:-}" ]]; then
      echo "rotate-codex: no matching auth.json.$requested_suffix pair" >&2
      if [[ ${#paired_suffixes[@]} -gt 0 ]]; then
        printf '  matching suffixes: %s\n' "$(printf '%s\n' "${paired_suffixes[@]}" | sort | paste -sd ' ' -)" >&2
      fi
      return 1
    fi
    next_suffix="$requested_suffix"
  elif [[ ${#paired_suffixes[@]} -lt 2 ]]; then
    echo "rotate-codex: need at least two matching auth.json.* pairs" >&2
    if [[ ${#paired_suffixes[@]} -gt 0 ]]; then
      printf '  matching suffixes: %s\n' "$(printf '%s\n' "${paired_suffixes[@]}" | sort | paste -sd ' ' -)" >&2
    fi
    return 1
  fi

  mapfile -t paired_suffixes < <(printf '%s\n' "${paired_suffixes[@]}" | sort)

  if [[ -z "$requested_suffix" ]]; then
    next_suffix="${paired_suffixes[0]}"

    codex_suffix="$(_rotate_codex_current_suffix "$codex_auth")"
    opencode_suffix="$(_rotate_codex_current_suffix "$opencode_auth")"

    if [[ -n "$codex_suffix" ]]; then
      current_suffix="$codex_suffix"
    else
      current_suffix="$opencode_suffix"
    fi

    if [[ -n "$current_suffix" ]]; then
      for ((i = 0; i < ${#paired_suffixes[@]}; i++)); do
        if [[ "${paired_suffixes[$i]}" == "$current_suffix" ]]; then
          next_suffix="${paired_suffixes[$(((i + 1) % ${#paired_suffixes[@]}))]}"
          break
        fi
      done
    fi
  fi

  _rotate_codex_point_symlink "$codex_dir" "$next_suffix" || return 1
  _rotate_codex_point_symlink "$opencode_dir" "$next_suffix" || return 1

  echo "rotate-codex: selected auth.json.$next_suffix"
  echo "  Codex: $codex_auth -> $(readlink "$codex_auth")"
  echo "  OpenCode: $opencode_auth -> $(readlink "$opencode_auth")"
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
