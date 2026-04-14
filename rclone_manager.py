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

# ── pystray + PIL import ──────────────────────────────────────────────────────
# ImportError 외에 ValueError(GTK 없음 등) 도 잡아야 하므로 Exception으로 처리
try:
    import pystray
    from PIL import Image, ImageDraw
    _TRAY_AVAILABLE = True
except Exception:
    pystray = None
    _TRAY_AVAILABLE = False
# ─────────────────────────────────────────────────────────────────────────────

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


def get_logical_screen_size():
    """
    Tkinter 기준 논리 해상도 반환.
    Tkinter 창 크기는 논리 픽셀 단위이므로 물리 해상도를 DPI 배율로 나눈 값.

    예시:
      1920x1080 @100%  → 논리 1920x1080
      1920x1080 @125%  → 논리 1536x 864
      2560x1440 @150%  → 논리 1706x 960
      2736x1824 @175%  → 논리 1563x1042
      3840x2160 @200%  → 논리 1920x1080
      3840x2160 @150%  → 논리 2560x1440
    """
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
    현재 화면 논리 해상도의 w_pct%, h_pct% 크기 창을 계산한다.
    1920×1080 기준값 없이, 실행 중인 화면에 맞게 직접 계산.

    검증된 비율 (2736x1824 @175% 기준, 논리 1563x1042):
      메인 창          → calc_window_size(40, 48)  → 약 625x500
      마운트 추가/편집  → calc_window_size(34, 82)  → 약 531x854
      업데이트 확인     → calc_window_size(34, 56)  → 약 531x583
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
    앱 자체 업데이트.

    [권한 문제 해결]
    실행 중인 .exe 는 Windows 에서 직접 rename / 덮어쓰기 불가(PermissionError).
    해결책:
      1. 새 파일을 %TEMP% 하위 임시 폴더에 다운로드
      2. updater.bat 작성: 현재 프로세스 종료 후 대기 → 파일 교체 → 재시작
      3. 배치 파일을 독립 프로세스로 실행 → 현재 프로세스는 "restart" 반환 후 종료

    zip 배포인 경우: 압축 해제만 수행 (True 반환, 호출자가 재시작 안내).

    반환값:
      "restart" - 배치 스크립트 실행됨, 호출자는 프로세스를 바로 종료해야 함
      True       - zip 압축 해제 완료 (재시작 필요 안내)
      str        - 오류 메시지
    """
    try:
        suffix = "." + asset_url.rsplit(".", 1)[-1] if "." in asset_url else ".zip"
        tmp_dir = Path(tempfile.mkdtemp())
        tmp_file = tmp_dir / f"update{suffix}"

        # ── 다운로드 ──────────────────────────────────────────────────────────
        r = requests.get(asset_url, stream=True, timeout=60)
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(tmp_file, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb and total:
                    progress_cb(int(downloaded * 100 / total))

        # ── zip 배포: 압축 해제 후 완료 ──────────────────────────────────────
        if suffix.lower() == ".zip":
            dest_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else APP_DIR
            with zipfile.ZipFile(tmp_file, "r") as z:
                z.extractall(dest_dir)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return True

        # ── exe 배포 ─────────────────────────────────────────────────────────
        # frozen(PyInstaller) 여부와 무관하게 항상 배치 스크립트 방식 사용.
        # 이유: frozen이든 아니든 .exe 파일이 실행 중이면 rename/overwrite 불가.
        cur_exe = Path(sys.executable) if getattr(sys, 'frozen', False) else APP_DIR / "rclone_mount_manager.exe"
        old_exe = cur_exe.with_suffix(".old")
        bat_path = tmp_dir / "updater.bat"

        # 배치 파일: ASCII로 작성해야 cmd.exe 가 깨지지 않음
        bat_lines = [
            "@echo off",
            "chcp 65001 > nul",
            ":: RcloneManager Updater",
            ":: 현재 프로세스가 완전히 종료될 때까지 3초 대기",
            "ping -n 4 127.0.0.1 > nul",
            f'if exist "{old_exe}" del /f /q "{old_exe}"',
            f'move /y "{cur_exe}" "{old_exe}"',
            "if errorlevel 1 (",
            "    echo Update failed: cannot rename current exe",
            "    pause",
            "    exit /b 1",
            ")",
            f'copy /y "{tmp_file}" "{cur_exe}"',
            "if errorlevel 1 (",
            f'    move /y "{old_exe}" "{cur_exe}"',
            "    echo Update failed: cannot copy new exe",
            "    pause",
            "    exit /b 1",
            ")",
            f'del /f /q "{tmp_file}"',
            f'rmdir /s /q "{tmp_dir}"',
            f'start "" "{cur_exe}"',
            "exit /b 0",
        ]
        bat_path.write_text("\r\n".join(bat_lines), encoding="utf-8")

        # DETACHED_PROCESS: 현재 콘솔과 완전히 분리, CREATE_NO_WINDOW: 창 없이
        subprocess.Popen(
            ["cmd.exe", "/c", str(bat_path)],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            close_fds=True,
        )
        return "restart"

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
    """등록된 rclone 경로만 반환. 없거나 파일 없으면 None."""
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
    - 업데이트 내역 필드에만 스크롤바 (내용이 필드보다 길 때만 자동 표시)
    - 현재 화면 논리 해상도의 38% × 60%
    - grid 레이아웃: 버튼 영역 항상 하단 고정
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
        tk.Button(btn_f, text="업데이트", bg="#a6e3a1", fg="#1e1e2e",
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
    """
    마운트 추가/편집 다이얼로그.
    현재 화면 논리 해상도의 38% × 88%.
    스크롤바 없이 모든 내용이 한 화면에 보임.
    """

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
        self.configure(bg="#1e1e2e")
        self.protocol("WM_DELETE_WINDOW", self.hide_window)

        self._cfg = load_config()
        self._status = {}
        self._latest_rc = ""
        self._latest_app_info = None
        self._version_check_running = False
        self._geometry_save_after = None  # 창 크기 저장 타이머 ID

        # ── 창 크기 복원 또는 초기 계산 ────────────────────────────────────
        # 사용자가 조정한 크기를 mounts.json > window_geometry 키로 저장/복원.
        # 기본 크기로 되돌리려면 mounts.json 에서 window_geometry 항목을 삭제.
        # 기본 비율: 논리 화면의 40%x48%
        #   2736x1824 @175% → 논리 1563x1042 → 약 625x500
        #   1920x1080 @100% → 논리 1920x1080 → 약 768x518
        saved_geo = self._cfg.get("window_geometry", "")
        if saved_geo and self._is_valid_geometry(saved_geo):
            self.geometry(saved_geo)
        else:
            mw, mh = calc_window_size(40, 48, min_w=620, min_h=480)
            self.geometry(f"{mw}x{mh}")

        self.minsize(560, 420)
        self.resizable(True, True)
        # ──────────────────────────────────────────────────────────────────────

        self._build_ui()
        self._refresh_list()
        self._start_tray()

        self._init_rc_label()
        self._check_versions_async()

        self.bind("<FocusIn>", self._on_focus_in)
        # 창 크기/위치 변경 시 저장 (디바운스: 500ms 후 저장)
        self.bind("<Configure>", self._on_configure)

        if self._cfg.get("auto_mount"):
            self.after(1500, self._automount_all)

    @staticmethod
    def _is_valid_geometry(geo: str) -> bool:
        """저장된 geometry 문자열이 유효한지 확인"""
        try:
            # WxH 또는 WxH+X+Y 형식 검증
            m = re.match(r"^(\d+)x(\d+)", geo)
            if not m:
                return False
            w, h = int(m.group(1)), int(m.group(2))
            lw, lh = get_logical_screen_size()
            # 현재 화면보다 크거나 너무 작으면 무효
            return 400 <= w <= lw and 300 <= h <= lh
        except Exception:
            return False

    def _on_configure(self, event):
        """창 크기/위치 변경 시 호출 - 디바운스 후 저장"""
        if event.widget is not self:
            return
        # 이전 저장 타이머 취소
        if self._geometry_save_after:
            self.after_cancel(self._geometry_save_after)
        # 500ms 후 저장
        self._geometry_save_after = self.after(500, self._save_geometry)

    def _save_geometry(self):
        """현재 창 크기/위치를 설정에 저장"""
        self._geometry_save_after = None
        geo = self.geometry()
        self._cfg["window_geometry"] = geo
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
                        command=self._toggle_am).pack(side="left")

        # 트리뷰 (5컬럼)
        cols = ("type", "auto", "drive", "remote", "status")
        self._tree = ttk.Treeview(self, columns=cols, show="headings", height=14)
        for col, head, cw, stretch in zip(
                cols,
                ("구분", "자동", "드라이브", "리모트 (서브경로)", "상태"),
                (70, 50, 75, 0, 85),
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
        exe = get_rclone_exe(self._cfg)
        if exe is None:
            self._rc_ver_label.config(text="rclone 다운로드", fg="#f38ba8")
        else:
            self._rc_ver_label.config(text="v체크 중...", fg="#94e2d5")

    def _check_rclone_presence(self):
        exe = get_rclone_exe(self._cfg)
        if exe is None:
            self._rc_ver_label.config(text="rclone 다운로드", fg="#f38ba8")
        else:
            self._rc_ver_label.config(text="v체크 중...", fg="#94e2d5")
            self._version_check_running = False
            self._check_versions_async()

    def _on_focus_in(self, event):
        if event.widget is self:
            self._check_rclone_presence()

    def _check_versions_async(self):
        if self._version_check_running:
            return
        self._version_check_running = True

        def _task():
            try:
                exe = get_rclone_exe(self._cfg)
                lat_rc = ""

                try:
                    res = requests.get(
                        "https://api.github.com/repos/rclone/rclone/releases/latest",
                        timeout=5)
                    lat_rc = res.json().get("tag_name", "").lstrip("v")
                    self._latest_rc = lat_rc
                except Exception:
                    pass

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

                try:
                    res = requests.get(
                        f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                        timeout=5)
                    data = res.json()
                    latest_app = data.get("tag_name", "").lstrip("v")
                    self._latest_app_info = data
                    if latest_app > APP_VERSION:
                        self.after(0, self._show_app_update_btn)
                    else:
                        self.after(0, self._hide_app_update_btn)
                except Exception:
                    pass
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

            if res == "restart":
                self.after(0, lambda: messagebox.showinfo(
                    "업데이트",
                    "업데이트 파일을 내려받았습니다.\n"
                    "프로그램이 종료된 후 자동으로 교체되고 재시작됩니다."))
                self.after(500, self._quit_app)
            elif res is True:
                self.after(0, lambda: messagebox.showinfo(
                    "업데이트 완료",
                    "업데이트가 완료되었습니다.\n프로그램을 재시작해 주세요."))
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
        registered = self._cfg.get("rclone_path", "").strip()
        dest_dir = Path(registered).parent if registered else APP_DIR

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
        """
        트레이 아이콘 초기화.
        pystray + PIL(Pillow) 모두 사용 가능할 때만 동작.
        import 실패(ImportError, ValueError 등) 는 모듈 상단에서 이미 처리됨.
        """
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
        트레이 우클릭 메뉴를 구성한다.

        주의: pystray.MenuItem 은 icon= 파라미터를 지원하지 않는다.
        icon= 을 넘기면 TypeError 가 발생해 트레이가 조용히 실패한다.
        마운트 상태는 텍스트(🟢/⚫)로만 표시한다.
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
                is_mounted = mid in active_mounts
                label = m.get("drive", "") or m.get("remote", "?")
                rstr  = f"{m['remote']}:{m.get('remote_path', '')}".strip(":")
                display = f"{'🟢' if is_mounted else '⚫'}  {label}  ({rstr})"

                def _make_toggle(mount_id, mount_data, mounted):
                    def _toggle(icon, item):
                        if mounted:
                            unmount(mount_id)
                            self.after(0, self._refresh_list)
                        else:
                            self.after(0, lambda: self._do_mount(mount_id, mount_data))
                    return _toggle

                items.append(
                    pystray.MenuItem(display, _make_toggle(mid, m, is_mounted))
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
                                      f"[{r['type']}] {r['name']}", "설정 대기"),
                              tags=("remote_tag",))
        for m in self._cfg.get("mounts", []):
            st = self._status.get(m["id"], "stopped")
            auto = "✅" if m.get("auto_mount") else "—"
            lbl  = "🟢 실행중" if st == "mounted" else "⚫ 중지됨"
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
