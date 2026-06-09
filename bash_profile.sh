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

  if [[ -f "$claude_auth" && ! -L "$claude_auth" ]]; then
    echo "rotate-claude: $claude_auth is not a symlink" >&2
    echo "  Choose an unused suffix N, then move the current file and link $credentials_name to it:" >&2
    echo "  mv \"$claude_auth\" \"$claude_auth.N\"" >&2
    echo "  ln -s \"$credentials_name.N\" \"$claude_auth\"" >&2
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

  _rotate_claude_point_symlink "$claude_dir" "$next_suffix" || return 1

  echo "rotate-claude: selected .credentials.json.$next_suffix"
  echo "  Claude: $claude_auth -> $(readlink "$claude_auth")"
}

_rotate_claude_current_suffix() {
  local auth_path="$1"
  local target target_base

  [[ -L "$auth_path" ]] || return 0

  target="$(readlink "$auth_path")" || return 0
  target_base="${target##*/}"

  if [[ "$target_base" != .credentials.json.* ]]; then
    return 0
  fi

  printf '%s\n' "${target_base#.credentials.json.}"
}

_rotate_claude_point_symlink() {
  local auth_dir="$1"
  local suffix="$2"
  local tmp_link="$auth_dir/.rotate-claude-credentials.tmp.$$"

  rm -f "$tmp_link"
  ln -s ".credentials.json.$suffix" "$tmp_link" || return 1
  mv -Tf "$tmp_link" "$auth_dir/.credentials.json"
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
