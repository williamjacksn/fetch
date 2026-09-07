from __future__ import annotations

import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

from flask import Flask, render_template, request

from .downloader import DownloadError, download_album, validate_youtube_url
from .jobs import JobStore
from .musicbrainz import MetadataError, MusicBrainzClient, release_id_from_url
from .tagging import tag_and_move


def create_app(config: dict[str, object] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        OUTPUT_DIR=os.environ.get("FETCH_OUTPUT_DIR", str(Path.cwd() / "downloads")),
        FFMPEG_LOCATION=os.environ.get("FETCH_FFMPEG_LOCATION"),
        MUSICBRAINZ_USER_AGENT=os.environ.get(
            "FETCH_USER_AGENT", "fetch/0.1 (https://github.com/williamjacksn/fetch)"
        ),
    )
    if config:
        app.config.update(config)

    app.extensions["musicbrainz_client"] = MusicBrainzClient(
        user_agent=app.config["MUSICBRAINZ_USER_AGENT"]
    )
    app.extensions["album_downloader"] = partial(
        download_album, ffmpeg_location=app.config["FFMPEG_LOCATION"]
    )
    app.extensions["job_store"] = JobStore()
    app.extensions["job_executor"] = ThreadPoolExecutor(max_workers=2)

    def render_result(*, status: int = 200, **context: object) -> tuple[str, int]:
        """Return a result fragment to HTMX, or the complete page without it."""
        is_htmx = request.headers.get("HX-Request") == "true"
        template = "_result.html" if is_htmx else "index.html"
        # HTMX does not swap error responses by default, so validation errors are
        # successful fragment responses while ordinary form posts retain useful
        # HTTP error statuses.
        response_status = 200 if is_htmx else status
        return render_template(template, **context), response_status

    @app.route("/", methods=["GET", "POST"])
    def index() -> str | tuple[str, int]:
        if request.method == "GET":
            return render_template("index.html", output_dir=app.config["OUTPUT_DIR"])

        youtube_url = request.form.get("youtube_url", "")
        musicbrainz_url = request.form.get("musicbrainz_url", "")
        try:
            validate_youtube_url(youtube_url)
            release_id_from_url(musicbrainz_url)
        except ValueError as exc:
            return render_result(
                status=400,
                error=str(exc),
                youtube_url=youtube_url,
                musicbrainz_url=musicbrainz_url,
                output_dir=app.config["OUTPUT_DIR"],
            )

        job = app.extensions["job_store"].create()
        app.extensions["job_executor"].submit(
            process_album, app, job.id, youtube_url, musicbrainz_url
        )
        template = (
            "_job.html" if request.headers.get("HX-Request") == "true" else "index.html"
        )
        return render_template(
            template, job=job, output_dir=app.config["OUTPUT_DIR"]
        ), 202

    @app.get("/jobs/<job_id>")
    def job_status(job_id: str) -> str | tuple[str, int]:
        job = app.extensions["job_store"].get(job_id)
        if job is None:
            return render_template("_result.html", error="Download job not found."), 404
        if job.state == "complete":
            return render_template("_result.html", success=job.result)
        if job.state == "failed":
            return render_template("_result.html", error=job.error)
        return render_template("_job.html", job=job)

    return app


def process_album(
    app: Flask, job_id: str, youtube_url: str, musicbrainz_url: str
) -> None:
    store: JobStore = app.extensions["job_store"]
    output_root = Path(app.config["OUTPUT_DIR"]).expanduser().resolve()
    staging: Path | None = None

    def progress(current: int, total: int, filename: str, stage: str) -> None:
        store.update(
            job_id,
            state="running",
            stage=stage,
            current=current,
            total=total,
            filename=filename,
        )

    try:
        store.update(job_id, state="running", stage="Fetching metadata")
        output_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="fetch-", dir=output_root))
        release = app.extensions["musicbrainz_client"].get_release(musicbrainz_url)
        store.update(
            job_id,
            stage="Fetching cover art",
            current=0,
            total=len(release.tracks),
            filename="",
        )
        cover_art = app.extensions["musicbrainz_client"].get_cover_art(release)
        files = app.extensions["album_downloader"](
            youtube_url,
            staging / "audio",
            progress_callback=progress,
            expected_total=len(release.tracks),
        )
        album_dir = tag_and_move(
            files, release, output_root, progress, cover_art=cover_art
        )
        cover_note = "" if cover_art else " (no cover art was available)"
        store.update(
            job_id,
            state="complete",
            current=len(release.tracks),
            total=len(release.tracks),
            result=(
                f"Saved {len(release.tracks)} tracks to {album_dir}{cover_note}"
            ),
        )
    except (MetadataError, DownloadError, ValueError, FileExistsError, OSError) as exc:
        store.update(job_id, state="failed", error=str(exc))
    except Exception:
        app.logger.exception("Unexpected failure processing job %s", job_id)
        store.update(
            job_id, state="failed", error="An unexpected error stopped the download."
        )
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
