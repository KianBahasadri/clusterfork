unalias gk 2>/dev/null || true
unalias grok 2>/dev/null || true

_cf_is_grok_headless() {
  # If stdin or stdout is not a terminal, this is non-interactive / headless.
  if ! [[ -t 0 ]] || ! [[ -t 1 ]]; then
    return 0
  fi

  # Explicit environment variables indicating non-interactive or headless execution,
  # or confirmation already completed in this invocation chain.
  if [[ -n "${GROK_HEADLESS:-}" || -n "${CI:-}" || -n "${NONINTERACTIVE:-}" || -n "${_CF_GROK_CONFIRMED:-}" ]]; then
    return 0
  fi

  local arg
  for arg in "$@"; do
    case "$arg" in
      # Grok headless flags: single-turn prompt, prompt file/json, or headless output formats
      -p|--single|-p=*|--single=*|--prompt-file|--prompt-file=*|--prompt-json|--prompt-json=*|--output-format|--output-format=*)
        return 0
        ;;
      # Grok agent command: runs Grok without interactive UI
      agent)
        return 0
        ;;
      # Informational / help / version queries
      -h|--help|-v|--version)
        return 0
        ;;
      # Utility subcommands that do not launch an interactive session
      clone|completions|dashboard|doctor|du|disk-usage|export|help|inspect|leader|login|logout|mcp|memory|models|plugin|sessions|setup|trace|update|version|worktree|wrap)
        return 0
        ;;
    esac
  done

  return 1
}

_cf_confirm_grok() {
  if _cf_is_grok_headless "$@"; then
    return 0
  fi

  local ans _drain
  read -r -n 1 -p "did you mean to launch grok? y/n " ans
  echo ""
  read -r -t 0.05 -n 10000 _drain 2>/dev/null || true
  case "$ans" in
    [yY])
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

grok() {
  _cf_confirm_grok "$@" || return 1
  local bin
  bin="$(type -P grok 2>/dev/null)" || true
  if [[ -z "$bin" ]]; then
    printf 'grok: command not found on PATH\n' >&2
    return 127
  fi
  _CF_GROK_CONFIRMED=1 "$bin" "$@"
}

gk() {
  local bin
  bin="$(type -P grok 2>/dev/null)" || true
  if [[ -z "$bin" ]]; then
    printf 'grok: command not found on PATH\n' >&2
    return 127
  fi
  _CF_GROK_CONFIRMED=1 _cf_tmux "$bin" "$@"
}

rotate-grok() {
  python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/rotate_auth.py" grok "$@"
}

