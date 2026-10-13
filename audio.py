"""Read the output IDs reported by mpv rather than guessing an ALSA card name."""
from dataclasses import dataclass
import re
import subprocess


@dataclass(frozen=True)
class AudioOutput:
    name: str
    description: str

    @property
    def label(self):
        if self.name == "auto":
            return "System default output"
        suffix = " · desktop audio" if self.name.startswith(("pulse/", "pipewire/")) else ""
        return self.description[:85] + suffix


def parse_outputs(text):
    outputs = []
    pending = None
    seen = set()

    def finish():
        nonlocal pending
        if pending is None:
            return
        name, lines = pending
        description = " ".join(" ".join(lines).split()).rstrip()
        if description.endswith(")"):
            description = description[:-1].rstrip()
        if name not in seen and (name == "auto" or "/" in name):
            seen.add(name)
            outputs.append(AudioOutput(name, description or name))
        pending = None

    for line in text.splitlines():
        match = re.match(r"^\s*'([^'\r\n]+)'\s*\((.*)$", line)
        if match:
            finish()
            pending = (match.group(1), [match.group(2)])
        elif pending and line.strip():
            pending[1].append(line.strip())
        if pending and line.rstrip().endswith(")"):
            finish()
    finish()
    return outputs


def discover_outputs():
    try:
        result = subprocess.run(["mpv", "--no-config", "--audio-device=help"],
                                capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        raise RuntimeError("Playback software is missing. Run: sudo apt install mpv") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("Audio device detection timed out. Check your DAC and retry.") from None
    if result.returncode:
        raise RuntimeError("Audio outputs could not be detected. " + (result.stderr or result.stdout)[-500:])
    outputs = parse_outputs(result.stdout + "\n" + result.stderr)
    if not outputs:
        raise RuntimeError("No audio outputs were reported. Check your DAC and refresh the list.")
    return outputs
