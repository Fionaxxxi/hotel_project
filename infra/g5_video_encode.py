"""Encode timestamped actual CUA browser captures; never invent uncaptured elapsed time."""

import hashlib
import json
import re
import subprocess

import imageio_ffmpeg

from infra.manage import ROOT


def duration(exe, path):
    result = subprocess.run([exe, "-i", str(path)], capture_output=True, text=True)
    match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", result.stderr)
    assert match, "Encoded duration unavailable"
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def main():
    recording = ROOT / ".runtime/g5-recording"
    manifest = json.loads((recording / "timeline.json").read_text(encoding="utf-8"))
    frames = manifest["frames"]
    assert len(frames) > 100
    elapsed = (frames[-1]["at_ms"] - frames[0]["at_ms"]) / 1000
    assert 720 <= elapsed <= 900, "Actual captured timeline must be 12–15 minutes"
    lines = []
    hashes = []
    for index, frame in enumerate(frames):
        path = (recording / frame["file"]).resolve()
        assert path.is_relative_to(recording.resolve()) and path.is_file()
        lines.append("file '" + path.as_posix().replace("'", "'\\''") + "'")
        if index + 1 < len(frames):
            delta = (frames[index + 1]["at_ms"] - frame["at_ms"]) / 1000
            assert 0 < delta <= 60, "Capture gap over one minute cannot be presented as continuous recording"
            lines.append(f"duration {delta:.3f}")
        hashes.append({"file": frame["file"], "at_ms": frame["at_ms"], "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    concat = recording / "frames.txt"
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = ROOT / "docs/acceptance"
    full = out / "g5-demo-full.mp4"
    short = out / "g5-demo-short.mp4"
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([exe, "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-vf", "scale=1280:800:force_original_aspect_ratio=decrease,pad=1280:800:(ow-iw)/2:(oh-ih)/2", "-fps_mode", "vfr", "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(full)], check=True, capture_output=True)
    subprocess.run([exe, "-y", "-ss", "0", "-i", str(full), "-t", "120", "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(short)], check=True, capture_output=True)
    report = {"status": "PASSED", "capture_type": "Actual timestamped low-frame-rate browser capture, assembled with original elapsed intervals; not native full-motion screen recording", "scope": "Local synthetic hotel project browser only, no audio or other apps", "source_elapsed_seconds": elapsed, "full_duration_seconds": duration(exe, full), "short_duration_seconds": duration(exe, short), "frames": hashes, "actions": manifest.get("actions", []), "files": {p.name: {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in [full, short]}}
    (out / "g5-video.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"full_seconds": report["full_duration_seconds"], "short_seconds": report["short_duration_seconds"]}))


if __name__ == "__main__":
    main()
