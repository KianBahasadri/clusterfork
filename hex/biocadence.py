"""BioCadence v0.1.0 typing features, model and planner adapted for Hex.

Copyright (c) 2026 Luc Duguay. See BIOCADENCE-LICENSE.
Source: https://github.com/lucduguaysita/biocadence/tree/8661059179324315f34765969c3d30f54cc0aa55
"""

from collections import defaultdict
from dataclasses import dataclass
from math import exp, log
from random import Random
from statistics import fmean, median, pstdev


BACKSPACE = "Key.backspace"
SEPARATOR = "\x1f"
MIN_SAMPLES = 4
CONTEXTS = ("mid", "word", "clause", "sentence")
MAX_FLIGHT = 12.0
SENTENCE_ENDERS = ".!?\n"
CLAUSE_ENDERS = ",;:"
SPECIAL = {" ": "space", "\n": "enter", "\t": "tab", BACKSPACE: "backspace"}
NEIGHBORS = {
    "q": "wa", "w": "qeas", "e": "wrsd", "r": "etdf", "t": "rygf",
    "y": "tuhg", "u": "yijh", "i": "uokj", "o": "iplk", "p": "ol",
    "a": "qwsz", "s": "awedxz", "d": "serfcx", "f": "drtgvc", "g": "ftyhbv",
    "h": "gyujnb", "j": "huikmn", "k": "jiolm", "l": "kop", "z": "asx",
    "x": "zsdc", "c": "xdfv", "v": "cfgb", "b": "vghn", "n": "bhjm", "m": "njk",
}


def boundary(history):
    if not history:
        return "start"
    last = history[-1]
    previous = history[-2] if last == " " and len(history) > 1 else last
    if previous in SENTENCE_ENDERS:
        return "sentence"
    if previous in CLAUSE_ENDERS:
        return "clause"
    return "word" if last == " " else "mid"


def key_class(key):
    if key is None:
        return "^"
    if key in SPECIAL:
        return SPECIAL[key]
    if key.isalpha():
        return "letter"
    if key.isdigit():
        return "digit"
    return "punct"


def distribution(values, default=None):
    logs = [log(value) for value in values]
    if not logs:
        return default
    sigma = pstdev(logs) if len(logs) > 1 else 0.20
    return [fmean(logs), max(sigma, 0.001), len(logs)]


def timing_buckets(observations, default):
    exact, classes, all_values = defaultdict(list), defaultdict(list), []
    for keys, value in observations:
        exact[SEPARATOR.join(key or "^" for key in keys)].append(value)
        classes[SEPARATOR.join(map(key_class, keys))].append(value)
        all_values.append(value)
    # Keep fitted statistics only. Rare keys and pairs back off to their class.
    return {
        "exact": {key: distribution(values) for key, values in exact.items() if len(values) >= MIN_SAMPLES},
        "classes": {key: distribution(values) for key, values in classes.items() if len(values) >= MIN_SAMPLES},
        "global": distribution(all_values, default),
    }


def fit(strokes):
    """Fit dwell, press-to-press flight, context pauses and correction runs."""
    dwell, flights, runs, history = [], [], [], []
    last_press, backspaces, characters = None, 0, 0
    for key, press, release in strokes:
        if release > press:
            dwell.append(((key,), release - press))
        if last_press is not None and 0 < press - last_press <= MAX_FLIGHT:
            flights.append(((history[-1] if history else None, key), boundary(history), press - last_press))
        last_press = press
        if key == BACKSPACE:
            backspaces += 1
            if history:
                history.pop()
            continue
        if backspaces:
            runs.append(backspaces)
            backspaces = 0
        history.append(key)
        characters += 1
    if backspaces:
        runs.append(backspaces)

    mid = sorted(value for _, context, value in flights if context == "mid")
    fence = 0.40
    if len(mid) >= 5:
        index = (len(mid) - 1) * 0.95
        lower = int(index)
        fence = mid[lower] + (mid[min(lower + 1, len(mid) - 1)] - mid[lower]) * (index - lower)
    fluent = [(keys, value) for keys, _, value in flights if value <= fence]
    base = median(value for _, value in fluent) if fluent else 0.13
    paused = [max(value - base, 0.001) for _, _, value in flights if value > fence]
    pause = {}
    for context in CONTEXTS:
        observed = [value for _, where, value in flights if where == context]
        extras = [max(value - base, 0.001) for value in observed if value > fence]
        probability = (len(extras) + 0.5) / (len(observed) + 1) if observed else 0
        pause[context] = [probability, distribution(extras) if len(extras) >= MIN_SAMPLES else None]
    return {
        "dwell": timing_buckets(dwell, [log(0.09), 0.3, 0]),
        "flight": timing_buckets(fluent, [log(0.13), 0.3, 0]),
        "pause": pause,
        "pause_global": distribution(paused, [log(0.4), 0.5, 0]),
        "correction": [min(len(runs) / max(characters, 1), 0.25), fmean(runs) if runs else 1.0],
    }


DEFAULT = fit(())
DEFAULT["pause"] = {"mid": [0.02, None], "word": [0.24, None], "clause": [0.55, None], "sentence": [0.8, None]}
DEFAULT["correction"] = [0.012, 1.0]


def lognormal(random, parameters, limits):
    mean, sigma, _ = parameters
    return exp(max(log(limits[0]), min(log(limits[1]), random.gauss(mean, sigma))))


def sample(model, random, keys, limits):
    pair = SEPARATOR.join(key or "^" for key in keys)
    classes = SEPARATOR.join(map(key_class, keys))
    parameters = model["exact"].get(pair) or model["classes"].get(classes) or model["global"]
    return lognormal(random, parameters, limits)


def unicode_entry(key):
    """Characters entered through the keyboard's Unicode input sequence."""
    return key not in (BACKSPACE, "\n", "\t") and (not key.isascii() or not key.isprintable())


@dataclass(frozen=True)
class KeyEvent:
    time: float
    key: str
    pressed: bool


def plan(text, model, random=None):
    """Build timed key transitions, including overlapped keys and corrected typos."""
    random = random or Random()
    events, history, held_until = [], [], {}
    down = last_up = 0.0
    chance, mean_run = model["correction"]

    def emit(key):
        nonlocal down, last_up
        if events:
            previous = history[-1] if history else None
            gap = sample(model["flight"], random, (previous, key), (0.010, 2.0))
            probability, parameters = model["pause"].get(boundary(history), (0, None))
            if probability and random.random() < probability:
                gap += lognormal(random, parameters or model["pause_global"], (0.05, 12.0))
            down += gap
        # Rollover is safe for distinct unshifted letters. A shifted key,
        # correction, Unicode sequence or repeat waits for earlier releases.
        if events and not (key in NEIGHBORS and events[-1].key in NEIGHBORS):
            down = max(down, last_up + 0.002)
        down = max(down, held_until.get(key, -1) + 0.002)
        duration = sample(model["dwell"], random, (key,), (0.012, 0.22))
        up = down + (max(duration, 0.18) if unicode_entry(key) else duration)
        events.extend((KeyEvent(down, key, True), KeyEvent(up, key, False)))
        held_until[key] = up
        last_up = max(last_up, up)
        if key == BACKSPACE:
            history.pop()
        else:
            history.append(key)

    index = 0
    while index < len(text):
        key = text[index]
        if not key.isascii() or key.lower() not in NEIGHBORS or random.random() >= chance:
            emit(key)
            index += 1
            continue
        # BioCadence's Poisson run of letters, ending in a neighboring key.
        remaining = exp(-(mean_run - 1))
        draw, run = random.random(), 1
        while run < 6 and draw > remaining:
            draw *= random.random()
            run += 1
        chunk = text[index:index + run]
        while chunk[-1].lower() not in NEIGHBORS or not chunk.isalpha() or not chunk.isascii():
            chunk = chunk[:-1]
        last = chunk[-1]
        wrong = random.choice(NEIGHBORS[last.lower()])
        for key in chunk[:-1] + (wrong.upper() if last.isupper() else wrong):
            emit(key)
        down += 0.15
        for _ in chunk:
            emit(BACKSPACE)
        for key in chunk:
            emit(key)
        index += len(chunk)

    return sorted(events, key=lambda event: event.time)
