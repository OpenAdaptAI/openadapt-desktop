"""Deterministic presentation-video derivatives for exact overlay timelines.

Raw recordings remain immutable. This module validates the canonical Types 0.5
timeline, binds it to the exact source-media hash, composites in memory over a
rawvideo pipe, and atomically publishes a separate MP4 derivative.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import uuid
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import BinaryIO

from openadapt_types import (
    ControlOverlayFrameV2,
    ControlOverlayMediaFrameBindingV2,
    ControlOverlayTimelineV2,
)
from PIL import Image, ImageDraw, ImageFont

TIMELINE_SCHEMA = "openadapt.control-overlay-timeline/v2"
MAX_TIMELINE_BYTES = 8 * 1024 * 1024
MAX_FRAME_PIXELS = 33_177_600  # 8K UHD
MAX_MEDIA_FRAMES = 2_000_000


@dataclass(frozen=True)
class PresentationExportPlan:
    capture_dir: Path
    timeline_path: Path
    media_path: Path
    media_sha256: str
    timeline: ControlOverlayTimelineV2


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timeline_candidates(capture_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for path in capture_dir.rglob("*.json"):
        if (
            not path.is_file()
            or path.is_symlink()
            or not path.resolve().is_relative_to(capture_dir)
            or path.stat().st_size > MAX_TIMELINE_BYTES
        ):
            continue
        try:
            prefix = path.read_text(encoding="utf-8")[:4096]
        except (OSError, UnicodeDecodeError):
            continue
        if TIMELINE_SCHEMA in prefix:
            candidates.append(path)
    return sorted(candidates)


def inspect_capture_for_presentation(capture_dir: Path) -> PresentationExportPlan:
    """Resolve one exact canonical timeline and its hash-bound raw MP4."""

    capture_dir = capture_dir.resolve(strict=True)
    timelines = _timeline_candidates(capture_dir)
    if len(timelines) != 1:
        raise ValueError(
            "presentation export requires exactly one canonical V2 overlay timeline; "
            f"found {len(timelines)}"
        )
    timeline_path = timelines[0]
    timeline = ControlOverlayTimelineV2.model_validate_json(
        timeline_path.read_text(encoding="utf-8")
    )
    if timeline.media_frame_count > MAX_MEDIA_FRAMES:
        raise ValueError("presentation timeline exceeds the local frame-count limit")

    matching: list[Path] = []
    for path in sorted(capture_dir.rglob("*.mp4")):
        if (
            "presentation" in path.relative_to(capture_dir).parts
            or not path.is_file()
            or path.is_symlink()
            or not path.resolve().is_relative_to(capture_dir)
        ):
            continue
        if _sha256(path) == timeline.media_sha256:
            matching.append(path)
    if len(matching) != 1:
        raise ValueError(
            "presentation timeline must bind exactly one raw MP4 in this capture; "
            f"found {len(matching)}"
        )
    return PresentationExportPlan(
        capture_dir=capture_dir,
        timeline_path=timeline_path,
        media_path=matching[0],
        media_sha256=timeline.media_sha256,
        timeline=timeline,
    )


def presentation_export_status(capture_dir: Path) -> dict[str, object]:
    try:
        plan = inspect_capture_for_presentation(capture_dir)
    except (OSError, ValueError) as error:
        return {"ready": False, "reason": str(error)}
    return {
        "ready": True,
        "reason": None,
        "media_sha256": plan.media_sha256,
        "media_frame_count": plan.timeline.media_frame_count,
    }


def _probe_video(ffprobe: Path, media: Path) -> tuple[int, int]:
    completed = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(media),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"could not inspect source video: {completed.stderr.strip()}")
    try:
        streams = json.loads(completed.stdout)["streams"]
        width = int(streams[0]["width"])
        height = int(streams[0]["height"])
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("source video has no usable video stream") from error
    if width <= 0 or height <= 0 or width * height > MAX_FRAME_PIXELS:
        raise RuntimeError("source video dimensions exceed the presentation boundary")
    return width, height


def _runtime_supports_rawvideo(ffmpeg: Path) -> bool:
    completed = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-encoders"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return completed.returncode == 0 and " rawvideo " in completed.stdout


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _phase_color(phase: str) -> tuple[int, int, int, int]:
    if phase == "verified":
        return (48, 190, 124, 255)
    if phase in {"halted", "failed", "completed_unverified", "rolled_back"}:
        return (244, 104, 83, 255)
    if phase in {"paused", "pausing", "stopping"}:
        return (244, 185, 66, 255)
    return (99, 102, 241, 255)


def _target_rect(
    frame: ControlOverlayFrameV2,
    *,
    frame_index: int,
    media_sha256: str,
    width: int,
    height: int,
) -> tuple[int, int, int, int] | None:
    target = frame.target_tracking
    if target is None or not isinstance(target.binding, ControlOverlayMediaFrameBindingV2):
        return None
    if (
        target.binding.media_sha256 != media_sha256
        or target.binding.frame_index != frame_index
    ):
        return None
    viewport = target.source_viewport
    expected_width = viewport.width_css_px * viewport.device_pixel_ratio
    expected_height = viewport.height_css_px * viewport.device_pixel_ratio
    # A full-frame mapping is valid only when the exact media dimensions prove
    # that the video is the CSS viewport. Browser chrome offsets are never
    # guessed. A future producer may carry an explicit content-box contract.
    if not (
        math.isclose(expected_width, width, abs_tol=1.0)
        and math.isclose(expected_height, height, abs_tol=1.0)
    ):
        return None
    rect = target.rect
    left = round(rect.x * width)
    top = round(rect.y * height)
    right = round((rect.x + rect.width) * width)
    bottom = round((rect.y + rect.height) * height)
    return left, top, right, bottom


def render_presentation_frame(
    image: Image.Image,
    frame: ControlOverlayFrameV2,
    *,
    frame_index: int,
    media_sha256: str,
) -> Image.Image:
    """Render one canonical status frame and an optional exactly bound target."""

    output = image.convert("RGBA")
    if not frame.visible:
        return output
    draw = ImageDraw.Draw(output, "RGBA")
    width, height = output.size
    margin = max(12, round(min(width, height) * 0.02))
    panel_width = min(max(280, round(width * 0.32)), max(280, width - 2 * margin))
    panel_height = min(88, max(64, round(height * 0.11)))
    left = max(margin, width - margin - panel_width)
    top = margin
    right = width - margin
    bottom = top + panel_height
    accent = _phase_color(frame.phase.value)
    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=16,
        fill=(13, 18, 28, 226),
    )
    draw.rounded_rectangle((left, top, left + 8, bottom), radius=4, fill=accent)
    font = ImageFont.load_default(size=max(12, round(panel_height * 0.18)))
    draw.text(
        (left + 22, top + 14),
        frame.workflow_label.value,
        fill=(245, 247, 250, 255),
        font=font,
    )
    step = ""
    if frame.step.current is not None and frame.step.total is not None:
        step = f"  Step {frame.step.current}/{frame.step.total}"
    draw.text(
        (left + 22, top + 42),
        f"{frame.status}{step}",
        fill=(205, 211, 221, 255),
        font=font,
    )

    target = _target_rect(
        frame,
        frame_index=frame_index,
        media_sha256=media_sha256,
        width=width,
        height=height,
    )
    if target is not None:
        line_width = max(3, round(min(width, height) * 0.006))
        draw.rounded_rectangle(target, radius=8, outline=accent, width=line_width)
    return output


def _wait_process(process: subprocess.Popen, label: str) -> None:
    if process.stdin is not None and process.stdin.closed:
        process.stdin = None
    _, stderr = process.communicate(timeout=60)
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"{label} failed: {detail}")


def export_presentation_video(capture_dir: Path) -> dict[str, object]:
    """Compose a new MP4 from one exact raw medium and canonical V2 timeline."""

    plan = inspect_capture_for_presentation(capture_dir)
    ffmpeg = Path(os.environ.get("OPENADAPT_FFMPEG_PATH", ""))
    ffprobe = Path(os.environ.get("OPENADAPT_FFPROBE_PATH", ""))
    if not ffmpeg.is_absolute() or not ffmpeg.is_file():
        raise RuntimeError("the managed FFmpeg runtime is not ready")
    if not ffprobe.is_absolute() or not ffprobe.is_file():
        raise RuntimeError("the managed ffprobe runtime is not ready")
    if not _runtime_supports_rawvideo(ffmpeg):
        raise RuntimeError(
            "the installed managed video runtime predates direct rawvideo composition; retry "
            "runtime provisioning after the OpenAdapt FFmpeg r2 update"
        )

    width, height = _probe_video(ffprobe, plan.media_path)
    frame_bytes = width * height * 4
    timeline = plan.timeline
    frame_rate = Fraction(timeline.media_frame_count * 1000, timeline.duration_ms)
    rate = f"{frame_rate.numerator}/{frame_rate.denominator}"
    presentation_dir = plan.capture_dir / "presentation"
    if presentation_dir.is_symlink():
        raise RuntimeError("presentation output directory cannot be a symbolic link")
    presentation_dir.mkdir(parents=True, exist_ok=True)
    if not presentation_dir.resolve().is_relative_to(plan.capture_dir):
        raise RuntimeError("presentation output directory escaped the capture boundary")
    token = uuid.uuid4().hex
    encoded = presentation_dir / f".{token}.video.mp4"
    remuxed = presentation_dir / f".{token}.complete.mp4"
    output = presentation_dir / f"{plan.media_path.stem}-openadapt-{token[:8]}.mp4"

    decoder = subprocess.Popen(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(plan.media_path),
            "-map",
            "0:v:0",
            "-fps_mode",
            "passthrough",
            "-pix_fmt",
            "rgba",
            "-c:v",
            "rawvideo",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    encoder = subprocess.Popen(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-f",
            "rawvideo",
            "-pixel_format",
            "rgba",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            rate,
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "mpeg4",
            "-q:v",
            "3",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-y",
            str(encoded),
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if decoder.stdout is None or encoder.stdin is None:
        raise RuntimeError("could not open the direct video-composition pipe")

    events = {event.media_frame_index: event for event in timeline.events}
    current = timeline.events[0].frame
    try:
        for frame_index in range(timeline.media_frame_count):
            raw = _read_exact(decoder.stdout, frame_bytes)
            if len(raw) != frame_bytes:
                raise RuntimeError(
                    f"source video ended at frame {frame_index}; timeline expects "
                    f"{timeline.media_frame_count}"
                )
            event = events.get(frame_index)
            if event is not None:
                current = event.frame
            rendered = render_presentation_frame(
                Image.frombytes("RGBA", (width, height), raw),
                current,
                frame_index=frame_index,
                media_sha256=plan.media_sha256,
            )
            encoder.stdin.write(rendered.tobytes())
        if decoder.stdout.read(1):
            raise RuntimeError("source video has more decoded frames than its exact timeline")
        encoder.stdin.close()
        _wait_process(decoder, "presentation video decode")
        _wait_process(encoder, "presentation video encode")

        # Preserve a compatible source audio stream without decoding it. The
        # optional map also succeeds for silent recordings.
        completed = subprocess.run(
            [
                str(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-i",
                str(encoded),
                "-i",
                str(plan.media_path),
                "-map",
                "0:v:0",
                "-map",
                "1:a?",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                "-y",
                str(remuxed),
            ],
            check=False,
            capture_output=True,
            timeout=120,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"presentation audio remux failed: {detail}")
        os.replace(remuxed, output)
        if _sha256(plan.media_path) != plan.media_sha256:
            output.unlink(missing_ok=True)
            raise RuntimeError("raw recording changed during presentation export")
    except Exception:
        decoder.kill()
        encoder.kill()
        raise
    finally:
        for temporary in (encoded, remuxed):
            temporary.unlink(missing_ok=True)

    return {
        "ok": True,
        "path": str(output),
        "sha256": _sha256(output),
        "source_media_sha256": plan.media_sha256,
        "media_frame_count": timeline.media_frame_count,
        "raw_media_unchanged": True,
    }
