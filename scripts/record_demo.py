"""Render the demo video from the page, frame by frame, without recording anyone's screen.

Each beat is a still taken by headless Chrome at 1280x720; the terminal beat is a run of stills that
scroll. ffmpeg holds each frame for its share of the running time. The result is a silent mp4.

    python scripts/record_demo.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from build_demo import BEATS, build  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FRAMES = ROOT / "frames"
OUT = ROOT / "media" / "remit-demo.mp4"

# One line of the terminal block is about 18px in a 556px window, so this many lines fit without
# scrolling. Below that the beat is a single still.
TERMINAL_LINES_ON_SCREEN = 30
TERMINAL_SCROLL_TO = 2600


def shot(url: str, target: Path, profile: Path) -> None:
    """Take one frame.

    Headless Chrome on this machine writes the file and then hangs on the way out, so a timeout here
    means nothing by itself. The frame on disk is what decides success.
    """
    command = [
        CHROME,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-extensions",
        f"--user-data-dir={profile}",
        "--window-size=1280,720",
        "--hide-scrollbars",
        f"--screenshot={target}",
        url,
    ]
    try:
        subprocess.run(command, capture_output=True, timeout=25)
    except subprocess.TimeoutExpired:
        subprocess.run(["pkill", "-f", str(profile)], capture_output=True)

    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError(f"no frame produced for {url}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-build", action="store_true", help="reuse site/demo.html as it stands")
    args = parser.parse_args()

    if args.skip_build:
        demo = ROOT / "site" / "demo.html"
        if not demo.exists():
            raise SystemExit("no site/demo.html to reuse")
    else:
        demo, _ = build(ROOT / "site")
    page = f"file://{demo}"

    shutil.rmtree(FRAMES, ignore_errors=True)
    FRAMES.mkdir(parents=True)
    profile = FRAMES / "chrome-profile"

    meta_path = ROOT / "site" / "demo-meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    lines = meta.get("terminal_lines", 999)
    # The page decides which beats exist (the receipt beat only exists once a settlement has run), so
    # the recorder follows the page rather than its own list.
    beats = [(name, seconds, "") for name, seconds in meta["beats"]] if meta.get("beats") else BEATS
    terminal_frames = 1 if lines <= TERMINAL_LINES_ON_SCREEN else 16
    if terminal_frames == 1:
        print(f"  terminal output is {lines} lines, which fits: one still, no scroll")

    plan: list[tuple[Path, float]] = []
    index = 0
    for name, seconds, _ in beats:
        if name == "terminal":
            per = seconds / terminal_frames
            for step in range(terminal_frames):
                scroll = 0 if terminal_frames == 1 else round(TERMINAL_SCROLL_TO * step / (terminal_frames - 1))
                frame = FRAMES / f"{index:03d}.png"
                shot(f"{page}?beat=terminal&scroll={scroll}", frame, profile)
                plan.append((frame, per))
                index += 1
                print(f"  terminal frame {step + 1}/{terminal_frames}", flush=True)
        else:
            frame = FRAMES / f"{index:03d}.png"
            shot(f"{page}?beat={name}", frame, profile)
            plan.append((frame, float(seconds)))
            index += 1
            print(f"  {name}", flush=True)

    # The concat demuxer needs the last file repeated, and gives that repeat the duration of the
    # entry before it. Splitting a tenth of a second off the final beat keeps the video from ending
    # on a second helping of its own last frame.
    last_frame, last_seconds = plan[-1]
    plan[-1] = (last_frame, last_seconds - 0.1)
    plan.append((last_frame, 0.1))

    concat = FRAMES / "concat.txt"
    lines = []
    for frame, seconds in plan:
        lines.append(f"file '{frame.name}'")
        lines.append(f"duration {seconds:.3f}")
    lines.append(f"file '{plan[-1][0].name}'")  # ffmpeg needs the last frame repeated
    concat.write_text("\n".join(lines) + "\n")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat),
            "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
                   "pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=30",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-an",
            str(OUT),
        ],
        check=True,
    )
    total = sum(seconds for _, seconds in plan)
    print(f"\nwrote {OUT} ({total:.0f}s, {len(plan)} frames)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
