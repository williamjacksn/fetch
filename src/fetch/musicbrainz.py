from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse
from uuid import UUID

import requests

MUSICBRAINZ_HOSTS = {"musicbrainz.org", "www.musicbrainz.org"}


class MetadataError(RuntimeError):
    """Raised when release metadata cannot be obtained or understood."""


@dataclass(frozen=True)
class CoverArt:
    data: bytes
    mime_type: str


@dataclass(frozen=True)
class Track:
    title: str
    artist: str
    recording_id: str
    number: str
    disc_number: int
    track_number: int
    disc_total: int
    track_total: int
    artist_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Release:
    release_id: str
    title: str
    album_artist: str
    date: str
    tracks: tuple[Track, ...]
    release_artist_ids: tuple[str, ...] = ()
    release_group_id: str = ""
    original_year: str = ""


def release_id_from_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in MUSICBRAINZ_HOSTS
    ):
        raise ValueError("Enter an HTTPS MusicBrainz release URL.")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0] != "release":
        raise ValueError(
            "The MusicBrainz URL must point to a release, "
            "not a release group or recording."
        )
    try:
        return str(UUID(parts[1]))
    except ValueError as exc:
        raise ValueError(
            "The MusicBrainz release URL contains an invalid release ID."
        ) from exc


def _artist_credit(items: list[dict]) -> str:
    rendered: list[str] = []
    for item in items:
        if "name" in item:
            rendered.append(item["name"])
        elif "artist" in item:
            rendered.append(item["artist"]["name"])
        rendered.append(item.get("joinphrase", ""))
    return "".join(rendered).strip()


def _artist_ids(items: list[dict]) -> tuple[str, ...]:
    return tuple(
        artist_id
        for item in items
        if (artist_id := (item.get("artist") or {}).get("id"))
    )


class MusicBrainzClient:
    def __init__(self, *, user_agent: str, timeout: int = 20) -> None:
        self.user_agent = user_agent
        self.timeout = timeout

    def get_release(self, url: str) -> Release:
        release_id = release_id_from_url(url)
        endpoint = f"https://musicbrainz.org/ws/2/release/{release_id}"
        try:
            response = requests.get(
                endpoint,
                params={
                    "inc": "recordings+artist-credits+release-groups",
                    "fmt": "json",
                },
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise MetadataError(f"MusicBrainz request failed: {exc}") from exc

        media = data.get("media") or []
        disc_total = len(media)
        tracks: list[Track] = []
        for disc_number, medium in enumerate(media, start=1):
            medium_tracks = medium.get("tracks") or []
            track_total = len(medium_tracks)
            for track_number, item in enumerate(medium_tracks, start=1):
                recording = item.get("recording") or {}
                credit = (
                    item.get("artist-credit")
                    or recording.get("artist-credit")
                    or data.get("artist-credit")
                    or []
                )
                tracks.append(
                    Track(
                        title=item.get("title")
                        or recording.get("title")
                        or f"Track {track_number}",
                        artist=_artist_credit(credit),
                        recording_id=recording.get("id", ""),
                        number=str(item.get("number") or track_number),
                        disc_number=disc_number,
                        track_number=track_number,
                        disc_total=disc_total,
                        track_total=track_total,
                        artist_ids=_artist_ids(credit),
                    )
                )
        if not tracks:
            raise MetadataError("The MusicBrainz release has no tracks.")
        release_credit = data.get("artist-credit") or []
        release_group = data.get("release-group") or {}
        first_release_date = str(release_group.get("first-release-date") or "")
        return Release(
            release_id=release_id,
            title=data.get("title") or "Unknown album",
            album_artist=_artist_credit(release_credit),
            date=data.get("date") or "",
            tracks=tuple(tracks),
            release_artist_ids=_artist_ids(release_credit),
            release_group_id=release_group.get("id") or "",
            original_year=first_release_date[:4],
        )

    def get_cover_art(self, release: Release) -> CoverArt | None:
        urls = [
            f"https://coverartarchive.org/release/{release.release_id}/front-500"
        ]
        if release.release_group_id:
            urls.append(
                "https://coverartarchive.org/release-group/"
                f"{release.release_group_id}/front-500"
            )

        for url in urls:
            try:
                response = requests.get(
                    url,
                    headers={"User-Agent": self.user_agent},
                    timeout=self.timeout,
                )
                if response.status_code == 404:
                    continue
                response.raise_for_status()
            except requests.RequestException as exc:
                raise MetadataError(f"Cover Art Archive request failed: {exc}") from exc

            if not response.content:
                raise MetadataError("The Cover Art Archive returned an empty image.")
            content_type = response.headers.get("Content-Type", "image/jpeg")
            mime_type = content_type.partition(";")[0].strip().lower()
            if not mime_type.startswith("image/"):
                mime_type = "image/jpeg"
            return CoverArt(data=response.content, mime_type=mime_type)

        return None
