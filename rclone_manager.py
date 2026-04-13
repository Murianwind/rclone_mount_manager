"""
RcloneManager - rclone 마운트 관리 트레이 앱
GitHub: https://github.com/Murianwind/rclone_mount_manager
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import os
import sys
import subprocess
import threading
import requests
import zipfile
import tempfile
import shutil
import re
import configparser
import uuid
import webbrowser
import urllib.parse
from pathlib import Path
import ctypes

try:
    import winreg
except ImportError:
    winreg = None

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    pystray = None

# ── 프로그램 설정 ──
APP_VERSION = "1.1.0"
GITHUB_REPO = "Murianwind/rclone_mount_manager"

# ── 1. 시스템 환경 설정 ──
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass


def get_dpi_scale():
    """현재 시스템 DPI 배율 반환 (1.0 = 100%)"""
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return dpi / 96.0
    except Exception:
        return 1.0


def get_screen_size():
    """실제 물리 해상도 반환"""
    try:
        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


def get_sys_info():
    """시스템 해상도 및 배율 정보 (Scenario 20)"""
    try:
        user32 = ctypes.windll.user32
        w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        hdc = user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
        user32.ReleaseDC(0, hdc)
        return f"Resolution: {w}x{h}, Scaling: {int((dpi / 96) * 100)}%"
    except Exception:
        return "N/A"


def calc_window_size(base_w, base_h):
    """
    해상도와 DPI 배율을 고려해 창 크기를 계산한다.
    기준: 1920x1080 @ 100% = base 크기
    - 높은 해상도 + 높은 배율(예: 2736x1824 @ 175%)에서는 논리 픽셀이 작으므로
      물리 해상도 대비 비율만 반영해 적절히 키움
    - 화면의 85%를 넘지 않도록 제한
    """
    sw, sh = get_screen_size()
    scale = get_dpi_scale()

    # 논리 해상도 = 물리 해상도 / 배율
    logical_w = sw / scale
    logical_h = sh / scale

    # 1920x1080 논리 해상도 기준 비율
    ratio = min(logical_w / 1920, logical_h / 1080)
    # 비율 범위 제한 (너무 크거나 작아지지 않도록)
    ratio = max(0.70, min(1.15, ratio))

    w = int(base_w * ratio)
    h = int(base_h * ratio)

    # 화면의 85% 초과 방지 (논리 픽셀 기준)
    w = min(w, int(logical_w * 0.85))
    h = min(h, int(logical_h * 0.85))

    return w, h


# ── 2. 시작 프로그램 및 유틸리티 ──
def is_startup_enabled():
    if not winreg:
        return False
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_READ)
        winreg.QueryValueEx(key, "RcloneManager")
        winreg.CloseKey(key)
        return True
    except:
        return False


def set_startup(enable: bool):
    if not winreg:
        return False
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_SET_VALUE)
        if enable:
            path = (f'"{sys.executable}"' if getattr(sys, 'frozen', False)
                    else f'pythonw "{Path(__file__).resolve()}"')
            winreg.SetValueEx(key, "RcloneManager", 0, winreg.REG_SZ, path)
        else:
            try:
                winreg.DeleteValue(key, "RcloneManager")
            except:
                pass
        winreg.CloseKey(key)
        return True
    except Exception as e:
        return str(e)


def parse_rclone_conf(conf_path: Path):
    remotes = []
    try:
        cfg = configparser.ConfigParser()
        cfg.read(str(conf_path), encoding="utf-8")
        for section in cfg.sections():
            remotes.append({"name": section, "type": cfg.get(section, "type", fallback="")})
    except Exception:
        pass
    return remotes


def find_default_rclone_conf():
    for p in [
        Path(os.environ.get("APPDATA", "")) / "rclone" / "rclone.conf",
        Path.home() / ".config" / "rclone" / "rclone.conf",
        APP_DIR / "rclone.conf",
    ]:
        if p.exists():
            return p
    return None


def download_rclone(dest_dir: Path, version: str, progress_cb=None):
    """rclone 다운로드 및 설치 (Scenario 15)"""
    url = (f"https://github.com/rclone/rclone/releases/download/"
           f"v{version}/rclone-v{version}-windows-amd64.zip")
    try:
        r = requests.get(url, stream=True, timeout=30)
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        tmp = tempfile.mktemp(suffix=".zip")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb and total:
                    progress_cb(int(downloaded * 100 / total))
        with zipfile.ZipFile(tmp, "r") as z:
            for name in z.namelist():
                if name.endswith("rclone.exe"):
                    data = z.read(name)
                    (dest_dir / "rclone.exe").write_bytes(data)
                    break
        os.unlink(tmp)
        return True
    except Exception as e:
        return str(e)


def download_app_release(asset_url: str, progress_cb=None):
    """
    앱 자체 업데이트:
    GitHub Release asset(exe 또는 zip)을 내려받아 현재 실행 파일 위치에 배치한다.
    zip이면 압축 해제 후 zip 삭제.
    frozen(PyInstaller) 환경에서만 파일 교체가 의미 있다.
    """
    try:
        r = requests.get(asset_url, stream=True, timeout=60)
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        suffix = "." + asset_url.rsplit(".", 1)[-1] if "." in asset_url else ".zip"
        tmp = tempfile.mktemp(suffix=suffix)
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb and total:
                    progress_cb(int(downloaded * 100 / total))

        dest_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else APP_DIR

        if suffix.lower() == ".zip":
            with zipfile.ZipFile(tmp, "r") as z:
                z.extractall(dest_dir)
            os.unlink(tmp)
        else:
            # 실행 중인 exe 교체: 기존 파일 .old로 백업 후 이동
            if getattr(sys, 'frozen', False):
                cur = Path(sys.executable)
            else:
                cur = dest_dir / "rclone_mount_manager.exe"
            if cur.exists():
                cur.rename(cur.with_suffix(".old"))
            shutil.move(tmp, str(cur))

        return True
    except Exception as e:
        return str(e)


# ── 3. 설정 관리 ──
if getattr(sys, 'frozen', False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).parent
CONFIG_FILE = APP_DIR / "mounts.json"


def load_config():
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if "mounts" not in cfg:
                cfg["mounts"] = []
            return cfg
        except:
            pass
    return {"remotes": [], "mounts": [], "rclone_path": "", "auto_mount": False}


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_rclone_exe(cfg):
    """
    등록된 rclone 경로만 반환.
    등록이 없거나 파일이 없으면 None 반환.
    (APP_DIR 자동 탐색 제거 - 항목 6 대응)
    """
    p = cfg.get("rclone_path", "").strip()
    if p and Path(p).exists():
        return Path(p)
    return None


active_mounts = {}


def build_cmd(exe: Path, mount: dict):
    rpath = mount.get("remote_path", "").strip().replace("\\", "/").strip("/")
    drive_target = mount.get("drive", "").strip() or " "
    cmd = [str(exe), "mount", f"{mount['remote']}:{rpath}", drive_target,
           "--volname", mount.get("label") or mount["remote"]]
    if mount.get("cache_dir"):
        cmd += ["--cache-dir", mount["cache_dir"]]
    if mount.get("cache_mode"):
        cmd += ["--vfs-cache-mode", mount["cache_mode"]]
    extra = mount.get("extra_flags", "").strip()
    if extra:
        for f in re.split(r"[\s;]+|\n", extra):
            if f.strip():
                cmd.append(f.strip())
    return cmd


def unmount(mid):
    p = active_mounts.get(mid)
    if p:
        p.terminate()
        try:
            p.wait(timeout=3)
        except:
            p.kill()
        active_mounts.pop(mid, None)


def activate_existing_window():
    hwnd = ctypes.windll.user32.FindWindowW(None, "RcloneManager")
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        return True
    return False


# ── 트레이 아이콘 이미지 헬퍼 ──
def _make_circle_icon(color="#cba6f7", size=64):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, size - 2, size - 2], fill=color)
    return img


def _make_dot_icon(color, size=16):
    """작은 원형 상태 아이콘 (트레이 메뉴용)"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([1, 1, size - 2, size - 2], fill=color)
    return img


# ── 4. 다이얼로그 ──
class ConfImportDialog(tk.Toplevel):
    def __init__(self, parent, remotes):
        super().__init__(parent)
        self.title("리모트 선택")
        self.grab_set()
        self.configure(bg="#1e1e2e")
        self.selected = []
        self._remotes = remotes
        self._vars = []
        tk.Label(self, text="가져올 리모트 선택:", bg="#1e1e2e", fg="#cba6f7",
                 font=("Segoe UI", 10, "bold")).pack(padx=16, pady=10, anchor="w")
        for r in self._remotes:
            v = tk.BooleanVar(value=True)
            self._vars.append((v, r))
            row = tk.Frame(self, bg="#1e1e2e")
            row.pack(fill="x", padx=16, pady=2)
            tk.Checkbutton(row, variable=v, bg="#1e1e2e", fg="#cdd6f4",
                           selectcolor="#313244").pack(side="left")
            tk.Label(row, text=f"{r['name']} [{r['type']}]", bg="#1e1e2e",
                     fg="#cdd6f4").pack(side="left")
        tk.Button(self, text="가져오기", bg="#cba6f7", fg="#1e1e2e",
                  font=("Segoe UI", 10, "bold"), relief="flat",
                  command=self._ok).pack(pady=10, ipady=3, ipadx=10)

    def _ok(self):
        self.selected = [(r["name"], r["type"]) for v, r in self._vars if v.get()]
        self.destroy()


class UpdateDialog(tk.Toplevel):
    """
    앱 업데이트 확인 다이얼로그.
    - 업데이트 내역 스크롤바 추가
    - 해상도/배율에 따라 창 크기 자동 조절 → 취소/업데이트 버튼 항상 보임
    - 업데이트 버튼 클릭 시 자동 다운로드/교체 (asset_url 있을 때)
    """

    def __init__(self, parent, tag, body, assets=None):
        super().__init__(parent)
        self.title(f"업데이트 확인 - {tag}")
        self.configure(bg="#1e1e2e")
        self.grab_set()
        self.confirmed = False
        self._asset_url = self._pick_asset(assets or [])

        # ── 창 크기: 해상도/배율 동적 계산 ──
        w, h = calc_window_size(620, 460)
        w = max(w, 480)
        h = max(h, 360)
        self.geometry(f"{w}x{h}")
        self.minsize(480, 360)
        self.resizable(True, True)

        # ── grid 레이아웃: 버튼행은 항상 하단 고정 ──
        self.rowconfigure(0, weight=0)   # 제목
        self.rowconfigure(1, weight=1)   # 본문 (늘어남)
        self.rowconfigure(2, weight=0)   # 버튼 (고정)
        self.columnconfigure(0, weight=1)

        # 제목
        tk.Label(self,
                 text=f"✨ 새 버전({tag})으로 업데이트하시겠습니까?",
                 bg="#1e1e2e", fg="#cba6f7",
                 font=("Segoe UI", 11, "bold"),
                 wraplength=w - 40).grid(row=0, column=0, sticky="ew",
                                         padx=20, pady=(16, 8))

        # 본문 + 스크롤바
        txt_frame = tk.Frame(self, bg="#1e1e2e")
        txt_frame.grid(row=1, column=0, sticky="nsew", padx=20, pady=4)
        txt_frame.rowconfigure(0, weight=1)
        txt_frame.columnconfigure(0, weight=1)

        vsb = tk.Scrollbar(txt_frame)
        vsb.grid(row=0, column=1, sticky="ns")

        txt = tk.Text(txt_frame, bg="#313244", fg="#cdd6f4", relief="flat",
                      font=("Segoe UI", 10), padx=10, pady=10,
                      yscrollcommand=vsb.set, wrap="word")
        txt.grid(row=0, column=0, sticky="nsew")
        vsb.config(command=txt.yview)
        txt.insert("1.0", body)
        txt.config(state="disabled")

        # 버튼 (항상 보임)
        btn_f = tk.Frame(self, bg="#1e1e2e")
        btn_f.grid(row=2, column=0, sticky="ew", padx=20, pady=(8, 16))
        tk.Button(btn_f, text="업데이트", bg="#a6e3a1", fg="#1e1e2e",
                  font=("Segoe UI", 10, "bold"), relief="flat",
                  command=self._ok, width=14).pack(side="right", padx=5, ipady=5)
        tk.Button(btn_f, text="취소", bg="#45475a", fg="#cdd6f4",
                  font=("Segoe UI", 10, "bold"), relief="flat",
                  command=self.destroy, width=14).pack(side="right", padx=5, ipady=5)

    @staticmethod
    def _pick_asset(assets):
        """windows exe 또는 zip asset URL 선택"""
        for a in assets:
            name = a.get("name", "").lower()
            if name.endswith(".exe") or name.endswith(".zip"):
                return a.get("browser_download_url", "")
        return ""

    def _ok(self):
        self.confirmed = True
        self.destroy()


class MountDialog(tk.Toplevel):
    def __init__(self, parent, mount=None, app_cfg=None):
        super().__init__(parent)
        self.title("마운트 추가" if not mount or "id" not in mount else "마운트 설정")
        # ── 창 크기: 해상도/배율 동적 계산 ──
        w, h = calc_window_size(640, 800)
        w = max(w, 500)
        h = max(h, 560)
        self.geometry(f"{w}x{h}")
        self.minsize(500, 560)
        self.resizable(True, True)
        self.configure(bg="#1e1e2e")
        self.grab_set()
        self.result = None
        self._m = mount or {}
        self._app_cfg = app_cfg
        self._build()

    def _build(self):
        # 낮은 해상도/높은 배율 환경에서도 모든 항목을 볼 수 있도록
        # Canvas + Scrollbar 로 감쌈
        canvas = tk.Canvas(self, bg="#1e1e2e", highlightthickness=0)
        vsb = tk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        c = tk.Frame(canvas, padx=26, pady=18, bg="#1e1e2e")
        win_id = canvas.create_window((0, 0), window=c, anchor="nw")

        def _on_frame_conf(e):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_conf(e):
            canvas.itemconfig(win_id, width=e.width)

        c.bind("<Configure>", _on_frame_conf)
        canvas.bind("<Configure>", _on_canvas_conf)
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        lbl_s = {"bg": "#1e1e2e", "fg": "#cba6f7", "font": ("Segoe UI", 10, "bold")}
        ent_s = {"bg": "#313244", "fg": "#cdd6f4", "insertbackground": "#cdd6f4",
                 "relief": "flat", "font": ("Segoe UI", 10)}

        tk.Label(c, text="리모트 이름", **lbl_s).pack(anchor="w")
        self._rem = tk.Entry(c, **ent_s)
        self._rem.pack(fill="x", pady=(5, 13), ipady=4)
        self._rem.insert(0, self._m.get("remote", ""))

        tk.Label(c, text="서브 디렉토리", **lbl_s).pack(anchor="w")
        pth_f = tk.Frame(c, bg="#1e1e2e")
        pth_f.pack(fill="x", pady=(5, 13))
        self._pth = tk.Entry(pth_f, **ent_s)
        self._pth.pack(side="left", fill="x", expand=True, ipady=4)
        self._pth.insert(0, self._m.get("remote_path", ""))
        tk.Button(pth_f, text="연결 테스트", bg="#89b4fa", fg="#1e1e2e",
                  font=("Segoe UI", 9, "bold"), relief="flat",
                  command=self._test).pack(side="left", padx=(10, 0), ipady=3)

        tk.Label(c, text="드라이브 문자", **lbl_s).pack(anchor="w")
        drive_values = [""] + [f"{chr(i)}:" for i in range(ord('D'), ord('Z') + 1)]
        self._drv = ttk.Combobox(c, values=drive_values, font=("Segoe UI", 10),
                                  state="readonly")
        self._drv.pack(fill="x", pady=(5, 13))
        self._drv.set(self._m.get("drive", ""))

        tk.Label(c, text="캐시 디렉토리", **lbl_s).pack(anchor="w")
        cdir_f = tk.Frame(c, bg="#1e1e2e")
        cdir_f.pack(fill="x", pady=(5, 13))
        self._cdir = tk.Entry(cdir_f, **ent_s)
        self._cdir.pack(side="left", fill="x", expand=True, ipady=4)
        self._cdir.insert(0, self._m.get("cache_dir", ""))
        tk.Button(cdir_f, text="📂", bg="#45475a", fg="#cdd6f4", relief="flat",
                  command=self._browse_cache).pack(side="left", padx=(5, 0))

        tk.Label(c, text="캐시 모드", **lbl_s).pack(anchor="w")
        self._cmode = ttk.Combobox(c, values=["off", "minimal", "writes", "full"],
                                    font=("Segoe UI", 10), state="readonly")
        self._cmode.pack(fill="x", pady=(5, 13))
        self._cmode.set(self._m.get("cache_mode", "full"))

        tk.Label(c, text="추가 플래그", **lbl_s).pack(anchor="w")
        self._ext = tk.Text(c, height=5, **ent_s)
        self._ext.pack(fill="x", pady=(5, 13))
        self._ext.insert("1.0", self._m.get("extra_flags", ""))

        self._auto = tk.BooleanVar(value=self._m.get("auto_mount", False))
        tk.Checkbutton(c, text="시작 시 자동 마운트", variable=self._auto,
                       bg="#1e1e2e", fg="#cdd6f4", selectcolor="#313244",
                       font=("Segoe UI", 10)).pack(anchor="w", pady=6)

        btn_f = tk.Frame(c, bg="#1e1e2e")
        btn_f.pack(fill="x", pady=(16, 4))
        tk.Button(btn_f, text="저장", bg="#cba6f7", fg="#1e1e2e",
                  font=("Segoe UI", 11, "bold"), relief="flat",
                  command=self._save, width=13).pack(side="right", padx=(10, 0), ipady=5)
        tk.Button(btn_f, text="취소", bg="#45475a", fg="#cdd6f4",
                  font=("Segoe UI", 11, "bold"), relief="flat",
                  command=self.destroy, width=13).pack(side="right", ipady=5)

    def _browse_cache(self):
        d = filedialog.askdirectory()
        if d:
            self._cdir.delete(0, tk.END)
            self._cdir.insert(0, d)

    def _test(self):
        target = f"{self._rem.get().strip()}:{self._pth.get().strip().strip('/')}"
        exe = get_rclone_exe(self._app_cfg)
        if not exe:
            return messagebox.showerror("오류", "rclone 경로가 등록되어 있지 않습니다.")

        def r():
            try:
                p = subprocess.run([str(exe), "lsf", target, "--max-depth", "1"],
                                   capture_output=True, text=True, timeout=10,
                                   creationflags=0x08000000)
                if p.returncode == 0:
                    messagebox.showinfo("성공", "연결 확인 완료!")
                else:
                    messagebox.showerror("실패", f"연결 불가:\n{p.stderr.strip()}")
            except Exception as e:
                messagebox.showerror("오류", str(e))

        threading.Thread(target=r, daemon=True).start()

    def _save(self):
        rem, drv, pth = self._rem.get().strip(), self._drv.get(), self._pth.get().strip()
        if not rem:
            return messagebox.showwarning("오류", "리모트 이름 필수")
        for m in self._app_cfg.get("mounts", []):
            if m.get("id") == self._m.get("id"):
                continue
            if drv and m.get("drive") == drv:
                return messagebox.showerror("오류", "드라이브 문자 중복")
            if m.get("remote") == rem and m.get("remote_path", "") == pth:
                return messagebox.showerror("오류", "동일한 리모트/경로가 이미 등록되어 있습니다.")
        self.result = {
            "remote": rem, "drive": drv, "remote_path": pth,
            "cache_dir": self._cdir.get().strip(),
            "cache_mode": self._cmode.get(),
            "extra_flags": self._ext.get("1.0", tk.END).strip(),
            "auto_mount": self._auto.get(),
        }
        self.destroy()


# ── 5. 메인 앱 ──
class App(tk.Tk):
    def __init__(self):
        self._tray = None
        super().__init__()
        self.title("RcloneManager")

        # ── 메인 창 크기: 해상도/배율 동적 계산 ──
        mw, mh = calc_window_size(1080, 700)
        mw = max(mw, 780)
        mh = max(mh, 520)
        self.geometry(f"{mw}x{mh}")
        self.minsize(780, 520)
        self.resizable(True, True)
        self.configure(bg="#1e1e2e")
        self.protocol("WM_DELETE_WINDOW", self.hide_window)

        self._cfg = load_config()
        self._status = {}
        self._latest_rc = ""
        self._latest_app_info = None
        self._version_check_running = False

        self._build_ui()
        self._refresh_list()
        self._start_tray()

        # 초기: 등록된 rclone 존재 여부를 즉시 반영
        self._init_rc_label()
        # 비동기로 버전 정보 조회
        self._check_versions_async()

        # 창 활성화(포커스) 시 버전 재확인
        self.bind("<FocusIn>", self._on_focus_in)

        if self._cfg.get("auto_mount"):
            self.after(1500, self._automount_all)

    # ────────────────────────────────────────────
    # UI 구성
    # ────────────────────────────────────────────
    def _build_ui(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame", background="#1e1e2e")
        s.configure("TLabel", background="#1e1e2e", foreground="#cdd6f4",
                    font=("Segoe UI", 10))
        s.configure("Header.TLabel", font=("Segoe UI", 16, "bold"),
                    foreground="#cba6f7")
        s.configure("Treeview", background="#313244", foreground="#cdd6f4",
                    fieldbackground="#313244", rowheight=30)
        s.configure("Treeview.Heading", background="#45475a", foreground="#cba6f7",
                    font=("Segoe UI", 11, "bold"))

        # 헤더
        hdr = ttk.Frame(self)
        hdr.pack(fill="x", padx=20, pady=12)
        ttl_f = ttk.Frame(hdr)
        ttl_f.pack(side="left")
        ttk.Label(ttl_f, text="🚀 RcloneManager", style="Header.TLabel").pack(side="left")
        ttk.Label(ttl_f, text=f"v{APP_VERSION}", foreground="#fab387",
                  font=("Segoe UI", 10, "bold")).pack(side="left", padx=8, pady=(5, 0))
        tk.Button(ttl_f, text="!", bg="#f38ba8", fg="#1e1e2e",
                  font=("Segoe UI", 9, "bold"), relief="flat", width=2,
                  command=self._open_issue).pack(side="left", padx=5, pady=(5, 0))

        # 앱 업데이트 버튼 (새 버전 있을 때만 표시)
        self._app_up_btn = tk.Button(
            hdr, text="✨ 새 버전 업데이트 가능", bg="#a6e3a1", fg="#1e1e2e",
            font=("Segoe UI", 9, "bold"), relief="flat",
            command=self._show_app_update_confirm)

        # rclone 경로 행
        rcf = tk.Frame(self, bg="#1e1e2e")
        rcf.pack(fill="x", padx=20, pady=5)
        tk.Label(rcf, text="rclone 경로:", bg="#1e1e2e", fg="#cba6f7",
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self._rc_var = tk.StringVar(value=self._cfg.get("rclone_path", ""))
        tk.Entry(rcf, textvariable=self._rc_var, bg="#313244", fg="#cdd6f4",
                 relief="flat", width=55).pack(side="left", padx=10, ipady=4)
        tk.Button(rcf, text="📂", bg="#45475a", fg="#cdd6f4", relief="flat",
                  command=self._browse_rc).pack(side="left")

        # rclone 버전/상태 레이블 (클릭 가능)
        # 상태별 표시:
        #   등록 없음  → "rclone 다운로드" (분홍, 클릭 → 다운로드)
        #   최신       → "v{현재} (최신)"  (초록)
        #   구버전     → "v{현재} / v{최신} 업데이트" (주황, 클릭 → 업데이트)
        self._rc_ver_label = tk.Label(
            rcf, text="", bg="#1e1e2e", fg="#94e2d5",
            font=("Segoe UI", 10), cursor="hand2")
        self._rc_ver_label.pack(side="left", padx=14)
        self._rc_ver_label.bind("<Button-1>", self._handle_rc_click)

        # 옵션 체크박스 행
        opt = tk.Frame(self, bg="#1e1e2e")
        opt.pack(fill="x", padx=20, pady=8)
        self._st_var = tk.BooleanVar(value=is_startup_enabled())
        ttk.Checkbutton(opt, text="시작 시 자동 실행", variable=self._st_var,
                        command=self._toggle_st).pack(side="left", padx=(0, 24))
        self._am_var = tk.BooleanVar(value=self._cfg.get("auto_mount", False))
        ttk.Checkbutton(opt, text="시작 시 자동 마운트", variable=self._am_var,
                        command=self._toggle_am).pack(side="left")

        # 트리뷰 (5컬럼)
        cols = ("type", "auto", "drive", "remote", "status")
        self._tree = ttk.Treeview(self, columns=cols, show="headings", height=14)
        for col, head, cw, stretch in zip(
                cols,
                ("구분", "자동", "드라이브", "리모트 (서브경로)", "상태"),
                (90, 55, 90, 0, 110),
                (False, False, False, True, False)):
            self._tree.heading(col, text=head)
            self._tree.column(col, width=cw, stretch=stretch)
        self._tree.pack(fill="both", expand=True, padx=20, pady=5)
        self._tree.tag_configure("remote_tag", foreground="#8fa0b5")

        # 하단 버튼 행
        btn_f = ttk.Frame(self)
        btn_f.pack(fill="x", padx=20, pady=12)
        ttk.Button(btn_f, text="➕ 추가", command=self._add).pack(side="left", padx=2)
        ttk.Button(btn_f, text="✏️ 편집", command=self._edit).pack(side="left", padx=2)
        ttk.Button(btn_f, text="🗑️ 삭제", command=self._del).pack(side="left", padx=2)
        ttk.Button(btn_f, text="🔼", width=4, command=self._move_up).pack(side="left", padx=2)
        ttk.Button(btn_f, text="🔽", width=4, command=self._move_down).pack(side="left", padx=2)
        ttk.Button(btn_f, text="📥 conf 가져오기",
                   command=self._import_conf).pack(side="left", padx=2)
        ttk.Button(btn_f, text="▶ 마운트",
                   command=self._mount_sel).pack(side="left", padx=14)
        ttk.Button(btn_f, text="■ 언마운트",
                   command=self._unmount_sel).pack(side="left")

        # 상태바
        st_bar = tk.Frame(self, bg="#313244", height=28)
        st_bar.pack(fill="x", side="bottom")
        tk.Label(st_bar, text=f" System: {get_sys_info()}", bg="#313244",
                 fg="#9399b2", font=("Segoe UI", 9)).pack(side="left", padx=10)

    # ────────────────────────────────────────────
    # rclone 레이블 / 버전 확인
    # ────────────────────────────────────────────
    def _init_rc_label(self):
        """초기 실행 시 등록된 rclone 존재 여부를 즉시 확인해 레이블 초기값 설정"""
        exe = get_rclone_exe(self._cfg)
        if exe is None:
            self._rc_ver_label.config(text="rclone 다운로드", fg="#f38ba8")
        else:
            self._rc_ver_label.config(text="v체크 중...", fg="#94e2d5")

    def _check_rclone_presence(self):
        """
        등록된 rclone 존재 여부 즉시 확인.
        없으면 '다운로드' 표시, 있으면 비동기 버전 체크 (재)실행.
        """
        exe = get_rclone_exe(self._cfg)
        if exe is None:
            self._rc_ver_label.config(text="rclone 다운로드", fg="#f38ba8")
        else:
            self._rc_ver_label.config(text="v체크 중...", fg="#94e2d5")
            self._version_check_running = False
            self._check_versions_async()

    def _on_focus_in(self, event):
        """창 활성화(포커스) 시 rclone 상태 및 앱 버전 재확인 (최상위 창 이벤트만)"""
        if event.widget is self:
            self._check_rclone_presence()

    def _check_versions_async(self):
        """
        백그라운드에서:
        1) 등록된 rclone 버전 확인 + GitHub rclone 최신 버전 조회
        2) 앱 자체 최신 버전 조회
        중복 실행 방지.
        """
        if self._version_check_running:
            return
        self._version_check_running = True

        def _task():
            try:
                exe = get_rclone_exe(self._cfg)
                lat_rc = ""

                # GitHub rclone 최신 버전 조회
                try:
                    res = requests.get(
                        "https://api.github.com/repos/rclone/rclone/releases/latest",
                        timeout=5)
                    lat_rc = res.json().get("tag_name", "").lstrip("v")
                    self._latest_rc = lat_rc
                except Exception:
                    pass

                # 등록된 rclone 없음 → 다운로드 문구
                if exe is None:
                    self.after(0, lambda: self._rc_ver_label.config(
                        text="rclone 다운로드", fg="#f38ba8"))
                else:
                    try:
                        r = subprocess.run([str(exe), "version"],
                                           capture_output=True, text=True,
                                           timeout=5, creationflags=0x08000000)
                        loc_match = re.search(r"rclone v([\d.]+)", r.stdout)
                        loc_rc = loc_match.group(1) if loc_match else ""
                        if loc_rc:
                            if lat_rc and loc_rc < lat_rc:
                                txt = f"v{loc_rc} / v{lat_rc} 업데이트"
                                self.after(0, lambda t=txt: self._rc_ver_label.config(
                                    text=t, fg="#fab387"))
                            else:
                                txt = f"v{loc_rc} (최신)"
                                self.after(0, lambda t=txt: self._rc_ver_label.config(
                                    text=t, fg="#94e2d5"))
                        else:
                            self.after(0, lambda: self._rc_ver_label.config(
                                text="v알 수 없음", fg="#f38ba8"))
                    except Exception:
                        self.after(0, lambda: self._rc_ver_label.config(
                            text="v알 수 없음", fg="#f38ba8"))

                # 앱 자체 업데이트 확인
                try:
                    res = requests.get(
                        f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                        timeout=5)
                    data = res.json()
                    latest_app = data.get("tag_name", "").lstrip("v")
                    self._latest_app_info = data
                    if latest_app > APP_VERSION:
                        self.after(0, lambda: self._app_up_btn.pack(side="right"))
                except Exception:
                    pass
            finally:
                self._version_check_running = False

        threading.Thread(target=_task, daemon=True).start()

    # ────────────────────────────────────────────
    # 앱 업데이트
    # ────────────────────────────────────────────
    def _show_app_update_confirm(self):
        if not self._latest_app_info:
            return
        tag = self._latest_app_info.get("tag_name", "New Version")
        body = self._latest_app_info.get("body", "No release notes.")
        assets = self._latest_app_info.get("assets", [])
        dlg = UpdateDialog(self, tag, body, assets=assets)
        self.wait_window(dlg)
        if not dlg.confirmed:
            return

        asset_url = dlg._asset_url
        if not asset_url:
            # 다운로드 가능한 asset 없으면 브라우저로 열기
            webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")
            return

        # 자동 다운로드 → 파일 교체
        self._app_up_btn.config(text="앱 업데이트 중... 0%", state="disabled")

        def _do():
            def _prog(p):
                self.after(0, lambda pv=p: self._app_up_btn.config(
                    text=f"앱 업데이트 중... {pv}%"))

            res = download_app_release(asset_url, _prog)
            if res is True:
                self.after(0, lambda: messagebox.showinfo(
                    "업데이트 완료",
                    "업데이트 파일 교체가 완료되었습니다.\n프로그램을 재시작해 주세요."))
                self.after(0, lambda: self._app_up_btn.config(
                    text="✅ 재시작 필요", state="normal"))
            else:
                self.after(0, lambda err=res: messagebox.showerror("오류", err))
                self.after(0, lambda: self._app_up_btn.config(
                    text="✨ 새 버전 업데이트 가능", state="normal"))

        threading.Thread(target=_do, daemon=True).start()

    # ────────────────────────────────────────────
    # rclone 다운로드/업데이트
    # ────────────────────────────────────────────
    def _handle_rc_click(self, event):
        """
        레이블 클릭:
        - 'rclone 다운로드': 최신 버전 다운로드 후 자동 등록
        - '업데이트' 포함 : 등록된 rclone를 최신 버전으로 업데이트
        """
        text = self._rc_ver_label.cget("text")

        if "다운로드" in text:
            if not self._latest_rc:
                messagebox.showinfo("rclone",
                                    "최신 버전 정보를 확인 중입니다. 잠시 후 다시 시도해 주세요.")
                self._version_check_running = False
                self._check_versions_async()
                return
            if messagebox.askyesno("rclone", f"rclone v{self._latest_rc}를 설치할까요?"):
                threading.Thread(target=self._do_rc_down, daemon=True).start()

        elif "업데이트" in text:
            if self._latest_rc and messagebox.askyesno(
                    "rclone", f"rclone v{self._latest_rc}로 업데이트할까요?"):
                threading.Thread(target=self._do_rc_down, daemon=True).start()

    def _do_rc_down(self):
        """
        rclone 다운로드/업데이트 실행 (백그라운드).
        다운로드 위치:
        - 등록된 경로가 있으면 해당 디렉토리에 덮어쓰기
        - 없으면 APP_DIR 에 다운로드 후 자동 등록
        """
        # 다운로드 대상 디렉토리 결정
        registered = self._cfg.get("rclone_path", "").strip()
        if registered:
            dest_dir = Path(registered).parent
        else:
            dest_dir = APP_DIR

        self.after(0, lambda: self._rc_ver_label.config(
            text="다운로드 중... 0%", fg="#89b4fa"))

        def _prog(p):
            self.after(0, lambda pv=p: self._rc_ver_label.config(
                text=f"다운로드 중... {pv}%", fg="#89b4fa"))

        res = download_rclone(dest_dir, self._latest_rc, _prog)
        if res is True:
            new_path = str(dest_dir / "rclone.exe")
            self._rc_var.set(new_path)
            self._cfg["rclone_path"] = new_path
            save_config(self._cfg)
            messagebox.showinfo("완료", "rclone 설치/업데이트 완료!")
            self._version_check_running = False
            self._check_versions_async()
        else:
            messagebox.showerror("오류", str(res))
            self.after(0, lambda: self._rc_ver_label.config(
                text="rclone 다운로드", fg="#f38ba8"))

    # ────────────────────────────────────────────
    # 이슈 리포트
    # ────────────────────────────────────────────
    def _open_issue(self):
        body = urllib.parse.quote(
            f"\n\n--- Debug Info ---\n- App Version: {APP_VERSION}\n- {get_sys_info()}")
        webbrowser.open(f"https://github.com/{GITHUB_REPO}/issues/new?body={body}")

    # ────────────────────────────────────────────
    # 트레이
    # ────────────────────────────────────────────
    def _start_tray(self):
        if not pystray:
            return
        try:
            main_icon = _make_circle_icon("#cba6f7", 64)
            self._tray = pystray.Icon("RcloneManager", main_icon, "RcloneManager",
                                      menu=self._build_tray_menu())
            threading.Thread(target=self._tray.run, daemon=True).start()
        except Exception:
            pass

    def _build_tray_menu(self):
        """
        트레이 우클릭 메뉴:
        🪟 열기
        ─────────────────
        🟢/⚫ [드라이브/리모트] (remote:path)   ← 클릭 시 마운트/언마운트 토글
        ... (등록된 마운트 전체)
        (등록된 마운트 없음) ← 마운트 없을 때
        ─────────────────
        🚪 종료
        """
        if not pystray:
            return None

        icon_open = _make_dot_icon("#89b4fa")   # 파란
        icon_quit = _make_dot_icon("#f38ba8")   # 빨간
        icon_on   = _make_dot_icon("#a6e3a1")   # 초록
        icon_off  = _make_dot_icon("#585b70")   # 회색

        items = [
            pystray.MenuItem("🪟 열기", lambda: self.after(0, self.show_window),
                             default=True, icon=icon_open),
            pystray.Menu.SEPARATOR,
        ]

        mounts = self._cfg.get("mounts", [])
        if mounts:
            for m in mounts:
                mid = m["id"]
                is_mounted = mid in active_mounts
                label = m.get("drive", "") or m.get("remote", "?")
                rstr  = f"{m['remote']}:{m.get('remote_path', '')}".strip(":")
                display = f"{'🟢' if is_mounted else '⚫'}  {label}  ({rstr})"
                ic = icon_on if is_mounted else icon_off

                def _make_toggle(mount_id, mount_data, mounted):
                    def _toggle(icon, item):
                        if mounted:
                            unmount(mount_id)
                            self.after(0, self._refresh_list)
                        else:
                            self.after(0, lambda: self._do_mount(mount_id, mount_data))
                    return _toggle

                items.append(
                    pystray.MenuItem(display, _make_toggle(mid, m, is_mounted), icon=ic)
                )
            items.append(pystray.Menu.SEPARATOR)
        else:
            items.append(pystray.MenuItem("(등록된 마운트 없음)", lambda *_: None,
                                          enabled=False))
            items.append(pystray.Menu.SEPARATOR)

        items.append(
            pystray.MenuItem("🚪 종료", lambda: self.after(0, self._quit_app),
                             icon=icon_quit)
        )
        return pystray.Menu(*items)

    # ────────────────────────────────────────────
    # 목록 갱신
    # ────────────────────────────────────────────
    def _refresh_list(self):
        for i in self._tree.get_children():
            self._tree.delete(i)
        for r in self._cfg.get("remotes", []):
            self._tree.insert("", "end", iid=f"remote_{r['name']}",
                              values=("☁️ 원본", "—", "—",
                                      f"[{r['type']}] {r['name']}", "설정 대기"),
                              tags=("remote_tag",))
        for m in self._cfg.get("mounts", []):
            st = self._status.get(m["id"], "stopped")
            auto = "✅" if m.get("auto_mount") else "—"
            lbl  = "🟢 실행중" if st == "mounted" else "⚫ 중지됨"
            rstr = f"{m['remote']}:{m.get('remote_path', '')}".strip(":")
            self._tree.insert("", "end", iid=m["id"],
                              values=("💾 마운트", auto, m.get("drive", ""), rstr, lbl))
        # 트레이 메뉴 동기화
        if self._tray:
            try:
                self._tray.menu = self._build_tray_menu()
                self._tray.update_menu()
            except Exception:
                pass

    # ────────────────────────────────────────────
    # 설정 토글
    # ────────────────────────────────────────────
    def _toggle_st(self):
        set_startup(self._st_var.get())

    def _toggle_am(self):
        self._save_settings()

    # [테스트 호환성] Scenario 17, 18, 19 대응 메서드
    def _save_settings(self):
        self._cfg["auto_mount"] = self._am_var.get()
        save_config(self._cfg)

    # ────────────────────────────────────────────
    # 마운트 조작
    # ────────────────────────────────────────────
    def _delete_mount(self, mid):
        if messagebox.askyesno("삭제", "선택한 항목을 삭제할까요?"):
            unmount(mid)
            self._cfg["mounts"] = [m for m in self._cfg["mounts"] if m["id"] != mid]
            save_config(self._cfg)
            self._refresh_list()

    def _mount_single(self, mid):
        m = next(m for m in self._cfg["mounts"] if m["id"] == mid)
        self._do_mount(mid, m)

    def _browse_rc(self):
        p = filedialog.askopenfilename(
            filetypes=[("실행 파일", "*.exe"), ("모든 파일", "*.*")])
        if p:
            self._rc_var.set(p)
            self._cfg["rclone_path"] = p
            save_config(self._cfg)
            self._check_rclone_presence()

    def _import_conf(self):
        p = find_default_rclone_conf()
        path = filedialog.askopenfilename(initialdir=str(p.parent) if p else None)
        if not path:
            return
        remotes = parse_rclone_conf(Path(path))
        dlg = ConfImportDialog(self, remotes)
        self.wait_window(dlg)
        if dlg.selected:
            exist = [r["name"] for r in self._cfg.get("remotes", [])]
            for r_name, r_type in dlg.selected:
                if r_name not in exist:
                    self._cfg.setdefault("remotes", []).append(
                        {"name": r_name, "type": r_type})
            save_config(self._cfg)
            self._refresh_list()

    def _add(self):
        sel = self._tree.selection()
        pre = (sel[0].split("remote_", 1)[1]
               if sel and sel[0].startswith("remote_") else "")
        dlg = MountDialog(self, mount={"remote": pre}, app_cfg=self._cfg)
        self.wait_window(dlg)
        if dlg.result:
            dlg.result["id"] = str(uuid.uuid4())
            self._cfg["mounts"].append(dlg.result)
            save_config(self._cfg)
            self._refresh_list()

    def _edit(self):
        sel = self._tree.selection()
        if not sel or sel[0].startswith("remote_"):
            return
        idx = next(i for i, m in enumerate(self._cfg["mounts"]) if m["id"] == sel[0])
        dlg = MountDialog(self, mount=self._cfg["mounts"][idx], app_cfg=self._cfg)
        self.wait_window(dlg)
        if dlg.result:
            dlg.result["id"] = sel[0]
            self._cfg["mounts"][idx] = dlg.result
            save_config(self._cfg)
            self._refresh_list()

    def _del(self):
        sel = self._tree.selection()
        if not sel:
            return
        if sel[0].startswith("remote_"):
            r_name = sel[0].split("remote_", 1)[1]
            if messagebox.askyesno("삭제", f"원본 '{r_name}'을 삭제할까요?"):
                self._cfg["remotes"] = [r for r in self._cfg.get("remotes", [])
                                        if r["name"] != r_name]
                save_config(self._cfg)
                self._refresh_list()
            return
        self._delete_mount(sel[0])

    def _move_up(self):
        sel = self._tree.selection()
        if not sel:
            return
        if sel[0].startswith("remote_"):
            idx = next((i for i, r in enumerate(self._cfg.get("remotes", []))
                        if f"remote_{r['name']}" == sel[0]), None)
            if idx is not None and idx > 0:
                lst = self._cfg["remotes"]
                lst[idx], lst[idx - 1] = lst[idx - 1], lst[idx]
                save_config(self._cfg); self._refresh_list(); self._tree.selection_set(sel[0])
        else:
            idx = next(i for i, m in enumerate(self._cfg["mounts"]) if m["id"] == sel[0])
            if idx > 0:
                lst = self._cfg["mounts"]
                lst[idx], lst[idx - 1] = lst[idx - 1], lst[idx]
                save_config(self._cfg); self._refresh_list(); self._tree.selection_set(sel[0])

    def _move_down(self):
        sel = self._tree.selection()
        if not sel:
            return
        if sel[0].startswith("remote_"):
            idx = next((i for i, r in enumerate(self._cfg.get("remotes", []))
                        if f"remote_{r['name']}" == sel[0]), None)
            if idx is not None and idx < len(self._cfg["remotes"]) - 1:
                lst = self._cfg["remotes"]
                lst[idx], lst[idx + 1] = lst[idx + 1], lst[idx]
                save_config(self._cfg); self._refresh_list(); self._tree.selection_set(sel[0])
        else:
            idx = next(i for i, m in enumerate(self._cfg["mounts"]) if m["id"] == sel[0])
            if idx < len(self._cfg["mounts"]) - 1:
                lst = self._cfg["mounts"]
                lst[idx], lst[idx + 1] = lst[idx + 1], lst[idx]
                save_config(self._cfg); self._refresh_list(); self._tree.selection_set(sel[0])

    def _mount_sel(self):
        sel = self._tree.selection()
        if not sel or sel[0].startswith("remote_") or sel[0] in active_mounts:
            return
        self._mount_single(sel[0])

    def _do_mount(self, mid, m):
        exe = get_rclone_exe(self._cfg)
        if exe is None:
            messagebox.showerror("오류", "rclone 경로가 등록되어 있지 않습니다.")
            return
        self._status[mid] = "mounted"
        self._refresh_list()
        threading.Thread(target=self._mount_task, args=(mid, exe, m), daemon=True).start()

    def _mount_task(self, mid, exe, m):
        try:
            p = subprocess.Popen(build_cmd(exe, m), creationflags=0x08000000)
            active_mounts[mid] = p
            p.wait()
        finally:
            active_mounts.pop(mid, None)
            self._status[mid] = "stopped"
            self.after(0, self._refresh_list)

    def _automount_all(self):
        for m in self._cfg.get("mounts", []):
            if m.get("auto_mount"):
                self._do_mount(m["id"], m)

    def _unmount_sel(self):
        sel = self._tree.selection()
        if sel and not sel[0].startswith("remote_"):
            unmount(sel[0])

    # ────────────────────────────────────────────
    # 창 표시/숨김/종료
    # ────────────────────────────────────────────
    def hide_window(self):
        self.withdraw()

    def show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _quit_app(self):
        for mid in list(active_mounts.keys()):
            unmount(mid)
        if self._tray:
            self._tray.stop()
        self.destroy()


if __name__ == "__main__":
    if activate_existing_window():
        sys.exit(0)
    app = App()
    app.mainloop()
