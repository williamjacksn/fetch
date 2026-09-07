import re
import time
from pathlib import Path

import pytest
from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, ID3, TXXX, UFID, PictureType

from fetch import create_app, musicbrainz
from fetch.downloader import (
    DownloadError,
    ProgressCallback,
    _ffmpeg_location,
    _progress_hook,
)
from fetch.musicbrainz import CoverArt, Release, Track, release_id_from_url

RELEASE_ID = "b84ee12a-09ef-421b-82de-0441a926375b"


def sample_release() -> Release:
    return Release(
        release_id=RELEASE_ID,
        title="An Album",
        album_artist="An Artist",
        date="2024-01-02",
        tracks=(
            Track(
                "A Song",
                "An Artist",
                "recording-id",
                "1",
                1,
                1,
                1,
                1,
                artist_ids=("track-artist-id",),
            ),
        ),
        release_artist_ids=("release-artist-id",),
        release_group_id="release-group-id",
        original_year="1999",
    )


def test_release_url_validation() -> None:
    assert (
        release_id_from_url(f"https://musicbrainz.org/release/{RELEASE_ID}")
        == RELEASE_ID
    )


def test_rejects_non_musicbrainz_url() -> None:
    with pytest.raises(ValueError, match="MusicBrainz"):
        release_id_from_url(f"https://example.com/release/{RELEASE_ID}")


def test_get_form(tmp_path: Path) -> None:
    app = create_app({"TESTING": True, "OUTPUT_DIR": tmp_path})
    response = app.test_client().get("/")
    assert response.status_code == 200
    assert b"YouTube Music album URL" in response.data
    assert b'hx-indicator="#progress"' in response.data
    assert b"Downloading and tagging" in response.data


def test_job_status_shows_current_file_and_total(tmp_path: Path) -> None:
    app = create_app({"TESTING": True, "OUTPUT_DIR": tmp_path})
    job = app.extensions["job_store"].create()
    app.extensions["job_store"].update(
        job.id,
        state="running",
        stage="Downloading",
        current=3,
        total=12,
        filename="Third Song",
    )
    response = app.test_client().get(f"/jobs/{job.id}")
    assert b"Downloading file 3 of 12" in response.data
    assert b"Third Song" in response.data
    assert b'value="3" max="12"' in response.data
    assert b'hx-trigger="every 2s"' in response.data


def test_invalid_submission_does_not_download(tmp_path: Path) -> None:
    app = create_app({"TESTING": True, "OUTPUT_DIR": tmp_path})
    response = app.test_client().post(
        "/", data={"youtube_url": "https://evil.example/test", "musicbrainz_url": "bad"}
    )
    assert response.status_code == 400
    assert b"YouTube Music" in response.data


def test_htmx_validation_error_returns_swappable_fragment(tmp_path: Path) -> None:
    app = create_app({"TESTING": True, "OUTPUT_DIR": tmp_path})
    response = app.test_client().post(
        "/",
        data={"youtube_url": "invalid", "musicbrainz_url": "invalid"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert b'class="message error"' in response.data
    assert b"<html" not in response.data


def test_configured_ffmpeg_location(tmp_path: Path) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.touch()
    assert _ffmpeg_location(ffmpeg) == str(ffmpeg.resolve())


def test_yt_dlp_progress_uses_playlist_metadata() -> None:
    updates: list[tuple[int, int, str, str]] = []
    hook = _progress_hook(lambda *values: updates.append(values), expected_total=10)
    hook(
        {
            "status": "downloading",
            "filename": "fallback.webm",
            "info_dict": {
                "playlist_index": 4,
                "playlist_count": 10,
                "title": "Fourth Song",
            },
        }
    )
    assert updates == [(4, 10, "Fourth Song", "Downloading")]


def test_missing_configured_ffmpeg_location(tmp_path: Path) -> None:
    with pytest.raises(DownloadError, match="does not exist"):
        _ffmpeg_location(tmp_path / "missing-ffmpeg")


def test_cover_art_falls_back_to_release_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(
            self, status_code: int, content: bytes = b"", mime: str = "image/jpeg"
        ) -> None:
            self.status_code = status_code
            self.content = content
            self.headers = {"Content-Type": mime}

        def raise_for_status(self) -> None:
            return

    responses = iter(
        [Response(404), Response(200, b"png-cover", "image/png; charset=binary")]
    )
    requested_urls: list[str] = []

    def fake_get(url: str, **_: object) -> Response:
        requested_urls.append(url)
        return next(responses)

    monkeypatch.setattr(musicbrainz.requests, "get", fake_get)
    artwork = musicbrainz.MusicBrainzClient(user_agent="test").get_cover_art(
        sample_release()
    )
    assert artwork == CoverArt(data=b"png-cover", mime_type="image/png")
    assert "/release/" in requested_urls[0]
    assert "/release-group/" in requested_urls[1]


def test_successful_download_is_tagged_and_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app({"TESTING": True, "OUTPUT_DIR": tmp_path})
    monkeypatch.setattr(
        app.extensions["musicbrainz_client"], "get_release", lambda _: sample_release()
    )
    cover_bytes = b"fake-jpeg-cover"
    monkeypatch.setattr(
        app.extensions["musicbrainz_client"],
        "get_cover_art",
        lambda _: CoverArt(data=cover_bytes, mime_type="image/jpeg"),
    )

    def fake_download(
        _: str,
        staging: Path,
        *,
        progress_callback: ProgressCallback,
        expected_total: int,
    ) -> list[Path]:
        staging.mkdir(parents=True)
        path = staging / "0001 - source.mp3"
        ID3().save(path)
        progress_callback(1, expected_total, "A Song", "Downloading")
        return [path]

    app.extensions["album_downloader"] = fake_download
    response = app.test_client().post(
        "/",
        data={
            "youtube_url": "https://music.youtube.com/playlist?list=album",
            "musicbrainz_url": f"https://musicbrainz.org/release/{RELEASE_ID}",
        },
    )
    assert response.status_code == 202
    match = re.search(rb'hx-get="(/jobs/[^"]+)"', response.data)
    assert match is not None
    status_url = match.group(1).decode()
    for _ in range(100):
        status_response = app.test_client().get(status_url)
        if b"Saved 1 tracks" in status_response.data:
            break
        time.sleep(0.01)
    else:
        raise AssertionError("background download did not complete")
    output = tmp_path / "An Artist" / "An Album" / "01 A Song.mp3"
    assert output.exists()
    assert EasyID3(output)["title"] == ["A Song"]
    custom_tags = [
        frame for frame in ID3(output).getall("TXXX") if isinstance(frame, TXXX)
    ]
    tags_by_description = {frame.desc: frame.text for frame in custom_tags}
    assert tags_by_description["MusicBrainz Album Id"] == [RELEASE_ID]
    assert tags_by_description["MusicBrainz Artist Id"] == ["track-artist-id"]
    assert tags_by_description["MusicBrainz Album Artist Id"] == [
        "release-artist-id"
    ]
    assert tags_by_description["MusicBrainz Release Group Id"] == [
        "release-group-id"
    ]
    assert tags_by_description["originalyear"] == ["1999"]
    raw_id3 = ID3(output, translate=False)
    assert "TORY" not in raw_id3
    assert "TDOR" not in raw_id3
    recording_ids = [
        frame
        for frame in ID3(output).getall("UFID")
        if isinstance(frame, UFID) and frame.owner == "http://musicbrainz.org"
    ]
    assert [frame.data for frame in recording_ids] == [b"recording-id"]
    assert "MusicBrainz Track Id" not in tags_by_description
    covers = [
        frame for frame in ID3(output).getall("APIC") if isinstance(frame, APIC)
    ]
    assert len(covers) == 1
    assert covers[0].type == PictureType.COVER_FRONT
    assert covers[0].mime == "image/jpeg"
    assert covers[0].data == cover_bytes
