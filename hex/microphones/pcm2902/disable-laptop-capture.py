"""Keep the laptop audio controller output-only after WirePlumber starts."""

import json
import subprocess
import time


CARD = "alsa_card.pci-0000_00_1f.3"
deadline = time.monotonic() + 10

while time.monotonic() < deadline:
    result = subprocess.run(
        ["/usr/bin/pactl", "--format=json", "list", "cards"],
        capture_output=True, text=True, timeout=2,
    )
    if result.returncode == 0:
        cards = json.loads(result.stdout)
        if any(card["name"] == CARD for card in cards):
            subprocess.run(
                ["/usr/bin/pactl", "set-card-profile", CARD, "output:analog-stereo"],
                check=True, timeout=3,
            )
            subprocess.run(
                ["/usr/bin/amixer", "-q", "-c", "PCH", "sset", "Capture", "0%", "nocap"],
                check=True, timeout=3,
            )
            print("Built-in audio is output-only; laptop microphone capture is muted.")
            break
    time.sleep(0.2)
else:
    raise SystemExit("Laptop audio controller did not appear; capture remains hidden by WirePlumber rules.")
