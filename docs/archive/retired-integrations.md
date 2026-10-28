# Retired gateway integrations

Neither integration below is installed or read by the current launchers.
This record retains the removal context; Git history holds the old code.

| Integration | Removed behavior |
| --- | --- |
| Aerolink | `shell/aerolink.sh` exposed `aero`, a Claude Code proxy launcher at `https://capi.aerolink.lat/`, using `AEROLINK_API_KEY` and inline settings |
| Pioneer | `shell/pioneer.sh` exposed `pi`, a Qwen launcher at `https://api.pioneer.ai/v1`, using `PIONEER_API_KEY` and `zai-org/GLM-5.2` |

Aerolink isolated its environment and cleared cached Anthropic OAuth variables
so the proxy key took precedence. Pioneer also shipped provider rosters in
Qwen and OpenCode and `scripts/enumerate_pioneer.py` for model/pricing/effort
inspection. Those rosters covered Pioneer Auto, Qwen, DeepSeek, Kimi, GLM,
and MiniMax; the provider config and enumeration script were removed together.

Qwen and OpenCode now leave provider/model selection to their native flows;
see [Agent Configs](../agent-configs.md).
