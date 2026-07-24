# Pioneer (removed)

Clusterfork no longer integrates with the Pioneer OpenAI-compatible gateway
(`https://api.pioneer.ai/v1`). `PIONEER_API_KEY` is not read by any installed
agent config or shell module.

## What was removed

- **`shell/pioneer.sh`** — `pi` launched Qwen Code against Pioneer using model
  `zai-org/GLM-5.2`, exporting `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and
  `OPENAI_MODEL` inside a subshell.
- **`agents/qwen.json` Pioneer provider** — six OpenAI-compatible models on
  Pioneer (Pioneer Auto, Qwen 3.7 Max, DeepSeek V4 Pro, Kimi K2.7 Code, GLM 5.2,
  MiniMax M3) with `PIONEER_API_KEY` and reasoning effort `xhigh`.
- **`agents/opencode.json` Pioneer provider** — same model lineup for OpenCode.
- **`scripts/enumerate_pioneer.py`** — utility to list Pioneer models, pricing,
  and probed reasoning-effort tiers.

Qwen and OpenCode configs now ship MCP servers, privacy, and other non-provider
settings only. Model selection and auth are left to each tool's built-in flow.
