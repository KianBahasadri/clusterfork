"""Run two pinned offline keystroke detectors against fixed BioCadence plans.

Supply checkouts of RoloBits/isHumanCadence and JacobRHess/hidsentry at the
revisions below, in that order. Requires Node with TypeScript stripping.
"""

import json
from pathlib import Path
from random import Random
import subprocess
import sys

from test_hex_typing import BENCHMARK_TEXT, biocadence, fixture_model, rhythm


CADENCE = "18e71dab25039569e3b2a201837e3bb52a85830c"  # tag v1.6.0
HIDSENTRY = "e845213e0d6769fc99276b0edf505c034831c847"  # 0.1.0


def pinned_path(directory, revision):
    path = Path(directory).resolve()
    head = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    if head != revision:
        raise ValueError(f"{path} must be checked out at {revision}")
    return path


def cadence_scores(cadence_path, traces):
    script = """
import {readFileSync} from 'node:fs';
import {registerHooks} from 'node:module';
import {pathToFileURL} from 'node:url';
const source = pathToFileURL(process.argv[1] + '/src/').href;
registerHooks({
  resolve(specifier, context, next) {
    if (context.parentURL?.startsWith(source) && /^\\.\\/[a-z-]+$/.test(specifier))
      return next(specifier + '.ts', context);
    return next(specifier, context);
  },
});
const {createCadence} = await import(source + 'index.ts');
let time;
Object.defineProperty(globalThis, 'performance', {value: {now: () => time}});
const results = {};
for (const trace of JSON.parse(readFileSync(0, 'utf8'))) {
  const target = new EventTarget();
  const detector = createCadence(target, {scheduling: 'manual'});
  detector.start();
  for (const item of trace.events) {
    time = item.t + 100;
    const event = new Event(item.action === 'down' ? 'keydown' : 'keyup');
    Object.defineProperty(event, 'key', {value: item.key === 'Key.backspace' ? 'Backspace' : item.key});
    target.dispatchEvent(event);
  }
  const result = detector.analyze();
  detector.destroy();
  results[trace.name] = {label: result.classification, score: result.score,
                         synthetic_events: result.signals.syntheticEvents};
}
console.log(JSON.stringify(results));
"""
    return json.loads(subprocess.check_output(
        ["node", "--input-type=module", "-e", script, str(cadence_path)],
        input=json.dumps(traces), text=True,
    ))


def main(cadence_path, hidsentry_path):
    cadence_path = pinned_path(cadence_path, CADENCE)
    sys.path.insert(0, str(pinned_path(hidsentry_path, HIDSENTRY) / "src"))
    from hidsentry.score import score_trace
    from hidsentry.trace import Trace

    traces, results = [], {}
    for name in ("thoughts", "copy"):
        events = biocadence.plan(BENCHMARK_TEXT, fixture_model(name), Random(511))
        bursts, gaps = rhythm(events)
        trace = {
            "name": name, "origin": "synthetic", "label": "injection",
            "description": "Planned Hex keystrokes from a synthetic training session",
            "events": [
                {"t": event.time * 1000, "key": event.key, "action": "down" if event.pressed else "up"}
                for event in events
            ],
        }
        traces.append(trace)
        verdict = score_trace(Trace.from_dict(trace))
        results[name] = {
            "presses": sum(event.pressed for event in events),
            "corrections": sum(event.pressed and event.key == biocadence.BACKSPACE for event in events),
            "bursts": bursts,
            "median_gaps_ms": {context: round(gap * 1000, 1) for context, gap in gaps.items()},
            "hidsentry": {"label": verdict.label, "score": verdict.score},
        }
    for name, score in cadence_scores(cadence_path, traces).items():
        results[name]["is-human-cadence"] = score
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main(*sys.argv[1:])
