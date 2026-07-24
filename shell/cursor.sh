alias ca='cursor-agent --yolo'

rotate-cursor-cli() {
  local cursor_dir="${ROTATE_CURSOR_DIR:-$HOME/.config/cursor}"
  local auth_store_dir="${ROTATE_CURSOR_AUTH_STORE_DIR:-$HOME/.local/share/clusterfork-auth/cursor}"
  local cursor_auth="$cursor_dir/auth.json"
  local current_link="$auth_store_dir/current"
  local requested_suffix="${1:-}"
  local path suffix current_suffix next_suffix auth_target resolved_auth_target
  local -A suffix_lookup=()
  local -a suffixes=()

  case "${1:-}" in
    --list)
      if [[ $# -ne 1 ]]; then
        echo "rotate-cursor-cli: usage: rotate-cursor-cli [--list | [name]]" >&2
        return 1
      fi
      _rotate_cursor_list_profiles "$auth_store_dir" "$current_link"
      return
      ;;
    --*)
      echo "rotate-cursor-cli: usage: rotate-cursor-cli [--list | [name]]" >&2
      return 1
      ;;
  esac

  if [[ $# -gt 1 ]]; then
    echo "rotate-cursor-cli: usage: rotate-cursor-cli [--list | [name]]" >&2
    return 1
  fi

  if [[ -e "$cursor_auth" && ! -L "$cursor_auth" ]]; then
    echo "rotate-cursor-cli: $cursor_auth exists but is not a symlink" >&2
    echo "  Choose an unused suffix NAME, then run:" >&2
    printf '  mkdir -p "%s"\n' "$auth_store_dir" >&2
    printf '  chmod 700 "%s" "%s"\n' "${auth_store_dir%/*}" "$auth_store_dir" >&2
    printf '  mv "%s" "%s/auth.json.NAME"\n' "$cursor_auth" "$auth_store_dir" >&2
    printf '  chmod 600 "%s/auth.json.NAME"\n' "$auth_store_dir" >&2
    printf '  ln -sfn "auth.json.NAME" "%s/current"\n' "$auth_store_dir" >&2
    printf '  ln -s "%s/current" "%s"\n' "$auth_store_dir" "$cursor_auth" >&2
    return 1
  fi

  if [[ ! -L "$cursor_auth" ]]; then
    echo "rotate-cursor-cli: missing shared auth link: $cursor_auth" >&2
    echo "  Run install-clusterfork.sh to configure it." >&2
    return 1
  fi

  if [[ ! -d "$auth_store_dir" ]]; then
    echo "rotate-cursor-cli: missing shared auth directory: $auth_store_dir" >&2
    echo "  Run install-clusterfork.sh to migrate existing profiles." >&2
    return 1
  fi

  auth_target="$(readlink "$cursor_auth")" || return 1
  if [[ "$auth_target" == /* ]]; then
    resolved_auth_target="$auth_target"
  else
    resolved_auth_target="$cursor_dir/$auth_target"
  fi
  if [[ ! -L "$current_link" ||
        "$(realpath -ms "$resolved_auth_target")" != "$(realpath -ms "$current_link")" ]]; then
    echo "rotate-cursor-cli: $cursor_auth does not link directly to $current_link" >&2
    echo "  Run install-clusterfork.sh to repair it." >&2
    return 1
  fi

  for path in "$auth_store_dir"/auth.json.*; do
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
    current_suffix="$(_rotate_cursor_current_suffix "$current_link")"

    if [[ -n "$current_suffix" ]]; then
      for ((i = 0; i < ${#suffixes[@]}; i++)); do
        if [[ "${suffixes[$i]}" == "$current_suffix" ]]; then
          next_suffix="${suffixes[$(((i + 1) % ${#suffixes[@]}))]}"
          break
        fi
      done
    fi
  fi

  _rotate_cursor_point_symlink "$auth_store_dir" "$next_suffix" || return 1

  echo "rotate-cursor-cli: selected auth.json.$next_suffix"
  echo "  Cursor: $cursor_auth -> $(readlink "$cursor_auth") -> $(readlink "$current_link")"
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
  local auth_store_dir="$1"
  local suffix="$2"
  local tmp_link="$auth_store_dir/.current.tmp.$$"

  rm -f "$tmp_link"
  ln -s "auth.json.$suffix" "$tmp_link" || return 1
  mv -Tf "$tmp_link" "$auth_store_dir/current"
}

_rotate_cursor_list_profiles() {
  local auth_store_dir="$1"
  local current_link="$2"
  local current_suffix profile
  local -a suffixes=()

  for path in "$auth_store_dir"/auth.json.*; do
    [[ -e "$path" || -L "$path" ]] || continue
    suffix="${path##*/auth.json.}"
    suffixes+=("$suffix")
  done

  if [[ ${#suffixes[@]} -eq 0 ]]; then
    echo "rotate-cursor-cli: no saved profiles"
    return 0
  fi

  mapfile -t suffixes < <(printf '%s\n' "${suffixes[@]}" | sort)
  if [[ -L "$current_link" ]]; then
    current_suffix="$(_rotate_cursor_current_suffix "$current_link")"
  fi

  echo "rotate-cursor-cli: saved profiles"
  for profile in "${suffixes[@]}"; do
    if [[ "$profile" == "$current_suffix" ]]; then
      echo "  * $profile"
    else
      echo "    $profile"
    fi
  done
}
