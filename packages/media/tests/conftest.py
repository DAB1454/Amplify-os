"""Pytest fixtures for media tests — generates small sample files."""

from __future__ import annotations

import struct
import tempfile
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    """Provide a clean temp directory for test outputs."""
    return tmp_path


@pytest.fixture
def sample_artwork(tmp_path: Path) -> Path:
    """Generate a small 100x100 test image (PNG)."""
    img = Image.new("RGB", (100, 100), color=(30, 60, 120))
    path = tmp_path / "artwork.png"
    img.save(path)
    return path


@pytest.fixture
def sample_artwork_square(tmp_path: Path) -> Path:
    """Generate a 200x200 test image."""
    img = Image.new("RGB", (200, 200), color=(200, 160, 80))
    path = tmp_path / "artwork_square.png"
    img.save(path)
    return path


@pytest.fixture
def sample_audio_wav(tmp_path: Path) -> Path:
    """Generate a tiny valid WAV file (~0.5 seconds of silence)."""
    path = tmp_path / "sample.wav"
    sample_rate = 44100
    num_samples = sample_rate // 2  # 0.5 seconds
    num_channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = num_samples * block_align

    with open(path, "wb") as f:
        # RIFF header
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_size))
        f.write(b"WAVE")
        # fmt chunk
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))  # chunk size
        f.write(struct.pack("<H", 1))   # PCM
        f.write(struct.pack("<H", num_channels))
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", byte_rate))
        f.write(struct.pack("<H", block_align))
        f.write(struct.pack("<H", bits_per_sample))
        # data chunk
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(b"\x00" * data_size)  # silence

    return path


@pytest.fixture
def sample_video_mp4(tmp_path: Path) -> Path:
    """Generate a minimal MP4 test file using ffmpeg (if available).

    Falls back to an empty file if ffmpeg is not installed — tests
    that require a real video should skip via pytest.mark.skipif.
    """
    import subprocess

    path = tmp_path / "sample.mp4"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=1",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                "-t", "1",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac",
                "-pix_fmt", "yuv420p",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        # Create a dummy file so tests that don't need real video can still reference it
        path.write_bytes(b"\x00" * 1024)

    return path
