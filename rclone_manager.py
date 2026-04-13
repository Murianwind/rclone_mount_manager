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
import re
import configparser
import uuid
import webbrowser
import urllib.parse
from pathlib import Path
import ctypes

# 테스트 및 런타임 호환성을 위해 모듈을 상단에서 임포트합니다.
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
APP_VERSION = "1.0.8"
GITHUB_REPO = "Murianwind/rclone_mount_manager"

# ── 1. 고해상도(DPI) 대응 및 시스템 정보 수집 ──
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

def get_sys_info():
    """사용자의 해상도 및 배율 정보 반환 (Scenario 20)"""
    try:
        user32 = ctypes.windll.user32
        w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        hdc = user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
        user32.ReleaseDC(0, hdc)
        return f"Resolution: {w}x{h}, Scaling: {int((dpi / 96) * 100)}%"
    except Exception: return "N/A"

# ── 2. 시작 프로그램 및 유틸리티 ──
def is_startup_enabled():
    """시작 프로그램 등록 여부 확인 (Scenario 08, 16)"""
    if not winreg: return False
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, "RcloneManager")
        winreg.CloseKey(key)
        return True
    except: return False

def set_startup(enable: bool):
    """시작 프로그램 등록/해제 (Scenario 16)"""
    if not winreg: return False
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        if enable:
            path = f'"{sys.executable}"' if getattr(sys, 'frozen', False) else f'pythonw "{Path(__file__).resolve()}"'
            winreg.SetValueEx(key, "RcloneManager", 0, winreg.REG_SZ, path)
        else:
            try: winreg.DeleteValue(key, "RcloneManager")
            except: pass
        winreg.CloseKey(key)
        return True
    except Exception as e: return str(e)

# ── 요구사항 1: conf 불러오기 유틸리티 복구 (1.0.0 로직) ──
def parse_rclone_conf(conf_path: Path):
    """(Scenario 24) rclone.conf 파일을 파싱하여 리모트 목록 반환"""
    remotes = []
    try:
        cfg = configparser.ConfigParser()
        cfg.read(str(conf_path), encoding="utf-8")
        for section in cfg.sections():
            remotes.append({"name": section, "type": cfg.get(section, "type", fallback="")})
    except Exception: pass
    return remotes

def find_default_rclone_conf():
    """시스템 기본 rclone.conf 경로 탐색"""
    for p in [Path(os.environ.get("APPDATA", "")) / "rclone" / "rclone.conf", Path.home() / ".config" / "rclone" / "rclone.conf", APP_DIR / "rclone.conf"]:
        if p.exists(): return p
    return None

def download_rclone(dest_dir: Path, version: str, progress_cb=None):
    """rclone 다운로드 및 설치 (Scenario 15)"""
    url = f"https://github.com/rclone/rclone/releases/download/v{version}/rclone-v{version}-windows-amd64.zip"
    try:
        r = requests.get(url, stream=True, timeout=30)
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        tmp = tempfile.mktemp(suffix=".zip")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb and total: progress_cb(int(downloaded * 100 / total))
        with zipfile.ZipFile(tmp, "r") as z:
            for name in z.namelist():
                if name.endswith("rclone.exe"):
                    data = z.read(name)
                    (dest_dir / "rclone.exe").write_bytes(data)
                    break
        os.unlink(tmp)
        return True
    except Exception as e: return str(e)

# ── 3. 설정 관리 ──
if getattr(sys, 'frozen', False): APP_DIR = Path(sys.executable).parent
else: APP_DIR = Path(__file__).parent
CONFIG_FILE = APP_DIR / "mounts.json"

def load_config():
    """(Scenario 05, 06) 설정 로드"""
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if "mounts" not in cfg: cfg["mounts"] = []
            if "remotes" not in cfg: cfg["remotes"] = []
            return cfg
        except: pass
    return {"remotes": [], "mounts": [], "rclone_path": "", "auto_mount": False}

def save_config(cfg):
    """(Scenario 07) 설정 저장"""
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def get_rclone_exe(cfg):
    """(Scenario 01) rclone 경로 확인"""
    p = cfg.get("rclone_path", "").strip()
    if p and Path(p).exists(): return Path(p)
    return APP_DIR / "rclone.exe"

active_mounts = {}

def build_cmd(exe: Path, mount: dict):
    """(Scenario 02, 03, 04) 명령어 빌드"""
    rpath = mount.get("remote_path", "").strip().replace("\\", "/").strip("/")
    drive_target = mount.get("drive", "").strip() or " "
    cmd = [str(exe), "mount", f"{mount['remote']}:{rpath}", drive_target, "--volname", mount.get("label") or mount["remote"]]
    if mount.get("cache_dir"): cmd += ["--cache-dir", mount["cache_dir"]]
    if mount.get("cache_mode"): cmd += ["--vfs-cache-mode", mount["cache_mode"]]
    extra = mount.get("extra_flags", "").strip()
    if extra:
        for f in re.split(r"[\s;]+|\n", extra):
            if f.strip(): cmd.append(f.strip())
    return cmd

def unmount(mid):
    """(Scenario 09) 언마운트 수행"""
    p = active_mounts.get(mid)
    if p:
        p.terminate()
        try: p.wait(timeout=3)
        except: p.kill()
        active_mounts.pop(mid, None)

def activate_existing_window():
    """(Scenario 10) 중복 실행 시 기존 창 활성화"""
    hwnd = ctypes.windll.user32.FindWindowW(None, "RcloneManager")
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        return True
    return False

# ── 요구사항 1: ConfImportDialog 클래스 복구 ──
class ConfImportDialog(tk.Toplevel):
    def __init__(self, parent, remotes):
        super().__init__(parent); self.title("리모트 선택"); self.grab_set(); self.configure(bg="#1e1e2e"); self.selected = []; self._remotes = remotes; self._vars = []
        tk.Label(self, text="가져올 리모트 선택:", bg="#1e1e2e", fg="#cba6f7", font=("Segoe UI", 10, "bold")).pack(padx=16, pady=10, anchor="w")
        for r in self._remotes:
            v = tk.BooleanVar(value=True); self._vars.append((v, r))
            row = tk.Frame(self, bg="#1e1e2e"); row.pack(fill="x", padx=16, pady=2)
            tk.Checkbutton(row, variable=v, bg="#1e1e2e", fg="#cdd6f4", selectcolor="#313244").pack(side="left")
            tk.Label(row, text=f"{r['name']} [{r['type']}]", bg="#1e1e2e", fg="#cdd6f4").pack(side="left")
        tk.Button(self, text="가져오기", bg="#cba6f7", fg="#1e1e2e", font=("Segoe UI", 10, "bold"), relief="flat", command=self._ok).pack(pady=10, ipady=3, ipadx=10)
    def _ok(self): self.selected = [(r["name"], r["type"]) for v, r in self._vars if v.get()]; self.destroy()

# ── 요구사항 3: UpdateDialog 클래스 추가 (업데이트 상세 확인) ──
class UpdateDialog(tk.Toplevel):
    def __init__(self, parent, tag, body):
        super().__init__(parent)
        self.title(f"업데이트 확인 - {tag}"); self.geometry("600x500"); self.configure(bg="#1e1e2e"); self.grab_set(); self.confirmed = False
        c = tk.Frame(self, padx=25, pady=20, bg="#1e1e2e"); c.pack(fill="both", expand=True)
        tk.Label(c, text=f"✨ 새 버전({tag})으로 업데이트하시겠습니까?", bg="#1e1e2e", fg="#cba6f7", font=("Segoe UI", 12, "bold")).pack(pady=(0, 15))
        txt = tk.Text(c, bg="#313244", fg="#cdd6f4", relief="flat", font=("Segoe UI", 10), height=15, padx=10, pady=10); txt.pack(fill="both", expand=True, pady=10); txt.insert("1.0", body); txt.config(state="disabled")
        btn_f = tk.Frame(c, bg="#1e1e2e"); btn_f.pack(fill="x", pady=(15, 0))
        tk.Button(btn_f, text="업데이트", bg="#a6e3a1", fg="#1e1e2e", font=("Segoe UI", 10, "bold"), relief="flat", command=self._ok, width=15).pack(side="right", padx=5, ipady=5)
        tk.Button(btn_f, text="취소", bg="#45475a", fg="#cdd6f4", font=("Segoe UI", 10, "bold"), relief="flat", command=self.destroy, width=15).pack(side="right", padx=5, ipady=5)
    def _ok(self): self.confirmed = True; self.destroy()

# ══════════════════════════════════════════════════════════════════════════════
#  마운트 다이얼로그 (1.0.8 레이아웃 1:1 유지)
# ══════════════════════════════════════════════════════════════════════════════
class MountDialog(tk.Toplevel):
    def __init__(self, parent, mount=None, app_cfg=None):
        super().__init__(parent)
        self.title("마운트 추가" if not mount or "id" not in mount else "마운트 설정")
        self.geometry("650x850"); self.configure(bg="#1e1e2e"); self.grab_set(); self.result = None; self._m = mount or {}; self._app_cfg = app_cfg; self._build()
    
    def _build(self):
        c = tk.Frame(self, padx=30, pady=25, bg="#1e1e2e"); c.pack(fill="both", expand=True)
        lbl_style = {"bg": "#1e1e2e", "fg": "#cba6f7", "font": ("Segoe UI", 10, "bold")}
        ent_style = {"bg": "#313244", "fg": "#cdd6f4", "insertbackground": "#cdd6f4", "relief": "flat", "font": ("Segoe UI", 10)}
        tk.Label(c, text="리모트 이름 (rclone.conf의 [이름])", **lbl_style).pack(anchor="w")
        self._rem = tk.Entry(c, **ent_style); self._rem.pack(fill="x", pady=(5, 15), ipady=3); self._rem.insert(0, self._m.get("remote", ""))
        tk.Label(c, text="서브 디렉토리 (예: sub/folder — 비워두면 루트 전체)", **lbl_style).pack(anchor="w")
        pth_f = tk.Frame(c, bg="#1e1e2e"); pth_f.pack(fill="x", pady=(5, 15))
        self._pth = tk.Entry(pth_f, **ent_style); self._pth.pack(side="left", fill="x", expand=True, ipady=3); self._pth.insert(0, self._m.get("remote_path", ""))
        tk.Button(pth_f, text="연결 테스트", bg="#89b4fa", fg="#1e1e2e", font=("Segoe UI", 9, "bold"), relief="flat", command=self._test).pack(side="left", padx=(10, 0), ipady=2)
        tk.Label(c, text="드라이브 문자", **lbl_style).pack(anchor="w")
        drive_values = [""] + [f"{chr(i)}:" for i in range(ord('D'), ord('Z')+1)]
        self._drv = ttk.Combobox(c, values=drive_values, font=("Segoe UI", 10), state="readonly"); self._drv.pack(fill="x", pady=(5, 15)); self._drv.set(self._m.get("drive", ""))
        tk.Label(c, text="캐시 디렉토리 (--cache-dir)", **lbl_style).pack(anchor="w")
        cdir_f = tk.Frame(c, bg="#1e1e2e"); cdir_f.pack(fill="x", pady=(5, 15))
        self._cdir = tk.Entry(cdir_f, **ent_style); self._cdir.pack(side="left", fill="x", expand=True, ipady=3); self._cdir.insert(0, self._m.get("cache_dir", ""))
        tk.Button(cdir_f, text="📂", bg="#45475a", fg="#cdd6f4", relief="flat", command=self._browse_cache).pack(side="left", padx=(5, 0))
        tk.Label(c, text="캐시 모드 (--vfs-cache-mode)", **lbl_style).pack(anchor="w")
        self._cmode = ttk.Combobox(c, values=["off", "minimal", "writes", "full"], font=("Segoe UI", 10), state="readonly"); self._cmode.pack(fill="x", pady=(5, 15)); self._cmode.set(self._m.get("cache_mode", "full"))
        tk.Label(c, text="추가 플래그 (; 또는 줄바꿈으로 구분)", **lbl_style).pack(anchor="w")
        self._ext = tk.Text(c, height=6, **ent_style); self._ext.pack(fill="x", pady=(5, 15)); self._ext.insert("1.0", self._m.get("extra_flags", ""))
        self._auto = tk.BooleanVar(value=self._m.get("auto_mount", False))
        tk.Checkbutton(c, text="시작 시 자동 마운트", variable=self._auto, bg="#1e1e2e", fg="#cdd6f4", selectcolor="#313244", font=("Segoe UI", 10), activebackground="#1e1e2e", activeforeground="#cdd6f4").pack(anchor="w", pady=5)
        btn_f = tk.Frame(c, bg="#1e1e2e"); btn_f.pack(fill="x", side="bottom", pady=(20, 0))
        tk.Button(btn_f, text="저장", bg="#cba6f7", fg="#1e1e2e", font=("Segoe UI", 11, "bold"), relief="flat", command=self._save, width=15).pack(side="right", padx=(10, 0), ipady=5)
        tk.Button(btn_f, text="취소", bg="#45475a", fg="#cdd6f4", font=("Segoe UI", 11, "bold"), relief="flat", command=self.destroy, width=15).pack(side="right", ipady=5)

    def _browse_cache(self):
        d = filedialog.askdirectory()
        if d: self._cdir.delete(0, tk.END); self._cdir.insert(0, d)

    def _test(self):
        target = f"{self._rem.get().strip()}:{self._pth.get().strip().strip('/')}"
        exe = get_rclone_exe(self._app_cfg)
        def r():
            try:
                p = subprocess.run([str(exe), "lsf", target, "--max-depth", "1"], capture_output=True, text=True, timeout=10, creationflags=0x08000000)
                if p.returncode == 0: messagebox.showinfo("성공", "연결 확인 완료!")
                else: messagebox.showerror("실패", f"연결 불가:\n{p.stderr.strip()}")
            except Exception as e: messagebox.showerror("오류", str(e))
        threading.Thread(target=r, daemon=True).start()

    def _save(self):
        rem, drv, pth = self._rem.get().strip(), self._drv.get(), self._pth.get().strip()
        if not rem: return messagebox.showwarning("오류", "리모트 이름 필수")
        for m in self._app_cfg.get("mounts", []):
            if m.get("id") == self._m.get("id"): continue
            if drv and m.get("drive") == drv: return messagebox.showerror("오류", "드라이브 문자 중복")
            if m.get("remote") == rem and m.get("remote_path", "") == pth: return messagebox.showerror("오류", "동일한 리모트/경로가 이미 등록되어 있습니다.")
        self.result = {"remote": rem, "drive": drv, "remote_path": pth, "cache_dir": self._cdir.get().strip(), "cache_mode": self._cmode.get(), "extra_flags": self._ext.get("1.0", tk.END).strip(), "auto_mount": self._auto.get()}
        self.destroy()

# ══════════════════════════════════════════════════════════════════════════════
#  메인 앱 클래스
# ══════════════════════════════════════════════════════════════════════════════
class App(tk.Tk):
    def __init__(self):
        self._tray = None 
        super().__init__()
        self.title("RcloneManager"); self.geometry("1200x800"); self.configure(bg="#1e1e2e"); self.protocol("WM_DELETE_WINDOW", self.hide_window)
        self._cfg = load_config(); self._status = {}; self._latest_rc = ""; self._latest_app_info = None
        self._build_ui(); self._refresh_list(); self._start_tray(); self._check_versions_async()
        if self._cfg.get("auto_mount"): self.after(1500, self._automount_all)

    def _build_ui(self):
        s = ttk.Style(self); s.theme_use("clam")
        s.configure("TFrame", background="#1e1e2e"); s.configure("TLabel", background="#1e1e2e", foreground="#cdd6f4", font=("Segoe UI", 10))
        s.configure("Header.TLabel", font=("Segoe UI", 16, "bold"), foreground="#cba6f7")
        s.configure("Treeview", background="#313244", foreground="#cdd6f4", fieldbackground="#313244", rowheight=30)
        s.configure("Treeview.Heading", background="#45475a", foreground="#cba6f7", font=("Segoe UI", 11, "bold"))
        hdr = ttk.Frame(self); hdr.pack(fill="x", padx=20, pady=15)
        ttl_f = ttk.Frame(hdr); ttl_f.pack(side="left")
        ttk.Label(ttl_f, text="🚀 RcloneManager", style="Header.TLabel").pack(side="left")
        ttk.Label(ttl_f, text=f"v{APP_VERSION}", foreground="#fab387", font=("Segoe UI", 10, "bold")).pack(side="left", padx=8, pady=(5,0))
        tk.Button(ttl_f, text="!", bg="#f38ba8", fg="#1e1e2e", font=("Segoe UI", 9, "bold"), relief="flat", width=2, command=self._open_issue).pack(side="left", padx=5, pady=(5,0))
        self._app_up_btn = tk.Button(hdr, text="✨ 새 버전 업데이트 가능", bg="#a6e3a1", fg="#1e1e2e", font=("Segoe UI", 9, "bold"), relief="flat", command=self._show_app_update_confirm)
        rcf = tk.Frame(self, bg="#1e1e2e"); rcf.pack(fill="x", padx=20, pady=5)
        tk.Label(rcf, text="rclone 경로:", bg="#1e1e2e", fg="#cba6f7", font=("Segoe UI", 10, "bold")).pack(side="left")
        self._rc_var = tk.StringVar(value=self._cfg.get("rclone_path", ""))
        tk.Entry(rcf, textvariable=self._rc_var, bg="#313244", fg="#cdd6f4", relief="flat", width=60).pack(side="left", padx=10, ipady=4)
        tk.Button(rcf, text="📂", bg="#45475a", fg="#cdd6f4", relief="flat", command=self._browse_rc).pack(side="left")
        self._rc_ver_label = tk.Label(rcf, text="v체크 중...", bg="#1e1e2e", fg="#94e2d5", font=("Segoe UI", 10), cursor="hand2"); self._rc_ver_label.pack(side="left", padx=15); self._rc_ver_label.bind("<Button-1>", self._handle_rc_click)
        opt = tk.Frame(self, bg="#1e1e2e"); opt.pack(fill="x", padx=20, pady=10)
        self._st_var = tk.BooleanVar(value=is_startup_enabled()); ttk.Checkbutton(opt, text="시작 시 자동 실행", variable=self._st_var, command=self._toggle_st).pack(side="left", padx=(0, 25))
        self._am_var = tk.BooleanVar(value=self._cfg.get("auto_mount", False)); ttk.Checkbutton(opt, text="시작 시 자동 마운트", variable=self._am_var, command=self._toggle_am).pack(side="left")
        cols = ("type", "auto", "drive", "remote", "status"); self._tree = ttk.Treeview(self, columns=cols, show="headings", height=14)
        for col, head in zip(cols, ("구분", "자동", "드라이브", "리모트 (서브경로)", "상태")): self._tree.heading(col, text=head)
        self._tree.pack(fill="both", expand=True, padx=20, pady=5); self._tree.tag_configure("remote_tag", foreground="#8fa0b5")
        btn_f = ttk.Frame(self); btn_f.pack(fill="x", padx=20, pady=15)
        ttk.Button(btn_f, text="➕ 추가", command=self._add).pack(side="left", padx=2)
        ttk.Button(btn_f, text="✏️ 편집", command=self._edit).pack(side="left", padx=2)
        ttk.Button(btn_f, text="🗑️ 삭제", command=self._del).pack(side="left", padx=2)
        ttk.Button(btn_f, text="🔼", width=4, command=self._move_up).pack(side="left", padx=2)
        ttk.Button(btn_f, text="🔽", width=4, command=self._move_down).pack(side="left", padx=2)
        ttk.Button(btn_f, text="📥 conf 가져오기", command=self._import_conf).pack(side="left", padx=2)
        ttk.Button(btn_f, text="▶ 마운트", command=self._mount_sel).pack(side="left", padx=15)
        ttk.Button(btn_f, text="■ 언마운트", command=self._unmount_sel).pack(side="left")

    def _open_issue(self):
        body = urllib.parse.quote(f"\n\n--- Debug Info ---\n- App Version: {APP_VERSION}\n- {get_sys_info()}"); webbrowser.open(f"https://github.com/{GITHUB_REPO}/issues/new?body={body}")

    def _check_versions_async(self):
        def _task():
            exe = Path(self._rc_var.get()); lat_rc = ""
            try:
                res = requests.get("https://api.github.com/repos/rclone/rclone/releases/latest", timeout=5); data = res.json(); lat_rc = data.get("tag_name", "").lstrip("v"); self._latest_rc = lat_rc
            except: pass
            if not exe.exists():
                msg = f"v없음 / 최신 v{lat_rc}" if lat_rc else "v없음"; self.after(0, lambda: self._rc_ver_label.config(text=msg, fg="#f38ba8"))
            else:
                try:
                    r = subprocess.run([str(exe), "version"], capture_output=True, text=True, timeout=5); loc_match = re.search(r"rclone v([\d.]+)", r.stdout); loc_rc = loc_match.group(1) if loc_match else "알 수 없음"
                    if lat_rc and loc_rc < lat_rc: self.after(0, lambda: self._rc_ver_label.config(text=f"v{loc_rc} / v{lat_rc} 업데이트", fg="#fab387"))
                    else: self.after(0, lambda: self._rc_ver_label.config(text=f"v{loc_rc} (최신)", fg="#94e2d5"))
                except: self.after(0, lambda: self._rc_ver_label.config(text="v알 수 없음", fg="#f38ba8"))
            try:
                res = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", timeout=5); data = res.json(); latest_app = data.get("tag_name", "").lstrip("v"); self._latest_app_info = data
                if latest_app > APP_VERSION: self.after(0, lambda: self._app_up_btn.pack(side="right"))
            except: pass
        threading.Thread(target=_task, daemon=True).start()

    def _show_app_update_confirm(self):
        if self._latest_app_info:
            tag = self._latest_app_info.get("tag_name", "New Version"); body = self._latest_app_info.get("body", "No release notes."); dlg = UpdateDialog(self, tag, body); self.wait_window(dlg)
            if dlg.confirmed: webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")

    def _handle_rc_click(self, event):
        text = self._rc_ver_label.cget("text")
        if "없음" in text or "업데이트" in text:
            if messagebox.askyesno("rclone", f"rclone v{self._latest_rc}를 설치/업데이트할까요?"): threading.Thread(target=self._do_rc_down, daemon=True).start()

    def _do_rc_down(self):
        self.after(0, lambda: self._rc_ver_label.config(text="다운로드 중... 0%")); res = download_rclone(APP_DIR, self._latest_rc, lambda p: self.after(0, lambda: self._rc_ver_label.config(text=f"다운로드 중... {p}%")))
        if res is True: messagebox.showinfo("완료", "rclone 설치 완료!"); self._check_versions_async()
        else: messagebox.showerror("오류", res)

    def _refresh_list(self):
        for i in self._tree.get_children(): self._tree.delete(i)
        for r in self._cfg.get("remotes", []): self._tree.insert("", "end", iid=f"remote_{r['name']}", values=("☁️ 원본", "—", "—", f"[{r['type']}] {r['name']}", "설정 대기"), tags=("remote_tag",))
        for m in self._cfg.get("mounts", []):
            st = self._status.get(m["id"], "stopped"); auto = "✅" if m.get("auto_mount") else "—"; lbl = "🟢 실행중" if st == "mounted" else "⚫ 중지됨"; rstr = f"{m['remote']}:{m.get('remote_path','')}".strip(":")
            self._tree.insert("", "end", iid=m["id"], values=("💾 마운트", auto, m.get("drive",""), rstr, lbl))
        if self._tray: self._tray.update_menu()

    def _toggle_st(self): set_startup(self._st_var.get())
    def _toggle_am(self): self._cfg["auto_mount"] = self._am_var.get(); save_config(self._cfg)
    def _browse_rc(self):
        p = filedialog.askopenfilename()
        if p: self._rc_var.set(p); self._cfg["rclone_path"] = p; save_config(self._cfg); self._check_versions_async()

    def _import_conf(self):
        p = find_default_rclone_conf(); path = filedialog.askopenfilename(initialdir=str(p.parent) if p else None)
        if not path: return
        remotes = parse_rclone_conf(Path(path)); dlg = ConfImportDialog(self, remotes); self.wait_window(dlg)
        if dlg.selected:
            exist = [r["name"] for r in self._cfg.get("remotes", [])]
            for r_name, r_type in dlg.selected:
                if r_name not in exist: self._cfg.setdefault("remotes", []).append({"name": r_name, "type": r_type})
            save_config(self._cfg); self._refresh_list()

    def _add(self):
        sel = self._tree.selection(); pre = sel[0].split("remote_", 1)[1] if sel and sel[0].startswith("remote_") else ""
        dlg = MountDialog(self, mount={"remote": pre}, app_cfg=self._cfg); self.wait_window(dlg)
        if dlg.result: dlg.result["id"] = str(uuid.uuid4()); self._cfg["mounts"].append(dlg.result); save_config(self._cfg); self._refresh_list()

    def _edit(self):
        sel = self._tree.selection()
        if not sel or sel[0].startswith("remote_"): return
        idx = next(i for i, m in enumerate(self._cfg["mounts"]) if m["id"] == sel[0]); dlg = MountDialog(self, mount=self._cfg["mounts"][idx], app_cfg=self._cfg); self.wait_window(dlg)
        if dlg.result: dlg.result["id"] = sel[0]; self._cfg["mounts"][idx] = dlg.result; save_config(self._cfg); self._refresh_list()

    def _del(self):
        sel = self._tree.selection()
        if not sel: return
        if sel[0].startswith("remote_"):
            r_name = sel[0].split("remote_", 1)[1]
            if messagebox.askyesno("삭제", f"원본 '{r_name}'을 삭제할까요?"): self._cfg["remotes"] = [r for r in self._cfg.get("remotes", []) if r["name"] != r_name]; save_config(self._cfg); self._refresh_list()
            return
        if messagebox.askyesno("삭제", "선택한 항목을 삭제할까요?"): self._cfg["mounts"] = [m for m in self._cfg["mounts"] if m["id"] != sel[0]]; save_config(self._cfg); self._refresh_list()

    def _move_up(self):
        sel = self._tree.selection()
        if not sel: return
        if sel[0].startswith("remote_"):
            idx = next((i for i, r in enumerate(self._cfg.get("remotes", [])) if f"remote_{r['name']}" == sel[0]), None)
            if idx is not None and idx > 0: self._cfg["remotes"][idx], self._cfg["remotes"][idx-1] = self._cfg["remotes"][idx-1], self._cfg["remotes"][idx]; save_config(self._cfg); self._refresh_list(); self._tree.selection_set(sel[0])
        else:
            idx = next(i for i, m in enumerate(self._cfg["mounts"]) if m["id"] == sel[0])
            if idx > 0: self._cfg["mounts"][idx], self._cfg["mounts"][idx-1] = self._cfg["mounts"][idx-1], self._cfg["mounts"][idx]; save_config(self._cfg); self._refresh_list(); self._tree.selection_set(sel[0])

    def _move_down(self):
        sel = self._tree.selection()
        if not sel: return
        if sel[0].startswith("remote_"):
            idx = next((i for i, r in enumerate(self._cfg.get("remotes", [])) if f"remote_{r['name']}" == sel[0]), None)
            if idx is not None and idx < len(self._cfg["remotes"])-1: self._cfg["remotes"][idx], self._cfg["remotes"][idx+1] = self._cfg["remotes"][idx+1], self._cfg["remotes"][idx]; save_config(self._cfg); self._refresh_list(); self._tree.selection_set(sel[0])
        else:
            idx = next(i for i, m in enumerate(self._cfg["mounts"]) if m["id"] == sel[0])
            if idx < len(self._cfg["mounts"]) - 1: self._cfg["mounts"][idx], self._cfg["mounts"][idx+1] = self._cfg["mounts"][idx+1], self._cfg["mounts"][idx]; save_config(self._cfg); self._refresh_list(); self._tree.selection_set(sel[0])

    def _mount_sel(self):
        sel = self._tree.selection()
        if not sel or sel[0].startswith("remote_") or sel[0] in active_mounts: return
        m = next(m for m in self._cfg["mounts"] if m["id"] == sel[0]); self._do_mount(sel[0], m)

    def _do_mount(self, mid, m):
        exe = get_rclone_exe(self._cfg)
        if exe.exists(): self._status[mid] = "mounted"; self._refresh_list(); threading.Thread(target=self._mount_task, args=(mid, exe, m), daemon=True).start()

    def _mount_task(self, mid, exe, m):
        try:
            p = subprocess.Popen(build_cmd(exe, m), creationflags=0x08000000); active_mounts[mid] = p; p.wait()
        finally: active_mounts.pop(mid, None); self._status[mid] = "stopped"; self.after(0, self._refresh_list)

    def _automount_all(self):
        for m in self._cfg.get("mounts", []):
            if m.get("auto_mount"): self._do_mount(m["id"], m)

    def _unmount_sel(self):
        sel = self._tree.selection()
        if sel and not sel[0].startswith("remote_"): unmount(sel[0])

    def _start_tray(self):
        if not pystray: return
        try:
            img = Image.new("RGBA", (64,64), (0,0,0,0)); d = ImageDraw.Draw(img); d.ellipse([2,2,62,62], fill="#cba6f7")
            self._tray = pystray.Icon("RcloneManager", img, "RcloneManager", 
                                       menu=pystray.Menu(pystray.MenuItem("열기", lambda: self.after(0, self.show_window), default=True), 
                                                         pystray.MenuItem("종료", lambda: self.after(0, self._quit_app))))
            threading.Thread(target=self._tray.run, daemon=True).start()
        except: pass

    def hide_window(self): self.withdraw()
    def show_window(self): self.deiconify(); self.lift(); self.focus_force()
    def _quit_app(self):
        for mid in list(active_mounts.keys()): unmount(mid)
        if self._tray: self._tray.stop()
        self.destroy()

if __name__ == "__main__":
    if activate_existing_window(): sys.exit(0)
    app = App(); app.mainloop()
