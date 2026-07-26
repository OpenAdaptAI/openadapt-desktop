from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from openadapt_types import (
    ControlOverlayDataClassification,
    ControlOverlayFrameV2,
    ControlOverlayMediaFrameBindingV2,
    ControlOverlayMode,
    ControlOverlayNormalizedRectV2,
    ControlOverlayPhase,
    ControlOverlaySourceViewportV2,
    ControlOverlayTargetTrackingV2,
    ControlOverlayTimelineEventV2,
    ControlOverlayTimelineV2,
)
from PIL import Image

from engine.presentation_export import (
    choose_capsule_bounds,
    export_presentation_video,
    inspect_capture_for_presentation,
    render_presentation_frame,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _frame(
    *,
    sequence: int,
    media_sha256: str,
    frame_index: int,
    target: bool = False,
) -> ControlOverlayFrameV2:
    tracking = None
    if target:
        tracking = ControlOverlayTargetTrackingV2(
            rect=ControlOverlayNormalizedRectV2(x=0.1, y=0.2, width=0.3, height=0.4),
            source_viewport=ControlOverlaySourceViewportV2(
                width_css_px=64,
                height_css_px=64,
                device_pixel_ratio=1.0,
            ),
            binding=ControlOverlayMediaFrameBindingV2(
                media_sha256=media_sha256,
                frame_index=frame_index,
            ),
            action_kind="click",
        )
    return ControlOverlayFrameV2.build(
        event_sequence=sequence,
        observed_at_unix_ms=1_785_000_000_000 + sequence,
        observed_at_monotonic_ms=float(sequence),
        visible=True,
        phase=ControlOverlayPhase.EXECUTING,
        mode=ControlOverlayMode.GOVERNED,
        current_step=sequence + 1,
        total_steps=3,
        target_tracking=tracking,
    )


def _write_timeline(capture: Path, media: Path, *, frame_count: int = 3) -> Path:
    media_sha256 = _sha256(media)
    events = tuple(
        ControlOverlayTimelineEventV2(
            at_ms=index * 200,
            media_frame_index=index,
            frame=_frame(
                sequence=index,
                media_sha256=media_sha256,
                frame_index=index,
                target=index == 1,
            ),
        )
        for index in range(frame_count)
    )
    timeline = ControlOverlayTimelineV2(
        data_classification=ControlOverlayDataClassification.SYNTHETIC,
        evidence_pack_id="desktop-export-test",
        media_sha256=media_sha256,
        media_frame_count=frame_count,
        duration_ms=frame_count * 200,
        events=events,
    )
    path = capture / "control-overlay-timeline-v2.json"
    path.write_text(timeline.model_dump_json(indent=2), encoding="utf-8")
    return path


def test_inspection_requires_one_exact_hash_bound_media(tmp_path: Path) -> None:
    capture = tmp_path / "capture"
    media = capture / "video" / "raw.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"synthetic mp4 placeholder")
    _write_timeline(capture, media)

    plan = inspect_capture_for_presentation(capture)
    assert plan.media_path == media
    assert plan.media_sha256 == _sha256(media)

    media.write_bytes(b"mutated")
    with pytest.raises(ValueError, match="bind exactly one raw MP4"):
        inspect_capture_for_presentation(capture)


def test_target_renders_only_when_full_viewport_mapping_is_proven() -> None:
    digest = "a" * 64
    frame = _frame(sequence=1, media_sha256=digest, frame_index=1, target=True)
    source = Image.new("RGBA", (64, 64), (10, 20, 30, 255))
    baseline = render_presentation_frame(
        source,
        frame.model_copy(update={"target_tracking": None}),
        frame_index=1,
        media_sha256=digest,
    )
    rendered = render_presentation_frame(
        source,
        frame,
        frame_index=1,
        media_sha256=digest,
    )
    wrong_frame = render_presentation_frame(
        source,
        frame,
        frame_index=2,
        media_sha256=digest,
    )

    assert rendered.getpixel((6, 24)) != baseline.getpixel((6, 24))
    assert wrong_frame.getpixel((6, 24)) == baseline.getpixel((6, 24))


def test_capsule_moves_left_only_for_exact_bottom_right_conflict() -> None:
    right_target = (560, 430, 790, 590)
    bounds, corner = choose_capsule_bounds(
        width=800,
        height=600,
        panel_width=300,
        panel_height=90,
        margin=12,
        protected_regions=(right_target,),
    )

    assert corner == "bottom-left"
    assert bounds[0] == 12

    default_bounds, default_corner = choose_capsule_bounds(
        width=800,
        height=600,
        panel_width=300,
        panel_height=90,
        margin=12,
    )
    assert default_corner == "bottom-right"
    assert default_bounds[2] == 788


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="system FFmpeg is not installed",
)
def test_direct_rawvideo_export_preserves_source(tmp_path: Path, monkeypatch) -> None:
    capture = tmp_path / "capture"
    media = capture / "video" / "raw.mp4"
    media.parent.mkdir(parents=True)
    frames = capture / "frames.rgb"
    frames.write_bytes(
        b"".join(bytes((index * 60, 30, 100)) * (64 * 64) for index in range(3))
    )
    ffmpeg = Path(shutil.which("ffmpeg") or "")
    ffprobe = Path(shutil.which("ffprobe") or "")
    subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pixel_format",
            "rgb24",
            "-video_size",
            "64x64",
            "-framerate",
            "5",
            "-i",
            str(frames),
            "-frames:v",
            "3",
            "-an",
            "-c:v",
            "mpeg4",
            "-y",
            str(media),
        ],
        check=True,
    )
    frames.unlink()
    _write_timeline(capture, media)
    source_hash = _sha256(media)
    monkeypatch.setenv("OPENADAPT_FFMPEG_PATH", str(ffmpeg))
    monkeypatch.setenv("OPENADAPT_FFPROBE_PATH", str(ffprobe))

    result = export_presentation_video(capture)

    assert result["raw_media_unchanged"] is True
    assert result["placement_policy"] == "collision-aware-bottom-corner"
    assert _sha256(media) == source_hash
    output = Path(str(result["path"]))
    assert output.is_file()
    probe = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert int(json.loads(probe.stdout)["streams"][0]["nb_read_frames"]) == 3
    assert os.path.commonpath((output, capture)) == str(capture)
