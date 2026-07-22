alias ca='cursor-agent --yolo'

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
