"""yt-dlp wrapper: builds options, lists playlists, downloads with progress callbacks."""
import os
import threading
from dataclasses import dataclass, field

import yt_dlp

from .utils import format_ranges

AUDIO_FORMATS = ("mp3", "m4a")
QUALITIES = ("best", "2160", "1440", "1080", "720", "480", "360", "mp3", "m4a")

# YouTube needs a JavaScript runtime for some formats; yt-dlp only enables deno by default.
JS_RUNTIMES = {"deno": {"path": None}, "node": {"path": None}}

# Playlist  -> "<folder>/Playlist Title/007 - Video Title.mp4"
# Single    -> "<folder>/Video Title.mp4"
# The "/" must stay outside the field, otherwise yt-dlp sanitizes it into "⧸" on Windows.
OUTTMPL = "%(playlist_title|.)s/%(playlist_index&{:03d} - |)s%(title)s.%(ext)s"


@dataclass
class Options:
    folder: str
    quality: str = "1080"  # one of QUALITIES
    playlist: bool = True  # False = only the video in a "watch?v=..&list=.." link
    mp4: bool = True  # prefer H.264/AAC in an .mp4 that plays everywhere
    subtitles: bool = False
    embed: bool = True  # embed thumbnail + metadata
    items: list = field(default_factory=list)  # selected playlist indices; empty = all


class Cancelled(Exception):
    pass


class NotAPlaylist(Exception):
    pass


def build_params(opts):
    """Turns Options into a yt-dlp params dict (without hooks/logger)."""
    params = {
        "paths": {"home": opts.folder},
        "outtmpl": OUTTMPL,
        "noplaylist": not opts.playlist,
        "ignoreerrors": True,  # private/deleted videos shouldn't stop a playlist
        "windowsfilenames": True,
        "noprogress": True,
        "js_runtimes": JS_RUNTIMES,
        "concurrent_fragment_downloads": 4,
    }
    postprocessors = []
    audio = opts.quality in AUDIO_FORMATS

    if audio:
        params["format"] = "ba[ext=m4a]/ba/b"
        pp = {"key": "FFmpegExtractAudio", "preferredcodec": opts.quality}
        if opts.quality == "mp3":
            pp["preferredquality"] = "320"
        postprocessors.append(pp)
    else:
        h = "" if opts.quality == "best" else f"[height<={opts.quality}]"
        if opts.mp4:
            params["format"] = f"bv*{h}[ext=mp4]+ba[ext=m4a]/bv*{h}+ba/b{h}/b"
            params["merge_output_format"] = "mp4"
            # without this YouTube often hands out AV1, which many Windows players can't play
            params["format_sort"] = ["res", "vcodec:h264", "acodec:m4a"]
        else:
            params["format"] = f"bv*{h}+ba/b{h}/b"

        if opts.subtitles:
            params.update({
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": ["tr", "en"],
                "subtitlesformat": "srt/best",
            })
            postprocessors.append({"key": "FFmpegSubtitlesConvertor", "format": "srt"})

    if opts.embed:
        params["writethumbnail"] = True
        postprocessors.append({"key": "FFmpegMetadata", "add_metadata": True})
        postprocessors.append({"key": "EmbedThumbnail"})

    if opts.playlist and opts.items:
        params["playlist_items"] = format_ranges(opts.items)

    params["postprocessors"] = postprocessors
    return params


def fetch_playlist(url):
    """Returns (title, [(index, title, duration_seconds), ...]) without downloading."""
    params = {
        "extract_flat": "in_playlist",
        "noplaylist": False,
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": JS_RUNTIMES,
    }
    with yt_dlp.YoutubeDL(params) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = info.get("entries")
    if entries is None:
        raise NotAPlaylist()
    items = [(i, e.get("title") or "?", e.get("duration")) for i, e in enumerate(entries, 1) if e]
    return info.get("title") or "Playlist", items


class Downloader:
    """Runs a download and reports through `emit(kind, *args)`:

    ("item", index, total, title)       a new playlist item started
    ("progress", percent, title, speed, eta)
    ("processing",)                      merging / converting
    ("file", path)                       a file is finished
    ("skipped",)                         file already existed
    ("warning", message) / ("error", message)
    """

    def __init__(self, emit):
        self.emit = emit
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    def run(self, url, opts):
        """Blocking. Returns (finished_files, error_count, cancelled)."""
        emit = self.emit
        files, errors = [], []
        last_item = [None]

        def on_progress(d):
            if self.cancel_event.is_set():
                raise Cancelled()
            info = d.get("info_dict", {})
            title = info.get("title", "")
            # with playlist_items, n_entries is the selection size and autonumber the position in it
            index, total = info.get("playlist_autonumber"), info.get("n_entries")
            if index and total and last_item[0] != index:
                last_item[0] = index
                emit("item", index, total, title)
            if d["status"] == "downloading":
                done = d.get("downloaded_bytes") or 0
                size = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                emit("progress", done / size * 100 if size else 0, title,
                     (d.get("_speed_str") or "").strip(), (d.get("_eta_str") or "").strip())
            elif d["status"] == "finished":
                emit("processing")

        def on_postprocess(d):
            if d["status"] == "finished" and d.get("postprocessor") == "MoveFiles":
                path = d["info_dict"].get("filepath", "")
                files.append(path)
                emit("file", path)

        class Logger:
            def debug(self, msg):
                if "has already been downloaded" in msg:
                    emit("skipped")

            def info(self, msg):
                pass

            def warning(self, msg):
                if "JavaScript runtime" not in msg:
                    emit("warning", msg)

            def error(self, msg):
                errors.append(msg)
                emit("error", msg)

        params = build_params(opts)
        params.update(progress_hooks=[on_progress], postprocessor_hooks=[on_postprocess],
                      logger=Logger())
        try:
            os.makedirs(opts.folder, exist_ok=True)
            with yt_dlp.YoutubeDL(params) as ydl:
                ydl.download([url])
        except Cancelled:
            pass
        except Exception as e:  # yt-dlp raises DownloadError etc.; report and finish cleanly
            errors.append(str(e))
            emit("error", str(e))
        return files, len(errors), self.cancel_event.is_set()
