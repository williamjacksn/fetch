# fetch

A small Flask app that downloads a YouTube Music album as MP3 files, tags them from a specific MusicBrainz release, and embeds its front cover from the Cover Art Archive.

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- FFmpeg available on `PATH`, or its location configured as shown below

## Run

```powershell
uv sync
$env:FETCH_OUTPUT_DIR = "D:\Music"
$env:FETCH_FFMPEG_LOCATION = "C:\ffmpeg\bin\ffmpeg.exe"
uv run fetch
```

Open <http://127.0.0.1:5000>, paste a YouTube Music album/playlist URL and a MusicBrainz **release** URL, then submit.
Output is written under `Artist/Album` inside `FETCH_OUTPUT_DIR`. If the variable is unset, the default is
`./downloads`.

Optional settings:

- `FETCH_HOST` (default `127.0.0.1`)
- `FETCH_PORT` (default `5000`)
- `FETCH_FFMPEG_LOCATION` (path to the FFmpeg executable or its containing directory; defaults to looking on `PATH`)
- `FETCH_USER_AGENT` (MusicBrainz asks API clients to identify themselves)

The app never overwrites an existing MP3. YouTube downloading may be subject to YouTube's terms and copyright law; only
download media you are authorized to copy.

## Test

```powershell
uv sync --extra test
uv run pytest
```
