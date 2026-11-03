"""Main window."""
import ctypes
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

import sv_ttk
import yt_dlp.version

from . import APP_NAME, REPO_URL, __version__, settings
from .downloader import QUALITIES, Downloader, NotAPlaylist, Options, fetch_playlist
from .i18n import LANGUAGES, set_language, t
from .picker import PickerWindow
from .utils import format_ranges

try:
    import darkdetect
except ImportError:
    darkdetect = None

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
IS_WINDOWS = sys.platform == "win32"

LOG_COLORS = {
    #          background  text       ok         warning    error      dim
    "dark": ("#1c1c1c", "#e4e4e4", "#6ccb5f", "#f0b429", "#ff6b6b", "#8a8a8a"),
    "light": ("#fbfbfb", "#1b1b1b", "#0f7b0f", "#9d5d00", "#c42b1c", "#6e6e6e"),
}


def quality_label(key):
    if key == "best":
        return t("q_best")
    if key in ("mp3", "m4a"):
        return t(f"q_{key}")
    return "2160p (4K)" if key == "2160" else f"{key}p"


def set_title_bar_dark(window, dark):
    """Windows 10/11: match the native title bar to the theme."""
    if not IS_WINDOWS:
        return
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
        # force a non-client repaint
        window.wm_attributes("-alpha", 0.99)
        window.wm_attributes("-alpha", 1.0)
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.prefs = settings.load()
        set_language(self.prefs["language"])

        self.title(APP_NAME)
        self.geometry(self.prefs["geometry"])
        self.minsize(640, 560)
        if IS_WINDOWS:
            self.iconbitmap(default=os.path.join(ASSETS, "icon.ico"))
        self.logo = self._load_logo()

        self.events = queue.Queue()
        self.downloader = None
        self.busy = False
        self.ffmpeg_warned = False
        self.log_lines = []  # (text, tag), kept so a language switch doesn't lose the log
        self.playlist_cache = None  # (url, title, items)
        self.selection = None  # chosen playlist indices for the current link, None = all

        p = self.prefs
        self.url = tk.StringVar()
        self.quality = tk.StringVar()
        self.folder = tk.StringVar(value=p["folder"])
        self.opt_playlist = tk.BooleanVar(value=p["playlist"])
        self.opt_mp4 = tk.BooleanVar(value=p["mp4"])
        self.opt_subs = tk.BooleanVar(value=p["subtitles"])
        self.opt_embed = tk.BooleanVar(value=p["embed"])
        self.language = tk.StringVar(value=LANGUAGES[p["language"]])
        self.selection_text = tk.StringVar()
        self.now_title = tk.StringVar()
        self.counter = tk.StringVar()
        self.status = tk.StringVar()
        self.url.trace_add("write", lambda *_: self._reset_selection())

        dark = p["theme"] == "dark" if p["theme"] else (darkdetect and darkdetect.isDark())
        self.dark = bool(dark)
        sv_ttk.set_theme("dark" if self.dark else "light")

        self.body = None
        self._build()
        self._apply_theme()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_events)

    # ------------------------------------------------------------------ UI
    def _load_logo(self):
        try:
            img = tk.PhotoImage(file=os.path.join(ASSETS, "icon-256.png"))
            target = 44 * float(self.tk.call("tk", "scaling")) / 1.333
            return img.subsample(max(1, round(256 / target)))
        except tk.TclError:
            return None

    def _build(self):
        if self.body:
            self.body.destroy()
        self.status.set(t("ready"))
        quality_key = self._quality_key() if self.quality.get() else self.prefs["quality"]
        self.quality_map = {quality_label(k): k for k in QUALITIES}
        self.quality.set(quality_label(quality_key))

        body = self.body = ttk.Frame(self, padding=(20, 16, 20, 12))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(4, weight=1)

        # header
        header = ttk.Frame(body)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        if self.logo:
            ttk.Label(header, image=self.logo).pack(side="left", padx=(0, 12))
        titles = ttk.Frame(header)
        titles.pack(side="left")
        ttk.Label(titles, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text=t("subtitle"), style="Hint.TLabel").pack(anchor="w")
        self.theme_btn = ttk.Button(header, width=3, command=self._toggle_theme)
        self.theme_btn.pack(side="right")
        lang = ttk.Combobox(header, textvariable=self.language, values=list(LANGUAGES.values()),
                            state="readonly", width=9)
        lang.pack(side="right", padx=8)
        lang.bind("<<ComboboxSelected>>", self._change_language)

        # input card
        card = ttk.Frame(body, style="Card.TFrame", padding=16)
        card.grid(row=1, column=0, sticky="ew")
        card.columnconfigure(1, weight=1)

        ttk.Label(card, text=t("link")).grid(row=0, column=0, sticky="w", padx=(0, 12))
        entry = ttk.Entry(card, textvariable=self.url)
        entry.grid(row=0, column=1, sticky="ew")
        entry.bind("<Return>", lambda e: self._start())
        entry.focus()
        ttk.Button(card, text=t("paste"), command=self._paste).grid(row=0, column=2, padx=(8, 0))
        ttk.Label(card, text=t("link_hint"), style="Hint.TLabel").grid(
            row=1, column=1, sticky="w", pady=(2, 10))

        ttk.Label(card, text=t("quality")).grid(row=2, column=0, sticky="w", padx=(0, 12))
        ttk.Combobox(card, textvariable=self.quality, values=list(self.quality_map),
                     state="readonly", width=22).grid(row=2, column=1, sticky="w")

        ttk.Label(card, text=t("folder")).grid(row=3, column=0, sticky="w", padx=(0, 12), pady=(10, 0))
        ttk.Entry(card, textvariable=self.folder).grid(row=3, column=1, sticky="ew", pady=(10, 0))
        ttk.Button(card, text=t("browse"), command=self._browse).grid(
            row=3, column=2, padx=(8, 0), pady=(10, 0))

        switches = ttk.Frame(card)
        switches.grid(row=4, column=0, columnspan=3, sticky="w", pady=(14, 0))
        for i, (var, key) in enumerate(((self.opt_playlist, "opt_playlist"), (self.opt_mp4, "opt_mp4"),
                                        (self.opt_subs, "opt_subs"), (self.opt_embed, "opt_embed"))):
            ttk.Checkbutton(switches, text=t(key), variable=var, style="Switch.TCheckbutton").grid(
                row=i // 2, column=i % 2, sticky="w", padx=(0, 28), pady=2)

        # actions
        actions = ttk.Frame(body)
        actions.grid(row=2, column=0, sticky="ew", pady=14)
        self.download_btn = ttk.Button(actions, text=t("download"), style="Accent.TButton",
                                       width=14, command=self._start)
        self.download_btn.pack(side="left")
        self.cancel_btn = ttk.Button(actions, text=t("cancel"), command=self._cancel)
        self.cancel_btn.pack(side="left", padx=8)
        self.select_btn = ttk.Button(actions, text=t("select_videos"), command=self._open_picker)
        self.select_btn.pack(side="left")
        ttk.Label(actions, textvariable=self.selection_text, style="Accent.TLabel").pack(side="left", padx=10)
        ttk.Button(actions, text=t("open_folder"), command=self._open_folder).pack(side="right")

        # progress card
        prog = ttk.Frame(body, style="Card.TFrame", padding=(16, 12))
        prog.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        prog.columnconfigure(0, weight=1)
        ttk.Label(prog, textvariable=self.now_title, style="Strong.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(prog, textvariable=self.counter, style="Hint.TLabel").grid(row=0, column=1, sticky="e")
        self.bar = ttk.Progressbar(prog, mode="determinate", maximum=100)
        self.bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        self.total_bar = ttk.Progressbar(prog, mode="determinate")
        self.total_bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        self.total_bar.grid_remove()
        ttk.Label(prog, textvariable=self.status, style="Hint.TLabel").grid(
            row=3, column=0, columnspan=2, sticky="w")

        # log
        log_frame = ttk.Frame(body)
        log_frame.grid(row=4, column=0, sticky="nsew")
        self.log = tk.Text(log_frame, height=8, wrap="word", state="disabled", relief="flat",
                           borderwidth=0, padx=10, pady=8, font=("Consolas", 9),
                           highlightthickness=1)
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        for text, tag in self.log_lines:
            self._insert_log(text, tag)

        # footer
        footer = ttk.Frame(body)
        footer.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(footer, text=f"v{__version__}  ·  yt-dlp {yt_dlp.version.__version__}",
                  style="Hint.TLabel").pack(side="left")
        github = ttk.Label(footer, text="GitHub ↗", style="Link.TLabel", cursor="hand2")
        github.pack(side="right")
        github.bind("<Button-1>", lambda e: webbrowser.open(REPO_URL))
        self.update_btn = ttk.Label(footer, text=t("update"), style="Link.TLabel", cursor="hand2")
        self.update_btn.pack(side="right", padx=16)
        self.update_btn.bind("<Button-1>", lambda e: self._update_ytdlp())

        self._set_busy(self.busy)

    def _apply_theme(self):
        style = ttk.Style()
        bg, fg, ok, warn, err, dim = LOG_COLORS["dark" if self.dark else "light"]
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 20))
        style.configure("Hint.TLabel", foreground=dim)
        style.configure("Strong.TLabel", font=("Segoe UI Semibold", 10))
        accent = "#57c8ff" if self.dark else "#005fb8"
        style.configure("Accent.TLabel", foreground=accent, font=("Segoe UI Semibold", 10))
        style.configure("Link.TLabel", foreground=accent)
        self.log.configure(background=bg, foreground=fg, insertbackground=fg,
                           highlightbackground="#2b2b2b" if self.dark else "#e5e5e5",
                           highlightcolor="#2b2b2b" if self.dark else "#e5e5e5")
        self.log.tag_configure("ok", foreground=ok)
        self.log.tag_configure("warning", foreground=warn)
        self.log.tag_configure("error", foreground=err)
        self.log.tag_configure("dim", foreground=dim)
        self.log.tag_configure("head", font=("Consolas", 9, "bold"))
        self.theme_btn.configure(text="☀" if self.dark else "☾")
        set_title_bar_dark(self, self.dark)

    def _toggle_theme(self):
        self.dark = not self.dark
        self.prefs["theme"] = "dark" if self.dark else "light"
        sv_ttk.set_theme(self.prefs["theme"])
        self._apply_theme()

    def _change_language(self, _event=None):
        code = next(k for k, v in LANGUAGES.items() if v == self.language.get())
        if code == self.prefs["language"]:
            return
        quality_key = self._quality_key()
        self.prefs["language"] = code
        set_language(code)
        self.quality.set("")
        self.prefs["quality"] = quality_key
        self._build()
        self._apply_theme()
        if self.selection:
            self.selection_text.set(t("selected", n=len(self.selection), total=len(self.playlist_cache[2])))

    def _set_busy(self, busy):
        self.busy = busy
        normal = "disabled" if busy else "normal"
        self.download_btn.configure(state=normal)
        self.select_btn.configure(state=normal)
        self.cancel_btn.configure(state="normal" if busy and self.downloader else "disabled")

    # ------------------------------------------------------------- helpers
    def _quality_key(self):
        return self.quality_map.get(self.quality.get(), self.prefs["quality"])

    def _paste(self):
        try:
            self.url.set(self.clipboard_get().strip())
        except tk.TclError:
            pass

    def _browse(self):
        path = filedialog.askdirectory(initialdir=self.folder.get() or os.path.expanduser("~"))
        if path:
            self.folder.set(os.path.normpath(path))

    def _open_folder(self):
        path = self.folder.get()
        os.makedirs(path, exist_ok=True)
        if IS_WINDOWS:
            os.startfile(path)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])

    def _insert_log(self, text, tag=""):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def log_line(self, text, tag=""):
        self.log_lines.append((text, tag))
        self._insert_log(text, tag)

    def _reset_selection(self):
        self.selection = None
        self.selection_text.set("")

    def _save_prefs(self):
        self.prefs.update(
            quality=self._quality_key(), folder=self.folder.get(), playlist=self.opt_playlist.get(),
            mp4=self.opt_mp4.get(), subtitles=self.opt_subs.get(), embed=self.opt_embed.get())
        if self.state() == "normal":
            self.prefs["geometry"] = self.geometry().split("+")[0]
        settings.save(self.prefs)

    def _flash(self):
        """Blink the taskbar button when a download ends while the app is in the background."""
        if not IS_WINDOWS:
            return
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.winfo_id())
            if user32.GetForegroundWindow() != hwnd:
                user32.FlashWindow(hwnd, True)
        except Exception:
            pass

    # ---------------------------------------------------- playlist picker
    def _open_picker(self):
        if self.busy:
            return
        url = self.url.get().strip()
        if not url:
            messagebox.showwarning(t("no_link_title"), t("no_link"))
            return
        if self.playlist_cache and self.playlist_cache[0] == url:
            self._show_picker()
            return
        self._set_busy(True)
        self.status.set(t("fetching_list"))
        self.bar.configure(mode="indeterminate")
        self.bar.start(12)

        def work():
            try:
                self.events.put(("list_done", url, fetch_playlist(url), None))
            except NotAPlaylist:
                self.events.put(("list_done", url, None, t("not_playlist")))
            except Exception as e:
                self.events.put(("list_done", url, None, str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _show_picker(self):
        url, title, items = self.playlist_cache

        def on_ok(chosen):
            if self.url.get().strip() != url:
                return
            self.selection = None if len(chosen) == len(items) else chosen
            self.selection_text.set(t("selected", n=len(chosen), total=len(items)))
            self.opt_playlist.set(True)

        picker = PickerWindow(self, title, items, self.selection, on_ok)
        if IS_WINDOWS:
            picker.iconbitmap(os.path.join(ASSETS, "icon.ico"))
        set_title_bar_dark(picker, self.dark)

    # ------------------------------------------------------------ download
    def _start(self):
        if self.busy:
            return
        url = self.url.get().strip()
        if not url:
            messagebox.showwarning(t("no_link_title"), t("no_link"))
            return
        if not shutil.which("ffmpeg") and not self.ffmpeg_warned:
            self.ffmpeg_warned = True
            messagebox.showwarning(t("no_ffmpeg_title"), t("no_ffmpeg"))
        self._save_prefs()

        opts = Options(
            folder=self.folder.get(), quality=self._quality_key(), playlist=self.opt_playlist.get(),
            mp4=self.opt_mp4.get(), subtitles=self.opt_subs.get(), embed=self.opt_embed.get(),
            items=self.selection or [])

        self.downloader = Downloader(lambda *e: self.events.put(e))
        self._set_busy(True)
        self.bar.configure(value=0)
        self.total_bar.grid_remove()
        self.now_title.set("")
        self.counter.set("")
        self.status.set(t("starting"))
        self.log_line(f"▶ {url}", "head")
        self.log_line(f"  {self.quality.get()}  →  {opts.folder}", "dim")
        if opts.playlist and opts.items:
            self.log_line("  " + t("log_selected", n=len(opts.items), ranges=format_ranges(opts.items)), "dim")

        def work(downloader=self.downloader):
            self.events.put(("finished", *downloader.run(url, opts)))

        threading.Thread(target=work, daemon=True).start()

    def _cancel(self):
        if self.downloader:
            self.downloader.cancel()
            self.status.set(t("cancelling"))

    # ------------------------------------------------------------- update
    def _update_ytdlp(self):
        if self.busy:
            return
        if getattr(sys, "frozen", False):
            if messagebox.askokcancel(APP_NAME, t("update_frozen")):
                webbrowser.open(REPO_URL + "/releases/latest")
            return
        self._set_busy(True)
        self.status.set(t("updating"))
        self.bar.configure(mode="indeterminate")
        self.bar.start(12)

        def work():
            flags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
            python = sys.executable.replace("pythonw.exe", "python.exe")
            try:
                r = subprocess.run([python, "-m", "pip", "install", "-U", "yt-dlp[default]"],
                                   capture_output=True, text=True, creationflags=flags)
                if r.returncode:
                    raise RuntimeError((r.stderr or r.stdout).strip()[-800:])
                v = subprocess.run([python, "-c", "import yt_dlp.version as v; print(v.__version__)"],
                                   capture_output=True, text=True, creationflags=flags).stdout.strip()
                self.events.put(("update_done", v, None))
            except Exception as e:
                self.events.put(("update_done", None, str(e)))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------- events
    def _stop_spinner(self):
        self.bar.stop()
        self.bar.configure(mode="determinate", value=0)

    def _poll_events(self):
        try:
            while True:
                self._handle(*self.events.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _handle(self, kind, *args):
        if kind == "item":
            index, total, title = args
            self.counter.set(t("item", i=index, n=total))
            if total > 1:
                self.total_bar.grid()
                self.total_bar.configure(maximum=total, value=index - 1)
            self.now_title.set(title[:80])
        elif kind == "progress":
            pct, title, speed, eta = args
            self.bar.configure(value=pct)
            self.now_title.set(title[:80])
            self.status.set(t("progress", pct=pct, speed=speed or "–", eta=eta or "–"))
        elif kind == "processing":
            self.bar.configure(value=100)
            self.status.set(t("processing"))
        elif kind == "file":
            self.log_line("✔ " + os.path.basename(args[0]), "ok")
        elif kind == "skipped":
            self.log_line("  " + t("log_skipped"), "dim")
        elif kind == "warning":
            self.log_line("⚠ " + args[0], "warning")
        elif kind == "error":
            self.log_line("✖ " + args[0], "error")
        elif kind == "finished":
            files, errors, cancelled = args
            self.downloader = None
            self._set_busy(False)
            if cancelled:
                self.status.set(t("cancelled"))
                self.log_line("■ " + t("cancelled"), "dim")
            else:
                if self.total_bar.winfo_ismapped():
                    self.total_bar.configure(value=self.total_bar["maximum"])
                msg = t("done_errors", n=len(files), e=errors) if errors else t("done", n=len(files))
                self.status.set(msg)
                self.log_line("■ " + msg, "error" if errors and not files else "head")
            self.log_line("")
            self._flash()
        elif kind == "list_done":
            url, result, error = args
            self._stop_spinner()
            self._set_busy(False)
            if error:
                self.status.set(t("ready"))
                messagebox.showwarning(t("list_failed"), error)
            else:
                self.playlist_cache = (url, *result)
                self.status.set(t("list_found", n=len(result[1])))
                if self.url.get().strip() == url:
                    self._show_picker()
        elif kind == "update_done":
            version, error = args
            self._stop_spinner()
            self._set_busy(False)
            self.status.set(t("ready"))
            if error:
                messagebox.showerror(APP_NAME, t("update_failed", e=error))
            elif version == yt_dlp.version.__version__:
                messagebox.showinfo(APP_NAME, t("update_same", v=version))
            else:
                messagebox.showinfo(APP_NAME, t("update_ok", v=version))

    def _on_close(self):
        if self.downloader and not messagebox.askyesno(t("confirm_exit_title"), t("confirm_exit")):
            return
        if self.downloader:
            self.downloader.cancel()
        self._save_prefs()
        self.destroy()


def main():
    if IS_WINDOWS:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            # own taskbar icon/grouping instead of python's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Clyrex9.YTIndirici")
        except Exception:
            pass
    App().mainloop()


if __name__ == "__main__":
    main()
