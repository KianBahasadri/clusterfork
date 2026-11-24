# Symphony follow-ups

Deferred work beyond the current Symphony pipeline:

- Run ntfy on Guaranine. Wire a private publisher route into Symphony and send
  one alert per parked task (`symphony/parked-<id>`).
- Enforce provider hostname egress at the network boundary. Guaranine's
  standard NetworkPolicy filters by address and port, currently permitting
  cluster DNS and public TCP 443.
- Add the Grafana dashboard view tracked in KIAN-1079.
