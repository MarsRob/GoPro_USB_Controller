"""
GoPro Hero 13 USB Controller
Requirements:
    pip install open-gopro opencv-python

Usage:
    1. On the GoPro: swipe down > Connections > USB Connection > GoPro Connect
    2. Plug in via USB-C
    3. Run: python gopro_controller.py
"""

import asyncio
import json
import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import urllib.request

from open_gopro import WiredGoPro
from open_gopro.models import constants as _c

# ---------------------------------------------------------------------------
# Resolve enum classes defensively
# ---------------------------------------------------------------------------
def _resolve(module, *names):
    for name in names:
        obj = getattr(module, name, None)
        if obj is not None:
            return obj
    available = [x for x in dir(module) if not x.startswith("_")]
    raise AttributeError(f"None of {names} found in {module}.\nAvailable: {available}")

_settings       = _c.settings
VideoResolution = _resolve(_settings, "VideoResolution")
VideoFPS        = _resolve(_settings, "FrameRate", "VideoFramesPerSecond", "FramesPerSecond", "VideoFPS")
VideoLens       = _resolve(_settings, "VideoLens")
Toggle          = _resolve(_c, "Toggle")

R = VideoResolution
F = VideoFPS

# ---------------------------------------------------------------------------
# Slow-motion presets
# Scientific default: 1080·240fps — highest frame rate for transient capture
# ---------------------------------------------------------------------------
SLOMO_PRESETS = [
    ("1080 · 240fps  ★ scientific", R.NUM_1080, F.NUM_240_0),  # DEFAULT
    ("2.7K · 240fps",               R.NUM_2_7K, F.NUM_240_0),
    ("4K   · 120fps",               R.NUM_4K,   F.NUM_120_0),
    ("1080 · 120fps",               R.NUM_1080, F.NUM_120_0),
    ("1080 · 60fps",                R.NUM_1080, F.NUM_60_0),
    ("720  · 240fps",               R.NUM_720,  F.NUM_240_0),
]

FOV_OPTIONS = {
    "Linear  ★ scientific":      VideoLens.LINEAR,           # DEFAULT — no distortion
    "Wide":                       VideoLens.WIDE,
    "Narrow":                     VideoLens.NARROW,
    "SuperView":                  VideoLens.SUPERVIEW,
    "Max SuperView":              VideoLens.MAX_SUPERVIEW,
    "Linear + Horizon Leveling":  VideoLens.LINEAR_HORIZON_LEVELING,
    "HyperView":                  VideoLens.HYPERVIEW,
    "Linear Horizon Lock":        VideoLens.LINEAR_HORIZON_LOCK,
    "Max HyperView":              VideoLens.MAX_HYPERVIEW,
    "Ultra SuperView":            VideoLens.ULTRA_SUPERVIEW,
    "Ultra Wide":                 VideoLens.ULTRA_WIDE,
    "Ultra Linear":               VideoLens.ULTRA_LINEAR,
    "Ultra HyperView":            VideoLens.ULTRA_HYPERVIEW,
}

KEEPALIVE_INTERVAL = 3.0

# Preview player constants
PREVIEW_W = 480
PREVIEW_H = 270
PREVIEW_FPS_DEFAULT = 30   # playback fps in GUI (not capture fps)


# ---------------------------------------------------------------------------
# Async worker
# ---------------------------------------------------------------------------
class GoProWorker:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.gopro: WiredGoPro | None = None
        self._ka_task = None
        self._base_url = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def _connect(self):
        self.gopro = WiredGoPro()
        await self.gopro.open()
        try:
            self._base_url = str(self.gopro._http_interface._base_url).rstrip("/")
        except Exception:
            self._base_url = "http://172.29.187.51:8080"
        self._ka_task = self.loop.create_task(self._keepalive())
        return True

    async def _disconnect(self):
        if self._ka_task:
            self._ka_task.cancel()
        if self.gopro:
            await self.gopro.close()
            self.gopro = None

    async def _keepalive(self):
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            try:
                await self.gopro.http_command.set_keep_alive()
            except Exception:
                pass

    async def _shutter(self, enable: bool):
        await self.gopro.http_command.set_shutter(
            shutter=Toggle.ENABLE if enable else Toggle.DISABLE
        )

    async def _apply_settings(self, res, fps, lens):
        await self.gopro.http_setting.video_resolution.set(res)
        await self.gopro.http_setting.frame_rate.set(fps)
        await self.gopro.http_setting.video_lens.set(lens)

    async def _get_status(self):
        state = (await self.gopro.http_command.get_camera_state()).data
        battery  = state.statuses.get(1, "?")
        encoding = state.statuses.get(10, False)
        return battery, encoding

    async def _get_media_list(self):
        url = f"{self._base_url}/gopro/media/list"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        files = []
        for folder in data.get("media", []):
            d = folder.get("d", "")
            for f in folder.get("fs", []):
                name = f.get("n", "")
                size = int(f.get("s", 0)) // 1024
                files.append((d, name, size))
        return files

    async def _download_file(self, directory, filename, dest_path, progress_cb):
        url = f"{self._base_url}/videos/DCIM/{directory}/{filename}"
        with urllib.request.urlopen(url, timeout=60) as r:
            total = int(r.headers.get("Content-Length", 0))
            downloaded = 0
            with open(dest_path, "wb") as f:
                while True:
                    buf = r.read(65536)
                    if not buf:
                        break
                    f.write(buf)
                    downloaded += len(buf)
                    if total > 0:
                        progress_cb(int(downloaded / total * 100))
        progress_cb(100)


# ---------------------------------------------------------------------------
# Inline video player (runs on a background thread, pushes frames to canvas)
# ---------------------------------------------------------------------------
class VideoPlayer:
    def __init__(self, canvas, log_fn):
        self.canvas   = canvas
        self.log      = log_fn
        self._active  = False
        self._path    = None
        self._thread  = None
        self._ph      = None   # keep PhotoImage reference alive
        self._paused  = False
        self._seek_to = None   # frame index to seek to
        self.total_frames = 0
        self.current_frame = 0
        self._on_frame_cb = None  # called with (current, total)

    def play(self, path, on_frame_cb=None):
        self.stop()
        self._path       = path
        self._active     = True
        self._paused     = False
        self._on_frame_cb = on_frame_cb
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def stop(self):
        self._active = False
        if self._thread:
            self._thread.join(timeout=2)
        self.canvas.delete("all")

    def pause_resume(self):
        self._paused = not self._paused

    def seek(self, frame_idx):
        self._seek_to = int(frame_idx)

    def _reader(self):
        try:
            import cv2
        except ImportError:
            self.canvas.after(0, lambda: self.log(
                "Install OpenCV for preview:  pip install opencv-python"))
            return

        cap = cv2.VideoCapture(self._path)
        if not cap.isOpened():
            self.canvas.after(0, lambda: self.log(f"Cannot open: {self._path}"))
            return

        self.total_frames  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        native_fps         = cap.get(cv2.CAP_PROP_FPS) or 30
        delay              = 1.0 / PREVIEW_FPS_DEFAULT

        import time

        def _render_frame(frame, pos):
            """Push one frame to the canvas on the main thread."""
            try:
                from PIL import Image, ImageTk
                frame_rgb = frame[:, :, ::-1]
                img = Image.fromarray(frame_rgb).resize(
                    (PREVIEW_W, PREVIEW_H), Image.LANCZOS)
                ph = ImageTk.PhotoImage(img)
                def _draw(ph=ph, cf=pos, tf=self.total_frames):
                    self._ph = ph
                    self.canvas.create_image(0, 0, anchor="nw", image=ph)
                    if self._on_frame_cb:
                        self._on_frame_cb(cf, tf)
                self.canvas.after(0, _draw)
            except Exception:
                pass

        while self._active:
            # ── seek: runs even while paused, then renders the new frame ──
            if self._seek_to is not None:
                target = self._seek_to
                self._seek_to = None
                cap.set(cv2.CAP_PROP_POS_FRAMES, target)
                ret, frame = cap.read()
                if ret:
                    self.current_frame = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
                    _render_frame(frame, self.current_frame)
                continue  # go back to top; if still paused we'll sit there

            if self._paused:
                time.sleep(0.02)
                continue

            ret, frame = cap.read()
            if not ret:
                self._active = False
                break

            self.current_frame = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
            _render_frame(frame, self.current_frame)
            time.sleep(delay)

        cap.release()
        self.canvas.after(0, lambda: self.log("Playback finished."))


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class GoProApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GoPro Hero 13 – USB Controller")
        self.resizable(True, True)
        self.configure(bg="#1e1e1e")
        self.minsize(520, 560)

        self.worker      = GoProWorker()
        self._connected  = False
        self._recording  = False
        self._media_items = []

        self._build_ui()
        self.player = VideoPlayer(self.canvas, self._log)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        PAD  = dict(padx=10, pady=6)
        DARK = "#1e1e1e"
        TEXT = "#f0f0f0"

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame",        background=DARK)
        style.configure("TLabel",        background=DARK, foreground=TEXT)
        style.configure("TButton",       background="#333", foreground=TEXT)
        style.configure("TCombobox",     fieldbackground="#333", background="#333",
                        foreground=TEXT, selectbackground="#444")
        style.configure("TNotebook",     background=DARK)
        style.configure("TNotebook.Tab", background="#333", foreground=TEXT, padding=[10, 4])
        style.map("TButton",       background=[("active", "#444")])
        style.map("TNotebook.Tab", background=[("selected", "#555")])
        style.configure("Treeview",         background="#222", foreground=TEXT,
                        fieldbackground="#222", rowheight=22)
        style.configure("Treeview.Heading", background="#333", foreground=TEXT)
        style.map("Treeview", background=[("selected", "#444")])
        style.configure("TScale", background=DARK)
        style.configure("TLabelframe",       background=DARK, foreground=TEXT,
                        bordercolor="#555")
        style.configure("TLabelframe.Label", background=DARK, foreground=TEXT)
        style.configure("TSpinbox", fieldbackground="#333", background="#333",
                        foreground=TEXT, arrowcolor=TEXT)
        # Force the combobox dropdown list colours via the option database
        self.option_add("*TCombobox*Listbox.background", "#333")
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", "#555")
        self.option_add("*TCombobox*Listbox.selectForeground", TEXT)
        style.map("TCombobox",
                  fieldbackground=[("readonly", "#333")],
                  foreground=[("readonly", TEXT)],
                  selectbackground=[("readonly", "#333")],
                  selectforeground=[("readonly", TEXT)],
                  background=[("readonly", "#333")])

        # ── top bar ──────────────────────────────────────────────────────────
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=(10, 0))

        self.btn_connect = ttk.Button(top, text="Connect (USB)",
                                      command=self._toggle_connect)
        self.btn_connect.pack(side="left")

        self.lbl_status = ttk.Label(top, text="● Disconnected", foreground="#888")
        self.lbl_status.pack(side="left", padx=10)

        self.lbl_battery = ttk.Label(top, text="🔋 –")
        self.lbl_battery.pack(side="right")

        # ── notebook ─────────────────────────────────────────────────────────
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=8)

        self._build_control_tab(nb, PAD)
        self._build_media_tab(nb)

        # ── log ──────────────────────────────────────────────────────────────
        lf = ttk.LabelFrame(self, text=" Log ", padding=4)
        lf.pack(fill="x", padx=10, pady=(0, 10))
        self.log_box = tk.Text(lf, height=4, bg="#111", fg="#aaa",
                               font=("Courier", 9), state="disabled", relief="flat")
        self.log_box.pack(fill="both", expand=True)

    # ── Control tab ──────────────────────────────────────────────────────────
    def _build_control_tab(self, nb, PAD):
        tab = ttk.Frame(nb)
        nb.add(tab, text="  Control  ")

        sf = ttk.LabelFrame(tab, text=" Video Settings ", padding=8)
        sf.pack(fill="x", padx=10, pady=8)

        ttk.Label(sf, text="Slow-Mo Preset:").grid(row=0, column=0, sticky="w", **PAD)
        self.var_preset = tk.StringVar(value=SLOMO_PRESETS[0][0])
        ttk.Combobox(sf, textvariable=self.var_preset, width=26, state="readonly",
                     values=[p[0] for p in SLOMO_PRESETS]).grid(
                     row=0, column=1, sticky="w", **PAD)

        ttk.Label(sf, text="Field of View:").grid(row=1, column=0, sticky="w", **PAD)
        self.var_fov = tk.StringVar(value=list(FOV_OPTIONS.keys())[0])
        ttk.Combobox(sf, textvariable=self.var_fov, width=26, state="readonly",
                     values=list(FOV_OPTIONS.keys())).grid(
                     row=1, column=1, sticky="w", **PAD)

        self.btn_apply = ttk.Button(sf, text="Apply Settings",
                                    command=self._apply_settings, state="disabled")
        self.btn_apply.grid(row=2, column=0, columnspan=2, pady=(4, 0))

        rf = ttk.Frame(tab)
        rf.pack(padx=10, pady=14)
        self.btn_record = tk.Button(rf, text="⏺  Start Recording",
                                    bg="#cc0000", fg="white",
                                    font=("", 13, "bold"), width=24,
                                    relief="flat", command=self._toggle_record,
                                    state="disabled")
        self.btn_record.pack()

    # ── Media tab ────────────────────────────────────────────────────────────
    def _build_media_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="  SD Card Files  ")

        # ── left pane: file list ──────────────────────────────────────────
        left = ttk.Frame(tab)
        left.pack(side="left", fill="both", expand=True, padx=(10, 4), pady=8)

        tb = ttk.Frame(left)
        tb.pack(fill="x", pady=(0, 4))

        self.btn_refresh = ttk.Button(tb, text="⟳  Refresh",
                                      command=self._refresh_media, state="disabled")
        self.btn_refresh.pack(side="left")

        self.btn_download = ttk.Button(tb, text="⬇  Download",
                                       command=self._download_selected, state="disabled")
        self.btn_download.pack(side="left", padx=6)

        self.lbl_file_count = ttk.Label(tb, text="")
        self.lbl_file_count.pack(side="right")

        cols = ("filename", "size")
        self.tree = ttk.Treeview(left, columns=cols, show="headings",
                                 selectmode="extended", height=12)
        self.tree.heading("filename", text="Filename")
        self.tree.heading("size",     text="Size")
        self.tree.column("filename",  width=200, anchor="w")
        self.tree.column("size",      width=80,  anchor="e")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")

        self.progress_var = tk.IntVar(value=0)
        ttk.Progressbar(left, variable=self.progress_var,
                        maximum=100).pack(fill="x", pady=(6, 0))

        # ── right pane: video preview ─────────────────────────────────────
        right = ttk.Frame(tab)
        right.pack(side="left", fill="both", padx=(4, 10), pady=8)

        ttk.Label(right, text="Preview").pack()

        self.canvas = tk.Canvas(right, width=PREVIEW_W, height=PREVIEW_H,
                                bg="#111", highlightthickness=1,
                                highlightbackground="#444")
        self.canvas.pack()

        # playback controls
        ctrl = ttk.Frame(right)
        ctrl.pack(fill="x", pady=(4, 0))

        self.btn_play = ttk.Button(ctrl, text="▶  Play",
                                   command=self._play_selected, state="disabled")
        self.btn_play.pack(side="left")

        self.btn_pause = ttk.Button(ctrl, text="⏸  Pause",
                                    command=self._pause_resume, state="disabled")
        self.btn_pause.pack(side="left", padx=4)

        self.btn_stop_play = ttk.Button(ctrl, text="⏹  Stop",
                                        command=self._stop_play, state="disabled")
        self.btn_stop_play.pack(side="left")

        self.lbl_frame = ttk.Label(right, text="", font=("Courier", 8))
        self.lbl_frame.pack(pady=(4, 0))

        self.scrub_var = tk.DoubleVar(value=0)
        self.scrub = ttk.Scale(right, from_=0, to=1000,
                               variable=self.scrub_var,
                               orient="horizontal", length=PREVIEW_W,
                               command=self._on_scrub)
        self.scrub.pack(pady=(2, 0))

        self.lbl_filepath = ttk.Label(right, text="No file selected",
                                      font=("", 8), foreground="#888",
                                      wraplength=PREVIEW_W)
        self.lbl_filepath.pack(pady=(4, 0))

        # also allow opening local files not on SD card
        ttk.Button(right, text="Open local file…",
                   command=self._open_local_file).pack(pady=(6, 0))

    # ── helpers ──────────────────────────────────────────────────────────────
    def _log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _set_connected(self, val: bool):
        self._connected = val
        if val:
            self.lbl_status.configure(text="● Connected", foreground="#44dd44")
            self.btn_connect.configure(text="Disconnect")
            self.btn_apply.configure(state="normal")
            self.btn_record.configure(state="normal")
            self.btn_refresh.configure(state="normal")
            self._poll_status()
        else:
            self.lbl_status.configure(text="● Disconnected", foreground="#888")
            self.btn_connect.configure(text="Connect (USB)")
            self.btn_apply.configure(state="disabled")
            self.btn_record.configure(state="disabled")
            self.btn_refresh.configure(state="disabled")
            self.btn_download.configure(state="disabled")
            self.lbl_battery.configure(text="🔋 –")

    # ── connection ───────────────────────────────────────────────────────────
    def _toggle_connect(self):
        if not self._connected:
            self._log("Connecting via USB…")
            self.btn_connect.configure(state="disabled")
            fut = self.worker.submit(self.worker._connect())
            self.after(200, lambda: self._check_connect(fut))
        else:
            fut = self.worker.submit(self.worker._disconnect())
            fut.result(timeout=5)
            self._set_connected(False)
            self._log("Disconnected.")

    def _check_connect(self, fut):
        if not fut.done():
            self.after(200, lambda: self._check_connect(fut))
            return
        self.btn_connect.configure(state="normal")
        try:
            fut.result()
            self._set_connected(True)
            self._log("Connected! Camera ready.")
        except Exception as e:
            self._log(f"Connection failed: {e}")
            messagebox.showerror("Connection Error",
                                 f"Could not connect to GoPro:\n{e}\n\n"
                                 "Make sure:\n"
                                 "• Camera is ON\n"
                                 "• USB Connection set to 'GoPro Connect'\n"
                                 "• USB cable is data-capable")

    # ── settings ─────────────────────────────────────────────────────────────
    def _apply_settings(self):
        idx = [p[0] for p in SLOMO_PRESETS].index(self.var_preset.get())
        _, res, fps = SLOMO_PRESETS[idx]
        lens = FOV_OPTIONS[self.var_fov.get()]
        self._log(f"Applying: {self.var_preset.get()} | FOV: {self.var_fov.get()}")
        fut = self.worker.submit(self.worker._apply_settings(res, fps, lens))
        self.after(0, lambda: self._wait_for_settings(fut))

    def _wait_for_settings(self, fut, attempts=0):
        if not fut.done():
            if attempts < 20:
                self.after(100, lambda: self._wait_for_settings(fut, attempts + 1))
            else:
                self._log("Settings timed out.")
            return
        if fut.exception():
            self._log(f"Settings error: {fut.exception()}")
        else:
            self._log("Settings applied.")

    # ── record ───────────────────────────────────────────────────────────────
    def _toggle_record(self):
        enable = not self._recording
        self.worker.submit(self.worker._shutter(enable))
        self._recording = enable
        if enable:
            self.btn_record.configure(text="⏹  Stop Recording", bg="#333")
            self._log("Recording started.")
        else:
            self.btn_record.configure(text="⏺  Start Recording", bg="#cc0000")
            self._log("Recording stopped. File saved to SD card.")

    # ── media browser ────────────────────────────────────────────────────────
    def _refresh_media(self):
        self._log("Reading SD card…")
        self.btn_refresh.configure(state="disabled")
        self.tree.delete(*self.tree.get_children())
        self._media_items = []
        fut = self.worker.submit(self.worker._get_media_list())
        self.after(200, lambda: self._check_media(fut))

    def _check_media(self, fut):
        if not fut.done():
            self.after(200, lambda: self._check_media(fut))
            return
        self.btn_refresh.configure(state="normal")
        try:
            items = fut.result()
            self._media_items = items
            video_items = [(d, n, s) for d, n, s in items
                           if n.upper().endswith((".MP4", ".LRV", ".THM"))]
            for d, name, size_kb in video_items:
                size_str = (f"{size_kb/1024:.1f} MB" if size_kb > 1024
                            else f"{size_kb} KB")
                self.tree.insert("", "end", values=(name, size_str), tags=(d,))
            self.lbl_file_count.configure(text=f"{len(video_items)} files")
            self._log(f"Found {len(video_items)} video files on SD card.")
            if video_items:
                self.btn_download.configure(state="normal")
        except Exception as e:
            self._log(f"Media list error: {e}")

    def _on_tree_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        # Stop any currently playing video immediately
        if self.player._active:
            self._stop_play()
        filename = self.tree.item(sel[0], "values")[0]
        path = self._get_selected_path()
        if path:
            self.btn_play.configure(state="normal",
                                    text="▶  Play")
            self.lbl_filepath.configure(text=f"{filename}  ✔ downloaded",
                                        foreground="#44dd44")
        else:
            self.btn_play.configure(state="disabled",
                                    text="▶  Play  (download first)")
            self.lbl_filepath.configure(
                text=f"{filename}  ✖ not downloaded — use ⬇ Download",
                foreground="#dd8844")

    def _download_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("No selection", "Select files to download.")
            return
        dest_dir = filedialog.askdirectory(title="Choose download folder")
        if not dest_dir:
            return
        to_download = []
        for iid in selected:
            vals = self.tree.item(iid, "values")
            tags = self.tree.item(iid, "tags")
            to_download.append((tags[0] if tags else "100GOPRO", vals[0]))
        self._log(f"Downloading {len(to_download)} file(s)…")
        self.btn_download.configure(state="disabled")
        self.btn_refresh.configure(state="disabled")
        self._dl_dest = dest_dir
        self._download_queue(to_download, dest_dir, 0)

    def _download_queue(self, queue, dest_dir, idx):
        if idx >= len(queue):
            self._log("All downloads complete.")
            self.btn_download.configure(state="normal")
            self.btn_refresh.configure(state="normal")
            self.progress_var.set(0)
            return
        directory, filename = queue[idx]
        dest_path = os.path.join(dest_dir, filename)
        self._log(f"Downloading {filename}…")
        self.progress_var.set(0)
        fut = self.worker.submit(
            self.worker._download_file(
                directory, filename, dest_path,
                lambda p: self.after(0, lambda v=p: self.progress_var.set(v))
            )
        )
        self.after(200, lambda: self._check_download(fut, queue, dest_dir, idx))

    def _check_download(self, fut, queue, dest_dir, idx):
        if not fut.done():
            self.after(200, lambda: self._check_download(fut, queue, dest_dir, idx))
            return
        _, filename = queue[idx]
        if fut.exception():
            self._log(f"Download failed for {filename}: {fut.exception()}")
        else:
            self._log(f"Saved: {filename}")
            # If this file is currently selected, enable Play now
            self._on_tree_select()
        self._download_queue(queue, dest_dir, idx + 1)

    # ── video playback ───────────────────────────────────────────────────────
    def _get_selected_path(self):
        """Return local path if file has been downloaded, else None."""
        sel = self.tree.selection()
        if not sel:
            return None
        filename = self.tree.item(sel[0], "values")[0]
        # check common download locations
        for folder in [os.path.expanduser("~\\Downloads"),
                       os.path.expanduser("~/Downloads"),
                       getattr(self, "_dl_dest", "")]:
            path = os.path.join(folder, filename)
            if os.path.exists(path):
                return path
        return None

    def _play_selected(self):
        path = self._get_selected_path()
        if not path:
            sel = self.tree.selection()
            filename = self.tree.item(sel[0], "values")[0] if sel else "?"
            messagebox.showinfo(
                "File not found locally",
                f"{filename} hasn't been downloaded yet.\n\n"
                "Download it first, then press Play.")
            return
        self._start_playback(path)

    def _open_local_file(self):
        path = filedialog.askopenfilename(
            title="Open video file",
            filetypes=[("MP4 files", "*.mp4 *.MP4"), ("All files", "*.*")])
        if path:
            self._start_playback(path)

    def _start_playback(self, path):
        # Always stop any existing playback before starting new one
        self.player.stop()
        self._log(f"Playing: {os.path.basename(path)}")
        self.lbl_filepath.configure(text=os.path.basename(path),
                                    foreground="#44dd44")
        self.btn_pause.configure(state="normal", text="⏸  Pause")
        self.btn_stop_play.configure(state="normal")
        self.scrub_var.set(0)

        def on_frame(current, total):
            self.lbl_frame.configure(text=f"Frame {current} / {total}")
            if total > 0:
                self.scrub.configure(to=total)
                # update scrub without triggering seek callback
                self._scrub_updating = True
                self.scrub_var.set(current)
                self._scrub_updating = False

        self.player.play(path, on_frame_cb=on_frame)

    def _pause_resume(self):
        self.player.pause_resume()
        paused = self.player._paused
        self.btn_pause.configure(text="▶  Resume" if paused else "⏸  Pause")

    def _stop_play(self):
        self.player.stop()
        self.btn_pause.configure(state="disabled", text="⏸  Pause")
        self.btn_stop_play.configure(state="disabled")
        self.lbl_frame.configure(text="")
        self.scrub_var.set(0)

    def _on_scrub(self, val):
        if getattr(self, "_scrub_updating", False):
            return
        # scrubbing pauses automatically
        if self.player._active and not self.player._paused:
            self.player.pause_resume()
            self.btn_pause.configure(text="▶  Resume")
        self.player.seek(int(float(val)))

    # ── status poll ──────────────────────────────────────────────────────────
    def _poll_status(self):
        if not self._connected:
            return
        fut = self.worker.submit(self.worker._get_status())
        self.after(300, lambda: self._update_status(fut))
        self.after(5000, self._poll_status)

    def _update_status(self, fut):
        if not fut.done() or fut.exception():
            return
        battery, encoding = fut.result()
        self.lbl_battery.configure(text=f"🔋 {battery}%")
        if encoding and not self._recording:
            self._recording = True
            self.btn_record.configure(text="⏹  Stop Recording", bg="#333")

    # ── close ────────────────────────────────────────────────────────────────
    def _on_close(self):
        self.player.stop()
        if self._connected:
            self.worker.submit(self.worker._disconnect())
        self.after(300, self.destroy)


if __name__ == "__main__":
    app = GoProApp()
    app.mainloop()