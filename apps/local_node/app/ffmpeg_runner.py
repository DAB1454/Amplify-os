"""FFmpeg runner for local media processing."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class FFmpegRunner:
    """Shell out to ffmpeg for media rendering tasks."""

    def check_installed(self) -> bool:
        """Return True if ffmpeg is available on PATH."""
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False
        except Exception:
            logger.exception("Error checking ffmpeg")
            return False

    def render(self, input_path: Path, output_path: Path, filters: list[str]) -> Path:
        """Render media with the given ffmpeg filter chain.

        Returns the output path on success.
        """
        filter_arg = ",".join(filters) if filters else "null"
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", filter_arg,
            str(output_path),
        ]
        logger.info("Running: %s", " ".join(cmd))
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return output_path

    def extract_audio(self, video_path: Path) -> Path:
        """Extract audio track from a video file.

        Returns the path to the extracted audio file.
        """
        output_path = video_path.with_suffix(".wav")
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-vn",
            "-acodec", "pcm_s16le",
            str(output_path),
        ]
        logger.info("Extracting audio: %s", " ".join(cmd))
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return output_path
