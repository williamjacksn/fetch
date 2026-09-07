from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path

from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, ID3, TXXX, UFID, ID3NoHeaderError, PictureType

from .musicbrainz import CoverArt, Release, Track

INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(value: str, fallback: str = "Unknown") -> str:
    cleaned = INVALID_FILENAME.sub("_", value).strip(" .")
    return cleaned[:150] or fallback


def _set_text_tag(id3: ID3, description: str, values: tuple[str, ...]) -> None:
    id3.delall(f"TXXX:{description}")
    if values:
        id3.add(TXXX(encoding=3, desc=description, text=list(values)))


def _write_tags(
    path: Path, release: Release, track: Track, cover_art: CoverArt | None
) -> None:
    try:
        tags = EasyID3(path)
    except ID3NoHeaderError:
        tags = EasyID3()
    tags["title"] = track.title
    tags["artist"] = track.artist or release.album_artist
    tags["album"] = release.title
    tags["albumartist"] = release.album_artist
    tags["tracknumber"] = f"{track.track_number}/{track.track_total}"
    tags["discnumber"] = f"{track.disc_number}/{track.disc_total}"
    if release.date:
        tags["date"] = release.date
    tags.save(path, v2_version=3)

    id3 = ID3(path)
    _set_text_tag(id3, "MusicBrainz Album Id", (release.release_id,))
    _set_text_tag(id3, "MusicBrainz Artist Id", track.artist_ids)
    _set_text_tag(id3, "MusicBrainz Album Artist Id", release.release_artist_ids)
    _set_text_tag(
        id3,
        "MusicBrainz Release Group Id",
        (release.release_group_id,) if release.release_group_id else (),
    )

    # Picard exposes this custom frame as its `originalyear` tag. TORY/TDOR
    # instead appear as "Original Release Date", which is a distinct field.
    id3.delall("TORY")
    id3.delall("TDOR")
    id3.delall("TXXX:Original Year")
    _set_text_tag(
        id3,
        "originalyear",
        (release.original_year,) if release.original_year else (),
    )

    id3.delall("TXXX:MusicBrainz Track Id")
    id3.delall("UFID:http://musicbrainz.org")
    if track.recording_id:
        id3.add(
            UFID(owner="http://musicbrainz.org", data=track.recording_id.encode())
        )
    id3.delall("APIC")
    if cover_art:
        id3.add(
            APIC(
                encoding=3,
                mime=cover_art.mime_type,
                type=PictureType.COVER_FRONT,
                desc="Cover",
                data=cover_art.data,
            )
        )
    id3.save(path, v2_version=3)


def tag_and_move(
    files: list[Path],
    release: Release,
    output_root: Path,
    progress_callback: Callable[[int, int, str, str], None] | None = None,
    cover_art: CoverArt | None = None,
) -> Path:
    if len(files) != len(release.tracks):
        raise ValueError(
            f"Track count mismatch: YouTube produced {len(files)} tracks, "
            f"but MusicBrainz lists {len(release.tracks)}."
        )

    album_dir = output_root / safe_name(release.album_artist) / safe_name(release.title)
    album_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        for source, track in zip(files, release.tracks, strict=True):
            if progress_callback:
                progress_callback(
                    len(created) + 1, len(release.tracks), track.title, "Tagging"
                )
            prefix = (
                f"{track.disc_number:02d}-{track.track_number:02d}"
                if track.disc_total > 1
                else f"{track.track_number:02d}"
            )
            destination = album_dir / f"{prefix} {safe_name(track.title, 'Track')}.mp3"
            if destination.exists():
                raise FileExistsError(
                    f"Refusing to overwrite existing file: {destination}"
                )
            shutil.move(source, destination)
            created.append(destination)
            _write_tags(destination, release, track, cover_art)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return album_dir
