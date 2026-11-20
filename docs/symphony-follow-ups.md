# Symphony follow-ups

MVP scope for KIAN-1077.

- Run ntfy on Guaranine. Wire a private publisher route into Symphony and send
  one alert per parked issue. Blockers are visible in the Web UI in the MVP.
- Add the remaining native composer commands and skill autocomplete, including
  thread switching and forking, review, MCP inspection, and explicit shell use.
- Add the Claude Code coordinator once there is a need to switch providers.
- Enforce provider hostname egress at the network boundary. Guaranine's
  standard NetworkPolicy filters by address and port. The MVP permits cluster
  DNS and public TCP 443.
- Add the Grafana view, Mermaid rendering, and shared cross-repository rules
  tracked in KIAN-1079, KIAN-1078, and KIAN-1080.
