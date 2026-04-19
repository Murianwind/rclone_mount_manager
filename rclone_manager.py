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
import time
import requests
import zipfile
import tempfile
import re
import configparser
import uuid
import webbrowser
from pathlib import Path
import ctypes

try:
    import winreg
except ImportError:
    winreg = None

# pystray + PIL: 함께 import, ImportError 외 ValueError 등도 처리
try:
    import pystray
    from PIL import Image, ImageDraw
    _TRAY_AVAILABLE = True
except Exception:
    pystray = None
    _TRAY_AVAILABLE = False

# ── 프로그램 설정 ──
APP_VERSION = "1.1.0"
GITHUB_REPO = "Murianwind/rclone_mount_manager"
# GitHub API 버전 체크 주기 (초 단위, 86400 = 24시간)
VERSION_CHECK_INTERVAL = 86400

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


def get_logical_screen_size():
    """Tkinter 기준 논리 해상도 = 물리 해상도 / DPI 배율"""
    sw, sh = get_screen_size()
    scale = get_dpi_scale()
    return int(sw / scale), int(sh / scale)


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


def calc_window_size(w_pct, h_pct, min_w=400, min_h=300):
    """
    현재 화면 논리 해상도의 w_pct%, h_pct% 크기 창을 계산.
    두 기준점 (사용자 지정 환경별 선호 크기):
      1920x1080 @100% → 목표 792x683  (논리화면의 41%x63%)
      2736x1824 @175% → 목표 1300x833 (논리화면의 83%x80%)
    단일 비율로 두 환경을 동시에 만족시킬 수 없으므로
    논리 화면의 55%x65%를 기본으로 사용한다.
    처음 실행 후 사용자가 창 크기 조정 시 저장/복원된다.
    """
    lw, lh = get_logical_screen_size()
    w = max(min_w, int(lw * w_pct / 100))
    h = max(min_h, int(lh * h_pct / 100))
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
    """
    rclone 다운로드 및 설치.

    반환값:
      True     - 설치 완료
      "manual" - 파일락으로 교체 불가, rclone_new.exe로 저장함
      str      - 오류 메시지
    """
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

        # zip에서 rclone.exe 추출
        rclone_data = None
        with zipfile.ZipFile(tmp, "r") as z:
            for name in z.namelist():
                if name.endswith("rclone.exe"):
                    rclone_data = z.read(name)
                    break
        os.unlink(tmp)

        if rclone_data is None:
            return "zip 파일에서 rclone.exe를 찾을 수 없습니다."

        # rclone.exe 교체 시도
        target = dest_dir / "rclone.exe"
        try:
            target.write_bytes(rclone_data)
            return True
        except PermissionError:
            # 다른 프로그램이 rclone.exe를 사용 중
            # → 프로그램 실행 폴더(APP_DIR)에 rclone_new.exe로 저장
            new_target = APP_DIR / "rclone_new.exe"
            new_target.write_bytes(rclone_data)
            return "manual"

    except Exception as e:
        return str(e)


def download_app_release(asset_url: str, progress_cb=None):
    """
    앱 자체 업데이트 파일 다운로드.

    Windows에서 실행 중인 exe는 OS 파일락으로 자동 교체가 불가능합니다.
    → 업데이트 파일을 프로그램과 같은 폴더에 다운로드하고 수동 교체 안내.

    반환값:
      "manual" - 다운로드 완료, 수동 교체 안내
      str      - 오류 메시지
    """
    try:
        suffix = "." + asset_url.rsplit(".", 1)[-1] if "." in asset_url else ".zip"

        # 프로그램과 같은 폴더에 저장
        dest_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else APP_DIR
        dest_file = dest_dir / f"RcloneManager_update{suffix}"

        # 다운로드
        r = requests.get(asset_url, stream=True, timeout=60)
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(dest_file, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb and total:
                    progress_cb(int(downloaded * 100 / total))

        return "manual"

    except Exception as e:
        return str(e)


# ── 3. 설정 관리 ──
def _ver_tuple(v: str):
    """
    버전 문자열을 정수 튜플로 변환하여 올바른 버전 비교를 수행한다.
    문자열 비교는 '1.68.2' < '1.68.10' 이 False가 되는 버그가 있음.
    예: '1.68.10' → (1, 68, 10),  '1.9.0' → (1, 9, 0)
    """
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except Exception:
        return (0,)


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
    return {"remotes": [], "mounts": [], "rclone_path": "", "auto_mount": False, "start_minimized": False}


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_rclone_exe(cfg):
    """
    등록된 rclone 경로 반환.
    1순위: cfg의 rclone_path (등록된 경로)
    2순위: APP_DIR/rclone.exe (같은 폴더의 rclone)
    없으면 None 반환.
    """
    p = cfg.get("rclone_path", "").strip()
    if p and Path(p).exists():
        return Path(p)
    # 같은 폴더에 rclone.exe가 있으면 사용 (원본 동작 복구)
    fallback = APP_DIR / "rclone.exe"
    if fallback.exists():
        return fallback
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


# 의도적 언마운트 중인 ID 집합 (오류 메시지 억제용)
_unmounting = set()

def unmount(mid):
    """
    마운트 프로세스를 종료한다.
    _unmounting에 mid를 추가해 의도적 종료임을 표시한다.
    discard는 _mount_task의 finally에서만 수행하여
    타이밍 경쟁 조건(race condition)을 방지한다.
    """
    p = active_mounts.get(mid)
    if p:
        _unmounting.add(mid)   # 의도적 종료 표시 (discard는 _mount_task finally에서)
        p.terminate()
        try:
            p.wait(timeout=3)
        except Exception:
            p.kill()
        active_mounts.pop(mid, None)
        # _unmounting.discard 는 여기서 하지 않음
        # → _mount_task의 p.wait() 반환 후 finally에서만 discard
        #   (unmount()가 먼저 discard하면 _mount_task가 오류로 인식하는 race condition 발생)


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
    - 업데이트 내역 필드에만 조건부 스크롤바
    - grid 레이아웃: 버튼 항상 하단 고정
    """

    def __init__(self, parent, tag, body, assets=None):
        super().__init__(parent)
        self.title(f"업데이트 확인 - {tag}")
        self.configure(bg="#1e1e2e")
        self.grab_set()
        self.confirmed = False
        self._asset_url = self._pick_asset(assets or [])

        w, h = calc_window_size(34, 56, min_w=480, min_h=360)
        self.geometry(f"{w}x{h}")
        self.minsize(480, 360)
        self.resizable(True, True)

        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=0)
        self.columnconfigure(0, weight=1)

        tk.Label(self,
                 text=f"✨ 새 버전({tag})으로 업데이트하시겠습니까?",
                 bg="#1e1e2e", fg="#cba6f7",
                 font=("Segoe UI", 11, "bold"),
                 wraplength=w - 40).grid(row=0, column=0, sticky="ew",
                                         padx=20, pady=(16, 8))

        txt_frame = tk.Frame(self, bg="#1e1e2e")
        txt_frame.grid(row=1, column=0, sticky="nsew", padx=20, pady=4)
        txt_frame.rowconfigure(0, weight=1)
        txt_frame.columnconfigure(0, weight=1)

        vsb = tk.Scrollbar(txt_frame)
        txt = tk.Text(txt_frame, bg="#313244", fg="#cdd6f4", relief="flat",
                      font=("Segoe UI", 10), padx=10, pady=10, wrap="word")
        txt.grid(row=0, column=0, sticky="nsew")
        txt.insert("1.0", body)
        txt.config(state="disabled")

        # 내용이 필드보다 길 때만 스크롤바 표시
        def _maybe_show_scrollbar(event=None):
            _, last = txt.yview()
            if last < 1.0:
                vsb.grid(row=0, column=1, sticky="ns")
                txt.config(yscrollcommand=vsb.set)
                vsb.config(command=txt.yview)
            else:
                vsb.grid_remove()

        txt.bind("<Configure>", _maybe_show_scrollbar)
        self.after(100, _maybe_show_scrollbar)

        btn_f = tk.Frame(self, bg="#1e1e2e")
        btn_f.grid(row=2, column=0, sticky="ew", padx=20, pady=(8, 16))
        tk.Button(btn_f, text="다운로드", bg="#a6e3a1", fg="#1e1e2e",
                  font=("Segoe UI", 10, "bold"), relief="flat",
                  command=self._ok, width=14).pack(side="right", padx=5, ipady=5)
        tk.Button(btn_f, text="취소", bg="#45475a", fg="#cdd6f4",
                  font=("Segoe UI", 10, "bold"), relief="flat",
                  command=self.destroy, width=14).pack(side="right", padx=5, ipady=5)

    @staticmethod
    def _pick_asset(assets):
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

        w, h = calc_window_size(34, 82, min_w=490, min_h=580)
        self.geometry(f"{w}x{h}")
        self.minsize(490, 580)
        self.resizable(True, True)
        self.configure(bg="#1e1e2e")
        self.grab_set()
        self.result = None
        self._m = mount or {}
        self._app_cfg = app_cfg
        self._build()

    def _build(self):
        c = tk.Frame(self, padx=26, pady=16, bg="#1e1e2e")
        c.pack(fill="both", expand=True)

        lbl_s = {"bg": "#1e1e2e", "fg": "#cba6f7", "font": ("Segoe UI", 10, "bold")}
        ent_s = {"bg": "#313244", "fg": "#cdd6f4", "insertbackground": "#cdd6f4",
                 "relief": "flat", "font": ("Segoe UI", 10)}

        tk.Label(c, text="리모트 이름", **lbl_s).pack(anchor="w")
        self._rem = tk.Entry(c, **ent_s)
        self._rem.pack(fill="x", pady=(4, 11), ipady=4)
        self._rem.insert(0, self._m.get("remote", ""))

        tk.Label(c, text="서브 디렉토리", **lbl_s).pack(anchor="w")
        pth_f = tk.Frame(c, bg="#1e1e2e")
        pth_f.pack(fill="x", pady=(4, 11))
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
        self._drv.pack(fill="x", pady=(4, 11))
        self._drv.set(self._m.get("drive", ""))

        tk.Label(c, text="캐시 디렉토리", **lbl_s).pack(anchor="w")
        cdir_f = tk.Frame(c, bg="#1e1e2e")
        cdir_f.pack(fill="x", pady=(4, 11))
        self._cdir = tk.Entry(cdir_f, **ent_s)
        self._cdir.pack(side="left", fill="x", expand=True, ipady=4)
        self._cdir.insert(0, self._m.get("cache_dir", ""))
        tk.Button(cdir_f, text="📂", bg="#45475a", fg="#cdd6f4", relief="flat",
                  command=self._browse_cache).pack(side="left", padx=(5, 0))

        tk.Label(c, text="캐시 모드", **lbl_s).pack(anchor="w")
        self._cmode = ttk.Combobox(c, values=["off", "minimal", "writes", "full"],
                                    font=("Segoe UI", 10), state="readonly")
        self._cmode.pack(fill="x", pady=(4, 11))
        self._cmode.set(self._m.get("cache_mode", "full"))

        tk.Label(c, text="추가 플래그", **lbl_s).pack(anchor="w")
        self._ext = tk.Text(c, height=4, **ent_s)
        self._ext.pack(fill="x", pady=(4, 11))
        self._ext.insert("1.0", self._m.get("extra_flags", ""))

        self._auto = tk.BooleanVar(value=self._m.get("auto_mount", False))
        tk.Checkbutton(c, text="시작 시 자동 마운트", variable=self._auto,
                       bg="#1e1e2e", fg="#cdd6f4", selectcolor="#313244",
                       font=("Segoe UI", 10)).pack(anchor="w", pady=5)

        btn_f = tk.Frame(c, bg="#1e1e2e")
        btn_f.pack(fill="x", pady=(14, 0))
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
            return messagebox.showinfo("알림", "rclone 경로가 등록되어 있지 않습니다.")

        def r():
            try:
                p = subprocess.run([str(exe), "lsf", target, "--max-depth", "1"],
                                   capture_output=True, text=True, timeout=10,
                                   creationflags=0x08000000)
                if p.returncode == 0:
                    messagebox.showinfo("성공", "연결 확인 완료!")
                else:
                    messagebox.showinfo("연결 실패", f"연결 불가:\n{p.stderr.strip()}")
            except Exception as e:
                messagebox.showinfo("알림", str(e))

        threading.Thread(target=r, daemon=True).start()

    def _save(self):
        rem, drv, pth = self._rem.get().strip(), self._drv.get(), self._pth.get().strip()
        if not rem:
            return messagebox.showinfo("알림", "리모트 이름을 입력해 주세요.")
        for m in self._app_cfg.get("mounts", []):
            if m.get("id") == self._m.get("id"):
                continue
            if drv and m.get("drive") == drv:
                return messagebox.showinfo("알림", "이미 사용 중인 드라이브 문자입니다.")
            if m.get("remote") == rem and m.get("remote_path", "") == pth:
                return messagebox.showinfo("알림", "동일한 리모트/경로가 이미 등록되어 있습니다.")
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
        self.configure(bg="#1e1e2e")
        self.protocol("WM_DELETE_WINDOW", self.hide_window)

        self._cfg = load_config()
        self._status = {}
        self._latest_rc = ""
        self._latest_app_info = None
        self._version_check_running = False
        self._geometry_save_after = None

        # ── 창 크기 복원 또는 초기 계산 ──────────────────────────────────────
        # 저장된 크기: 복원
        # 없으면: UI 구성 후 Tkinter 자동 측정으로 적절한 크기 결정
        self._saved_geo = self._cfg.get("window_geometry", "")
        self.minsize(560, 420)
        self.resizable(True, True)
        # ─────────────────────────────────────────────────────────────────────

        self._build_ui()
        self._refresh_list()
        self._start_tray()

        # UI 구성 완료 후 창 크기 결정
        # 저장된 크기 있으면 복원, 없으면 Tkinter가 필요 크기 측정 후 여유 추가
        if self._saved_geo and self._is_valid_geometry(self._saved_geo):
            self.geometry(self._saved_geo)
        else:
            self._auto_size_window()

        self._init_rc_label()
        self._check_versions_async()

        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<Configure>", self._on_configure)

        if self._cfg.get("auto_mount"):
            self.after(1500, self._automount_all)

        # 시작 시 트레이로 최소화
        if self._cfg.get("start_minimized"):
            self.after(100, self.hide_window)

    @staticmethod
    def _is_valid_geometry(geo: str) -> bool:
        """저장된 geometry 문자열이 유효한지 확인 (최솟값만 체크)"""
        try:
            m = re.match(r"^(\d+)x(\d+)", geo)
            if not m:
                return False
            w, h = int(m.group(1)), int(m.group(2))
            return w >= 400 and h >= 300
        except Exception:
            return False

    def _auto_size_window(self):
        """
        저장된 크기가 없을 때 Tkinter 자동 측정으로 창 크기 결정.

        원리:
          1. update_idletasks()로 모든 위젯 렌더링 완료
          2. winfo_reqwidth/reqheight로 실제 필요 최소 크기 측정
          3. 여유 공간 추가 + 화면 90% 초과 방지
          → DPI/폰트 크기에 관계없이 내용이 항상 화면에 맞게 표시됨
        """
        self.update_idletasks()
        req_w = self.winfo_reqwidth()
        req_h = self.winfo_reqheight()
        lw, lh = get_logical_screen_size()
        # 측정값에 여유 공간 추가 (트리뷰 높이 확보 등)
        w = min(req_w + 100, int(lw * 0.90))
        h = min(req_h + 80,  int(lh * 0.90))
        # 최솟값 보장
        w = max(w, 780)
        h = max(h, 520)
        # 화면 중앙 배치
        x = max(0, (lw - w) // 2)
        y = max(0, (lh - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _on_configure(self, event):
        """창 크기/위치 변경 시 디바운스 후 저장"""
        if event.widget is not self:
            return
        if self._geometry_save_after:
            self.after_cancel(self._geometry_save_after)
        self._geometry_save_after = self.after(500, self._save_geometry)

    def _save_geometry(self):
        """현재 창 크기/위치 저장"""
        self._geometry_save_after = None
        self._cfg["window_geometry"] = self.geometry()
        save_config(self._cfg)

    def _on_column_resize(self, event=None):
        """컬럼 폭 조절 후 저장 (헤더 드래그 or ButtonRelease)"""
        widths = {}
        for col in ("type", "auto", "drive", "status"):
            try:
                widths[col] = self._tree.column(col, "width")
            except Exception:
                pass
        if widths:
            self._cfg["column_widths"] = widths
            save_config(self._cfg)

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
                        command=self._toggle_am).pack(side="left", padx=(0, 24))
        self._min_var = tk.BooleanVar(value=self._cfg.get("start_minimized", False))
        ttk.Checkbutton(opt, text="시작 시 트레이로 최소화", variable=self._min_var,
                        command=self._toggle_min).pack(side="left")

        # 트리뷰 (5컬럼)
        cols = ("type", "auto", "drive", "remote", "status")
        # 기본 폭: 상태는 170(기존 85의 2배), 저장된 값 있으면 복원
        self._col_default_widths = {"type": 70, "auto": 50, "drive": 75, "status": 170}
        saved_cw = self._cfg.get("column_widths", {})
        self._tree = ttk.Treeview(self, columns=cols, show="headings", height=14)
        for col, head, anchor, stretch in zip(
                cols,
                ("구분", "자동", "드라이브", "리모트 (서브경로)", "상태"),
                ("w", "center", "center", "w", "w"),
                (False, False, False, True, True)):
            self._tree.heading(col, text=head, anchor=anchor)
            if not stretch:
                cw = saved_cw.get(col, self._col_default_widths[col])
                self._tree.column(col, width=cw, minwidth=40,
                                  stretch=False, anchor=anchor)
            else:
                cw = saved_cw.get(col, self._col_default_widths.get(col, 80))
                self._tree.column(col, width=cw, minwidth=80,
                                  stretch=True, anchor=anchor)
        self._tree.pack(fill="both", expand=True, padx=20, pady=5)
        self._tree.tag_configure("remote_tag", foreground="#8fa0b5")
        # <<TreeviewColumnRelease>>: 헤더 드래그 완료 이벤트
        # <ButtonRelease-1>: 헤더 클릭/드래그 완료 후 폭 저장 (이중 바인딩으로 확실히 감지)
        self._tree.bind("<<TreeviewColumnRelease>>", self._on_column_resize)
        self._tree.bind("<ButtonRelease-1>", lambda e: self.after(100, self._on_column_resize))

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
        """초기 rclone 상태 레이블 설정"""
        exe = get_rclone_exe(self._cfg)
        if exe is None:
            self._rc_ver_label.config(text="rclone 다운로드", fg="#f38ba8")
        else:
            self._rc_ver_label.config(text="v체크 중...", fg="#94e2d5")

    def _check_rclone_presence(self):
        """
        등록된 rclone 존재 여부 즉시 확인.
        - 등록 경로가 사라졌으면 경로 초기화 + 다운로드 표시 (요구사항 5)
        - 있으면 버전 체크
        """
        registered = self._cfg.get("rclone_path", "").strip()
        exe = get_rclone_exe(self._cfg)

        if exe is None:
            # 등록된 경로가 있었는데 파일이 사라진 경우 → 경로 초기화
            if registered:
                self._cfg["rclone_path"] = ""
                self._rc_var.set("")
                save_config(self._cfg)
            self._rc_ver_label.config(text="rclone 다운로드", fg="#f38ba8")
        else:
            self._rc_ver_label.config(text="v체크 중...", fg="#94e2d5")
            self._version_check_running = False
            self._check_versions_async(force=True)

    def _on_focus_in(self, event):
        """창 활성화 시 rclone + 앱 버전 재확인"""
        if event.widget is self:
            self._check_rclone_presence()

    def _check_versions_async(self, force: bool = False):
        """
        백그라운드에서 rclone 및 앱 버전 확인.

        GitHub API rate limit 대응 (비인증: 60회/시간/IP):
          - rclone 버전: 매번 체크 (실행 시, 창 활성화 시)
            이유: rclone은 자주 업데이트되고 사용자가 직접 인지해야 함
          - 앱 버전:     24시간 주기로만 체크 (last_version_check 타임스탬프)
            이유: 앱 업데이트는 빈도가 낮고 rate limit 절약
          - force=True이면 앱 버전도 주기 무관하게 즉시 체크
        """
        if self._version_check_running:
            return

        # 앱 버전만 24시간 주기 체크
        now = time.time()
        last_check = self._cfg.get("last_version_check", 0)
        skip_app_api = (not force) and (now - last_check < VERSION_CHECK_INTERVAL)

        self._version_check_running = True

        def _task():
            try:
                exe = get_rclone_exe(self._cfg)
                lat_rc = ""

                # rclone 최신 버전은 항상 조회 (rate limit 영향 적음: 1회/실행)
                try:
                    res = requests.get(
                        "https://api.github.com/repos/rclone/rclone/releases/latest",
                        timeout=5)
                    lat_rc = res.json().get("tag_name", "").lstrip("v")
                    self._latest_rc = lat_rc
                except Exception:
                    lat_rc = self._latest_rc  # 실패 시 이전 값 재사용

                # 앱 업데이트는 24시간 주기로만 확인
                if not skip_app_api:
                    try:
                        res = requests.get(
                            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                            timeout=5)
                        data = res.json()
                        latest_app = data.get("tag_name", "").lstrip("v")
                        self._latest_app_info = data
                        if _ver_tuple(latest_app) > _ver_tuple(APP_VERSION):
                            self.after(0, self._show_app_update_btn)
                        else:
                            self.after(0, self._hide_app_update_btn)
                    except Exception:
                        pass

                    # 앱 체크 완료 시각 저장
                    self._cfg["last_version_check"] = now
                    save_config(self._cfg)
                else:
                    # 앱 API 스킵: 기존에 저장된 정보로 버튼 상태 복원
                    if self._latest_app_info:
                        latest_app = self._latest_app_info.get("tag_name", "").lstrip("v")
                        if _ver_tuple(latest_app) > _ver_tuple(APP_VERSION):
                            self.after(0, self._show_app_update_btn)

                # rclone 로컬 버전 표시 (로컬 실행, API 호출 없음)
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
                            if lat_rc and _ver_tuple(loc_rc) < _ver_tuple(lat_rc):
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
            finally:
                self._version_check_running = False

        threading.Thread(target=_task, daemon=True).start()

    def _show_app_update_btn(self):
        if not self._app_up_btn.winfo_ismapped():
            self._app_up_btn.pack(side="right")

    def _hide_app_update_btn(self):
        if self._app_up_btn.winfo_ismapped():
            self._app_up_btn.pack_forget()

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
            webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")
            return

        self._app_up_btn.config(text="앱 업데이트 중... 0%", state="disabled")

        def _do():
            def _prog(p):
                self.after(0, lambda pv=p: self._app_up_btn.config(
                    text=f"앱 업데이트 중... {pv}%"))

            res = download_app_release(asset_url, _prog)

            exe_dir = (Path(sys.executable).parent if getattr(sys, 'frozen', False)
                       else APP_DIR)
            suffix = "." + asset_url.rsplit(".", 1)[-1] if "." in asset_url else ".zip"
            dest_file = exe_dir / f"RcloneManager_update{suffix}"

            if res == "manual":
                self.after(0, lambda df=str(dest_file): messagebox.showinfo(
                    "업데이트 파일 다운로드 완료",
                    "Windows 보안 정책으로 인해 실행 중인 프로그램의\n"
                    "자동 교체가 불가능합니다.\n\n"
                    f"업데이트 파일 저장 위치:\n{df}\n\n"
                    "프로그램을 종료한 후 기존 파일을 새 파일로 교체하고 재시작하세요."))
                self.after(0, lambda: self._app_up_btn.config(
                    text="✨ 새 버전 업데이트 가능", state="normal"))
            else:
                self.after(0, lambda err=res: messagebox.showinfo("알림", err))
                self.after(0, lambda: self._app_up_btn.config(
                    text="✨ 새 버전 업데이트 가능", state="normal"))

        threading.Thread(target=_do, daemon=True).start()

    # ────────────────────────────────────────────
    # rclone 다운로드/업데이트
    # ────────────────────────────────────────────
    def _handle_rc_click(self, event):
        text = self._rc_ver_label.cget("text")
        if "다운로드" in text:
            if not self._latest_rc:
                messagebox.showinfo("rclone",
                                    "최신 버전 정보를 확인 중입니다. 잠시 후 다시 시도해 주세요.")
                self._version_check_running = False
                self._check_versions_async(force=True)
                return
            if messagebox.askyesno("rclone", f"rclone v{self._latest_rc}를 설치할까요?"):
                threading.Thread(target=self._do_rc_down, daemon=True).start()
        elif "업데이트" in text:
            if not self._latest_rc:
                return
            # 마운트 중인 드라이브가 있으면 경고 후 확인
            mounted = [m for m in self._cfg.get("mounts", [])
                       if m["id"] in active_mounts]
            if mounted:
                names = ", ".join(
                    m.get("drive", "") or m.get("remote", "?")
                    for m in mounted)
                if not messagebox.askyesno(
                        "rclone 업데이트",
                        f"현재 마운트 중인 드라이브가 있습니다: {names}\n\n"
                        "업데이트하려면 마운트를 해제해야 합니다.\n"
                        "모두 해제하고 업데이트할까요?\n"
                        "(업데이트 완료 후 자동으로 재마운트됩니다)"):
                    return
            threading.Thread(target=self._do_rc_down, daemon=True).start()

    def _do_rc_down(self):
        registered = self._cfg.get("rclone_path", "").strip()
        dest_dir = Path(registered).parent if registered else APP_DIR

        # 마운트 중인 항목 기록 (업데이트 후 재마운트용)
        remount_list = [m for m in self._cfg.get("mounts", [])
                        if m["id"] in active_mounts]

        # 마운트 중인 항목 모두 해제
        if remount_list:
            self.after(0, lambda: self._rc_ver_label.config(
                text="마운트 해제 중...", fg="#89b4fa"))
            for m in remount_list:
                unmount(m["id"])
            self.after(0, self._refresh_list)

        self.after(0, lambda: self._rc_ver_label.config(
            text="다운로드 중... 0%", fg="#89b4fa"))

        def _prog(p):
            self.after(0, lambda pv=p: self._rc_ver_label.config(
                text=f"다운로드 중... {pv}%", fg="#89b4fa"))

        res = download_rclone(dest_dir, self._latest_rc, _prog)

        if res is True:
            # 교체 완료
            new_path = str(dest_dir / "rclone.exe")
            self._rc_var.set(new_path)
            self._cfg["rclone_path"] = new_path
            save_config(self._cfg)
            messagebox.showinfo("완료", "rclone 설치/업데이트 완료!")
            self._version_check_running = False
            self._check_versions_async(force=True)
            if remount_list:
                self.after(500, lambda: [self._do_mount(m["id"], m)
                                         for m in remount_list])

        elif res == "manual":
            # 다른 프로그램이 rclone을 사용 중 → 파일락으로 교체 불가
            new_file = APP_DIR / "rclone_new.exe"
            self.after(0, lambda nf=str(new_file): messagebox.showinfo(
                "수동 교체 필요",
                "다른 프로그램에서 rclone을 사용 중이어서\n"
                "자동 업데이트가 불가능합니다.\n\n"
                f"새 파일 저장 위치:\n{nf}\n\n"
                "해당 프로그램을 종료한 후\n"
                "rclone_new.exe 파일의 이름을 rclone.exe로 변경하고\n"
                "기존 rclone.exe를 교체해 주세요."))
            self.after(0, lambda: self._rc_ver_label.config(
                text="수동 교체 필요", fg="#fab387"))
            if remount_list:
                self.after(500, lambda: [self._do_mount(m["id"], m)
                                         for m in remount_list])

        else:
            # 다운로드 오류
            messagebox.showinfo("알림", str(res))
            self.after(0, lambda: self._rc_ver_label.config(
                text="rclone 다운로드", fg="#f38ba8"))
            if remount_list:
                self.after(500, lambda: [self._do_mount(m["id"], m)
                                         for m in remount_list])

    # ────────────────────────────────────────────
    # 이슈 리포트
    # ────────────────────────────────────────────
    def _open_issue(self):
        webbrowser.open(f"https://github.com/{GITHUB_REPO}/issues/new")

    # ────────────────────────────────────────────
    # 트레이
    # ────────────────────────────────────────────
    def _start_tray(self):
        if not _TRAY_AVAILABLE:
            return
        try:
            main_icon = _make_circle_icon("#cba6f7", 64)
            self._tray = pystray.Icon(
                "RcloneManager", main_icon, "RcloneManager",
                menu=self._build_tray_menu())
            threading.Thread(target=self._tray.run, daemon=True).start()
        except Exception:
            self._tray = None

    def _build_tray_menu(self):
        """
        트레이 우클릭 메뉴.
        pystray.MenuItem은 icon= 파라미터 미지원 → 이모지로 상태 표시.
        마운트 항목 클릭 시 현재 active_mounts에서 실시간으로 상태 확인.
        """
        if not _TRAY_AVAILABLE:
            return None

        items = [
            pystray.MenuItem("🪟 열기", lambda: self.after(0, self.show_window),
                             default=True),
            pystray.Menu.SEPARATOR,
        ]

        mounts = self._cfg.get("mounts", [])
        if mounts:
            for m in mounts:
                mid = m["id"]
                # _status=="mounted" OR active_mounts 둘 다 확인
                # 이유: _do_mount에서 _status를 먼저 설정하고 _refresh_list를 호출하지만
                #       active_mounts[mid]=p는 _mount_task 스레드에서 나중에 설정됨
                #       → _status를 우선 확인해야 트레이 이모지가 즉시 반영됨
                is_mounted = (self._status.get(mid) == "mounted") or (mid in active_mounts)
                label = m.get("drive", "") or m.get("remote", "?")
                rstr = f"{m['remote']}:{m.get('remote_path', '')}".strip(":")
                display = f"{'■' if is_mounted else '▶'}  {label}  ({rstr})"

                # 클릭 시 active_mounts와 _status 모두 확인하여 실시간 토글
                def _make_toggle(mount_id, mount_data):
                    def _toggle(icon, item):
                        currently_mounted = (
                            (self._status.get(mount_id) == "mounted") or
                            (mount_id in active_mounts)
                        )
                        if currently_mounted:
                            # 현재 마운트 중 → 언마운트
                            unmount(mount_id)
                            self.after(0, self._refresh_list)
                        else:
                            # 현재 언마운트 → 마운트
                            self.after(0, lambda: self._do_mount(mount_id, mount_data))
                    return _toggle

                items.append(
                    pystray.MenuItem(display, _make_toggle(mid, m))
                )
            items.append(pystray.Menu.SEPARATOR)
        else:
            items.append(pystray.MenuItem("(등록된 마운트 없음)", lambda *_: None,
                                          enabled=False))
            items.append(pystray.Menu.SEPARATOR)

        items.append(
            pystray.MenuItem("🚪 종료", lambda: self.after(0, self._quit_app))
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
                                      f"[{r['type']}] {r['name']}", ""),
                              tags=("remote_tag",))
        for m in self._cfg.get("mounts", []):
            st = self._status.get(m["id"], "stopped")
            auto = "✅" if m.get("auto_mount") else "—"
            lbl = "■ 실행중" if st == "mounted" else "▶ 중지됨"
            rstr = f"{m['remote']}:{m.get('remote_path', '')}".strip(":")
            self._tree.insert("", "end", iid=m["id"],
                              values=("💾 마운트", auto, m.get("drive", ""), rstr, lbl))
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

    def _toggle_min(self):
        self._cfg["start_minimized"] = self._min_var.get()
        save_config(self._cfg)

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
            messagebox.showinfo("알림", "rclone 경로가 등록되어 있지 않습니다.")
            return
        if mid in active_mounts:
            return  # 이미 마운트 중이면 무시
        self._status[mid] = "mounted"
        self._refresh_list()
        threading.Thread(target=self._mount_task, args=(mid, exe, m), daemon=True).start()

    def _mount_task(self, mid, exe, m):
        """
        rclone mount 실행.
        rclone이 즉시 종료(오류)되면 stderr를 캡처해 사용자에게 표시.
        unmount()로 의도적 종료된 경우(_unmounting)는 오류 표시 안 함.
        """
        try:
            p = subprocess.Popen(
                build_cmd(exe, m),
                stderr=subprocess.PIPE,
                creationflags=0x08000000
            )
            active_mounts[mid] = p
            p.wait()
            # 의도적 언마운트(terminate)가 아닌 경우에만 오류 표시
            if p.returncode != 0 and mid not in _unmounting:
                stderr_out = ""
                try:
                    stderr_out = p.stderr.read().decode("utf-8", errors="replace").strip()
                except Exception:
                    pass
                if stderr_out:
                    self.after(0, lambda msg=stderr_out: messagebox.showinfo(
                        "마운트 오류", f"rclone 오류:\n{msg[:500]}"))
        except Exception as e:
            if mid not in _unmounting:
                self.after(0, lambda err=str(e): messagebox.showinfo("마운트 오류", err))
        finally:
            active_mounts.pop(mid, None)
            _unmounting.discard(mid)
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
            self._refresh_list()

    # ────────────────────────────────────────────
    # 창 표시/숨김/종료/재시작
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
