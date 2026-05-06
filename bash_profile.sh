#!/usr/bin/env bash

alias o='ulimit -v 14206967 && opencode'
alias oc='o --continue'

rotate-codex() {
  local codex_dir="${ROTATE_CODEX_CODEX_DIR:-$HOME/.codex}"
  local opencode_dir="${ROTATE_CODEX_OPENCODE_DIR:-$HOME/.local/share/opencode}"
  local codex_auth="$codex_dir/auth.json"
  local opencode_auth="$opencode_dir/auth.json"
  local path suffix codex_suffix opencode_suffix current_suffix next_suffix
  local -A codex_suffixes=()
  local -a paired_suffixes=()

  if [[ ! -d "$codex_dir" ]]; then
    echo "rotate-codex: missing Codex auth directory: $codex_dir" >&2
    return 1
  fi

  if [[ ! -d "$opencode_dir" ]]; then
    echo "rotate-codex: missing OpenCode auth directory: $opencode_dir" >&2
    return 1
  fi

  if [[ -e "$codex_auth" && ! -L "$codex_auth" ]]; then
    echo "rotate-codex: $codex_auth is not a symlink" >&2
    echo "  Choose an unused suffix N, then move the current file and link auth.json to it:" >&2
    echo "  mv \"$codex_auth\" \"$codex_auth.N\"" >&2
    echo "  ln -s auth.json.N \"$codex_auth\"" >&2
    return 1
  fi

  if [[ -e "$opencode_auth" && ! -L "$opencode_auth" ]]; then
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
    fi
  done

  if [[ ${#paired_suffixes[@]} -lt 2 ]]; then
    echo "rotate-codex: need at least two matching auth.json.* pairs" >&2
    if [[ ${#paired_suffixes[@]} -gt 0 ]]; then
      printf '  matching suffixes: %s\n' "$(printf '%s\n' "${paired_suffixes[@]}" | sort | paste -sd ' ' -)" >&2
    fi
    return 1
  fi

  mapfile -t paired_suffixes < <(printf '%s\n' "${paired_suffixes[@]}" | sort)

  if [[ ! -e "$codex_auth" && ! -e "$opencode_auth" ]]; then
    next_suffix="${paired_suffixes[0]}"
    _rotate_codex_point_symlink "$codex_dir" "$next_suffix" || return 1
    _rotate_codex_point_symlink "$opencode_dir" "$next_suffix" || return 1

    echo "rotate-codex: selected auth.json.$next_suffix"
    echo "  Codex: $codex_auth -> $(readlink "$codex_auth")"
    echo "  OpenCode: $opencode_auth -> $(readlink "$opencode_auth")"
    return 0
  fi

  if [[ ! -L "$codex_auth" || ! -L "$opencode_auth" ]]; then
    echo "rotate-codex: active auth.json files must both exist as symlinks or both be missing" >&2
    return 1
  fi

  codex_suffix="$(_rotate_codex_current_suffix "$codex_auth")" || return 1
  opencode_suffix="$(_rotate_codex_current_suffix "$opencode_auth")" || return 1

  if [[ "$codex_suffix" != "$opencode_suffix" ]]; then
    echo "rotate-codex: active auth suffixes are not aligned" >&2
    echo "  Codex: $codex_suffix" >&2
    echo "  OpenCode: $opencode_suffix" >&2
    return 1
  fi

  current_suffix="$codex_suffix"
  next_suffix="${paired_suffixes[0]}"

  for ((i = 0; i < ${#paired_suffixes[@]}; i++)); do
    if [[ "${paired_suffixes[$i]}" == "$current_suffix" ]]; then
      next_suffix="${paired_suffixes[$(((i + 1) % ${#paired_suffixes[@]}))]}"
      break
    fi
  done

  _rotate_codex_point_symlink "$codex_dir" "$next_suffix" || return 1
  _rotate_codex_point_symlink "$opencode_dir" "$next_suffix" || return 1

  echo "rotate-codex: selected auth.json.$next_suffix"
  echo "  Codex: $codex_auth -> $(readlink "$codex_auth")"
  echo "  OpenCode: $opencode_auth -> $(readlink "$opencode_auth")"
}

_rotate_codex_current_suffix() {
  local auth_path="$1"
  local target target_base

  target="$(readlink "$auth_path")" || return 1
  target_base="${target##*/}"

  if [[ "$target_base" != auth.json.* ]]; then
    echo "rotate-codex: $auth_path points to '$target', not an auth.json.* variant" >&2
    return 1
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
