from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

YOUTUBE_HOSTS = {"music.youtube.com", "www.youtube.com", "youtube.com", "youtu.be"}
ProgressCallback = Callable[[int, int, str, str], None]


class DownloadError(RuntimeError):
    """Raised when media download or conversion fails."""


def _progress_hook(
    progress_callback: ProgressCallback | None, expected_total: int
) -> Callable[[dict], None]:
    def report(event: dict) -> None:
        if progress_callback is None or event.get("status") not in {
            "downloading",
            "finished",
        }:
            return
        # yt-dlp supplies the extracted playlist/video metadata as info_dict.
        info = event.get("info_dict") or {}
        try:
            current = int(info.get("playlist_index") or 1)
            total = int(
                info.get("playlist_count") or info.get("n_entries") or expected_total
            )
        except TypeError, ValueError:
            current, total = 1, expected_total
        title = str(
            info.get("title") or Path(event.get("filename") or "Unknown track").stem
        )
        progress_callback(current, total, title, "Downloading")

    return report


def validate_youtube_url(url: str) -> str:
    value = url.strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in YOUTUBE_HOSTS:
        raise ValueError("Enter an HTTPS YouTube Music album or playlist URL.")
    if parsed.hostname == "music.youtube.com" and not parsed.path.startswith(
        ("/playlist", "/browse")
    ):
        raise ValueError("The YouTube Music URL must point to an album or playlist.")
    return value


def _ffmpeg_location(configured_location: str | Path | None) -> str:
    if configured_location:
        path = Path(configured_location).expanduser().resolve()
        if not path.exists():
            raise DownloadError(f"Configured FFmpeg path does not exist: {path}")
        return str(path)

    discovered = shutil.which("ffmpeg")
    if discovered is None:
        raise DownloadError(
            "FFmpeg was not found. Add it to PATH or set FETCH_FFMPEG_LOCATION."
        )
    return discovered


def download_album(
    url: str,
    staging_dir: Path,
    *,
    ffmpeg_location: str | Path | None = None,
    progress_callback: ProgressCallback | None = None,
    expected_total: int = 0,
) -> list[Path]:
    validated_url = validate_youtube_url(url)
    resolved_ffmpeg_location = _ffmpeg_location(ffmpeg_location)

    staging_dir.mkdir(parents=True, exist_ok=False)
    options = {
        "format": "bestaudio/best",
        "outtmpl": str(staging_dir / "%(playlist_index)04d - %(title)s.%(ext)s"),
        "noplaylist": False,
        "ignoreerrors": False,
        "restrictfilenames": True,
        "ffmpeg_location": resolved_ffmpeg_location,
        "progress_hooks": [_progress_hook(progress_callback, expected_total)],
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            }
        ],
        "quiet": True,
        "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            downloader.download([validated_url])
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadError(f"YouTube download failed: {exc}") from exc

    files = sorted(staging_dir.glob("*.mp3"))
    if not files:
        raise DownloadError("The download completed without producing any MP3 files.")
    return files
