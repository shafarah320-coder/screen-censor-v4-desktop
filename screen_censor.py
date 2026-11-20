import os
import sys
import time
import threading
import tempfile
import ctypes
from ctypes import wintypes
import tkinter as tk
from tkinter import messagebox

import cv2
import numpy as np
import mss
from PIL import Image, ImageTk, ImageFilter, ImageDraw
from nudenet import NudeDetector

APP_TITLE = "Screen Censor 4.0"
BREAST_LABEL = "FEMALE_BREAST_EXPOSED"
BREAST_THRESHOLD = 0.28
DETECTION_INTERVAL = 0.30      # AI detection is hardware-dependent, not 30 FPS.
OVERLAY_REFRESH_MS = 33        # Overlay redraw target: ~30 FPS.
BOX_PADDING = 0.22
BLUR_RADIUS = 15
TILE_ROWS = 2
TILE_COLS = 2
TILE_OVERLAP = 0.18

IS_WINDOWS = sys.platform == "win32"
WDA_EXCLUDEFROMCAPTURE = 0x00000011
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
VK_F12 = 0x7B
WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001

class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND), ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD), ("pt", POINT)
    ]

class ScreenCensor:
    def __init__(self, root):
        if not IS_WINDOWS:
            raise RuntimeError("Screen Censor 4.0 Desktop Overlay hanya untuk Windows 10/11.")
        self.root = root
        root.title(APP_TITLE)
        root.geometry("430x300")
        root.resizable(False, False)
        root.configure(bg="#151923")
        root.attributes("-topmost", True)

        self.running = False
        self.closing = False
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.boxes = []
        self.patch_data = []
        self.patch_images = []
        self.detector = None
        self.worker = None
        self.overlay_hwnd = None
        self.last_error = None
        self.frame_size = (0, 0)
        self.hotkey_registered = False

        tk.Label(root, text="SCREEN CENSOR 4.0", fg="white", bg="#151923",
                 font=("Segoe UI", 17, "bold")).pack(pady=(16, 3))
        tk.Label(root, text="Desktop blur overlay • wajah + area dada terbuka",
                 fg="#b9c2d0", bg="#151923", font=("Segoe UI", 9)).pack()

        self.status = tk.Label(root, text="Status: Mati", fg="#ff7777",
                               bg="#151923", font=("Segoe UI", 10, "bold"))
        self.status.pack(pady=12)

        row = tk.Frame(root, bg="#151923")
        row.pack()
        self.start_btn = tk.Button(row, text="MULAI SENSOR", width=16,
                                   command=self.start, bg="#238b57", fg="white",
                                   relief="flat", font=("Segoe UI", 10, "bold"))
        self.start_btn.pack(side="left", padx=5)
        self.stop_btn = tk.Button(row, text="MATIKAN", width=12,
                                  command=self.stop, state="disabled",
                                  bg="#a83e45", fg="white", relief="flat",
                                  font=("Segoe UI", 10, "bold"))
        self.stop_btn.pack(side="left", padx=5)

        tk.Label(root, text="Tombol darurat global: Ctrl + Shift + F12",
                 fg="#ffd166", bg="#151923", font=("Segoe UI", 9, "bold")).pack(pady=(14, 3))
        tk.Label(root, text="Overlay desktop di atas aplikasi lain • label: losser",
                 fg="#9aa5b5", bg="#151923", font=("Segoe UI", 8)).pack()
        tk.Label(root, text="Target gambar overlay 30 FPS; kecepatan AI tergantung PC.",
                 fg="#9aa5b5", bg="#151923", font=("Segoe UI", 8)).pack(pady=(3, 0))

        self.overlay = tk.Toplevel(root)
        self.overlay.withdraw()
        self.overlay.overrideredirect(True)
        self.overlay.attributes("-topmost", True)
        self.overlay.configure(bg="magenta")
        self.overlay.wm_attributes("-transparentcolor", "magenta")
        self.canvas = tk.Canvas(self.overlay, bg="magenta", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._register_hotkey)
        self.root.after(OVERLAY_REFRESH_MS, self.refresh_overlay)
        self.root.bind_all("<Control-Shift-F12>", lambda _e: self.stop())

    def _register_hotkey(self):
        if not self.closing:
            try:
                self.hotkey_registered = bool(ctypes.windll.user32.RegisterHotKey(
                    None, 0x5343, MOD_CONTROL | MOD_SHIFT, VK_F12))
            except Exception:
                self.hotkey_registered = False
            self.root.after(35, self._poll_hotkey)

    def _poll_hotkey(self):
        if self.closing:
            return
        if self.hotkey_registered:
            msg = MSG()
            while ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), None,
                                                     WM_HOTKEY, WM_HOTKEY, PM_REMOVE):
                self.root.after(0, self.stop)
        self.root.after(35, self._poll_hotkey)

    def _configure_overlay(self):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.overlay.geometry(f"{sw}x{sh}+0+0")
        self.overlay.deiconify()
        self.overlay.update_idletasks()
        hwnd = self.overlay.winfo_id()
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                              style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)
        # Keep the overlay visible to the user but exclude it from screen capture,
        # avoiding the hide/show flicker and recursive capture feedback.
        if not user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
            raise ctypes.WinError()
        self.overlay_hwnd = hwnd
        self.overlay.lift()

    def start(self):
        if self.running:
            return
        self.stop_event.clear()
        self.start_btn.config(state="disabled")
        self.status.config(text="Status: Memuat model…", fg="#ffd166")
        self.worker = threading.Thread(target=self._load_and_scan, daemon=True)
        self.worker.start()

    def _load_and_scan(self):
        try:
            self.detector = NudeDetector()
            cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            self.face_cascade = cv2.CascadeClassifier(cascade_path)
            if self.face_cascade.empty():
                raise RuntimeError("Model deteksi wajah OpenCV tidak ditemukan.")
            if self.stop_event.is_set():
                self.root.after(0, self._stopped_ui)
                return
            self.running = True
            self.root.after(0, self._started_ui)
            self._scan_loop()
        except Exception as exc:
            self.running = False
            self.last_error = str(exc)
            self.root.after(0, lambda err=str(exc): self._failed(err))
        finally:
            self.root.after(0, self._stopped_ui)

    def _started_ui(self):
        try:
            self._configure_overlay()
            self.status.config(text="Status: AKTIF", fg="#65d69a")
            self.stop_btn.config(state="normal")
        except Exception as exc:
            self.running = False
            self.stop_event.set()
            self._failed("Windows gagal mengaktifkan desktop overlay dengan aman:\n" + str(exc))

    def _screen_tiles(self, frame):
        h, w = frame.shape[:2]
        tile_w = int(w / TILE_COLS * (1 + TILE_OVERLAP))
        tile_h = int(h / TILE_ROWS * (1 + TILE_OVERLAP))
        xs = [0, max(0, w - tile_w)]
        ys = [0, max(0, h - tile_h)]
        tiles = []
        for y in ys:
            for x in xs:
                x2, y2 = min(w, x + tile_w), min(h, y + tile_h)
                tiles.append((frame[y:y2, x:x2], x, y))
        return tiles

    def _nudenet_boxes(self, frame):
        h, w = frame.shape[:2]
        found = []
        for tile, ox, oy in self._screen_tiles(frame):
            temp_name = None
            try:
                fd, temp_name = tempfile.mkstemp(suffix=".jpg")
                os.close(fd)
                if not cv2.imwrite(temp_name, tile, [int(cv2.IMWRITE_JPEG_QUALITY), 90]):
                    continue
                for item in self.detector.detect(temp_name):
                    label = str(item.get("class", "")).upper()
                    score = float(item.get("score", 0))
                    if label != BREAST_LABEL or score < BREAST_THRESHOLD:
                        continue
                    bx, by, bw, bh = [float(v) for v in item.get("box", [0, 0, 0, 0])]
                    px, py = int(bw * BOX_PADDING), int(bh * BOX_PADDING)
                    x1 = max(0, int(ox + bx) - px)
                    y1 = max(0, int(oy + by) - py)
                    x2 = min(w, int(ox + bx + bw) + px)
                    y2 = min(h, int(oy + by + bh) + py)
                    if x2 > x1 and y2 > y1:
                        found.append((x1, y1, x2, y2, "losser"))
            finally:
                if temp_name and os.path.exists(temp_name):
                    try:
                        os.remove(temp_name)
                    except OSError:
                        pass
        return self._dedupe(found)

    @staticmethod
    def _dedupe(boxes):
        kept = []
        for box in sorted(boxes, key=lambda b: (b[2]-b[0])*(b[3]-b[1]), reverse=True):
            x1,y1,x2,y2,_ = box
            cx,cy = (x1+x2)/2,(y1+y2)/2
            duplicate = False
            for old in kept:
                ox1,oy1,ox2,oy2,_ = old
                ocx,ocy = (ox1+ox2)/2,(oy1+oy2)/2
                if abs(cx-ocx) < min(x2-x1, ox2-ox1)*0.45 and abs(cy-ocy) < min(y2-y1, oy2-oy1)*0.45:
                    duplicate = True
                    break
            if not duplicate:
                kept.append(box)
        return kept

    def _scan_loop(self):
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # primary display
            while self.running and not self.stop_event.is_set():
                started = time.perf_counter()
                raw = np.asarray(sct.grab(monitor))
                frame = cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)
                h, w = frame.shape[:2]
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                # Detect faces on a downscaled frame for speed, then restore coordinates.
                scale = 0.5 if max(w, h) >= 1400 else 0.75
                small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                faces = self.face_cascade.detectMultiScale(
                    small, scaleFactor=1.08, minNeighbors=4, minSize=(22, 22)
                )
                boxes = []
                inv = 1.0 / scale
                for x,y,fw,fh in faces:
                    x1,y1 = int(x*inv), int(y*inv)
                    x2,y2 = int((x+fw)*inv), int((y+fh)*inv)
                    boxes.append((x1,y1,x2,y2,"losser"))
                boxes.extend(self._nudenet_boxes(frame))
                # Prepare blurred screen patches. The live overlay renderer only
                # redraws these cached patches at 30 FPS; AI inference runs slower.
                patches = []
                screen = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                for x1,y1,x2,y2,label in boxes:
                    x1,y1 = max(0,x1),max(0,y1)
                    x2,y2 = min(w,x2),min(h,y2)
                    if x2 <= x1 or y2 <= y1:
                        continue
                    patch = screen.crop((x1,y1,x2,y2)).filter(ImageFilter.GaussianBlur(BLUR_RADIUS))
                    patches.append((x1,y1,patch,label))
                with self.lock:
                    self.boxes = boxes
                    self.patch_data = patches
                    self.frame_size = (w,h)
                elapsed = time.perf_counter() - started
                time.sleep(max(0.02, DETECTION_INTERVAL - elapsed))

    def refresh_overlay(self):
        if self.running and self.overlay.state() != "withdrawn":
            with self.lock:
                patches = list(self.patch_data)
            self.canvas.delete("all")
            self.patch_images = []
            for x,y,patch,label in patches:
                photo = ImageTk.PhotoImage(patch)
                self.patch_images.append(photo)
                self.canvas.create_image(x, y, image=photo, anchor="nw")
                self.canvas.create_rectangle(x, y, x+patch.width, y+patch.height,
                                             outline="#ff4d6d", width=2)
                tag_h = 22
                self.canvas.create_rectangle(x, y, x+70, y+tag_h,
                                             fill="#151923", outline="#ff4d6d", width=1)
                self.canvas.create_text(x+35, y+tag_h/2, text="losser",
                                        fill="white", font=("Segoe UI", 10, "bold"))
            self.overlay.lift()
        self.root.after(OVERLAY_REFRESH_MS, self.refresh_overlay)

    def _failed(self, error):
        self.status.config(text="Sensor gagal / berhenti", fg="#ff7777")
        messagebox.showerror(APP_TITLE, "Gagal menjalankan sensor:\n\n" + error)

    def _stopped_ui(self):
        self.running = False
        try:
            self.overlay.withdraw()
        except Exception:
            pass
        with self.lock:
            self.boxes = []
            self.patch_data = []
        self.canvas.delete("all")
        self.patch_images = []
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        if not self.closing:
            self.status.config(text="Status: Mati", fg="#ff7777")

    def stop(self):
        self.running = False
        self.stop_event.set()
        self._stopped_ui()

    def close(self):
        self.closing = True
        self.stop()
        if self.hotkey_registered:
            try:
                ctypes.windll.user32.UnregisterHotKey(None, 0x5343)
            except Exception:
                pass
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = ScreenCensor(root)
    root.mainloop()
