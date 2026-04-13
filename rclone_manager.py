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

def get_sys_info():
    """사용자의 해상도 및 배율 정보를 수집 (Scenario 20)"""
    try:
        user32 = ctypes.windll.user32
        w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        hdc = user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
        user32.ReleaseDC(0, hdc)
        return f"Resolution: {w}x{h}, Scaling: {int((dpi / 96) * 100)}%"
    except Exception: return "N/A"

# ── 2. 유틸리티 함수 ──
def is_startup_enabled():
    """시작 프로그램 등록 여부 확인 (Scenario 08)"""
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

def parse_rclone_conf(conf_path: Path):
    """rclone.conf 파일을 파싱하여 리모트 목록 반환 (Scenario 24)"""
    remotes = []
    try:
        cfg = configparser.ConfigParser()
        cfg.read(str(conf_path), encoding="utf-8")
        for section in cfg.sections():
            remotes.append({"name": section, "type": cfg.get(section, "type", fallback="")})
    except Exception: pass
    return remotes

def find_default_rclone_conf():
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
    """설정 파일 로드 (Scenario 05, 06)"""
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if "mounts" not in cfg: cfg["mounts"] = []
            return cfg
        except: pass
    return {"remotes": [], "mounts": [], "rclone_path": "", "auto_mount": False}

def save_config(cfg):
    """설정 파일 저장 (Scenario 07)"""
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def get_rclone_exe(cfg):
    """rclone 실행 파일 경로 반환 (Scenario 01)"""
    p = cfg.get("rclone_path", "").strip()
    if p and Path(p).exists(): return Path(p)
    return APP_DIR / "rclone.exe"

active_mounts = {}

def build_cmd(exe: Path, mount: dict):
    """rclone 명령어 빌드 (Scenario 02, 03, 04)"""
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
    """마운트 프로세스 중지 (Scenario 09)"""
    p = active_mounts.get(mid)
    if p:
        p.terminate()
        try: p.wait(timeout=3)
        except: p.kill()
        active_mounts.pop(mid, None)

def activate_existing_window():
    """기존 창 활성화 (Scenario 10)"""
    hwnd = ctypes.windll.user32.FindWindowW(None, "RcloneManager")
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        return True
    return False

# ── 4. 다이얼로그 클래스 ──
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

class MountDialog(tk.Toplevel):
    def __init__(self, parent, mount=None, app_cfg=None):
        super().__init__(parent)
        self.title("마운트 추가" if not mount or "id" not in mount else "마운트 설정")
        self.geometry("650x850"); self.configure(bg="#1e1e2e"); self.grab_set(); self.result = None; self._m = mount or {}; self._app_cfg = app_cfg; self._build()
    
    def _build(self):
        c = tk.Frame(self, padx=30, pady=25, bg="#1e1e2e"); c.pack(fill="both", expand=True)
        lbl_style = {"bg": "#1e1e2e", "fg": "#cba6f7", "font": ("Segoe UI", 10, "bold")}
        ent_style = {"bg": "#313244", "fg": "#cdd6f4", "insertbackground": "#cdd6f4", "relief": "flat", "font": ("Segoe UI", 10)}
        tk.Label(c, text="리모트 이름", **lbl_style).pack(anchor="w")
        self._rem = tk.Entry(c, **ent_style); self._rem.pack(fill="x", pady=(5, 15), ipady=3); self._rem.insert(0, self._m.get("remote", ""))
        tk.Label(c, text="서브 디렉토리", **lbl_style).pack(anchor="w")
        pth_f = tk.Frame(c, bg="#1e1e2e"); pth_f.pack(fill="x", pady=(5, 15))
        self._pth = tk.Entry(pth_f, **ent_style); self._pth.pack(side="left", fill="x", expand=True, ipady=3); self._pth.insert(0, self._m.get("remote_path", ""))
        tk.Button(pth_f, text="연결 테스트", bg="#89b4fa", fg="#1e1e2e", font=("Segoe UI", 9, "bold"), relief="flat", command=self._test).pack(side="left", padx=(10, 0), ipady=2)
        tk.Label(c, text="드라이브 문자", **lbl_style).pack(anchor="w")
        drive_values = [""] + [f"{chr(i)}:" for i in range(ord('D'), ord('Z')+1)]
        self._drv = ttk.Combobox(c, values=drive_values, font=("Segoe UI", 10), state="readonly"); self._drv.pack(fill="x", pady=(5, 15)); self._drv.set(self._m.get("drive", ""))
        tk.Label(c, text="캐시 디렉토리", **lbl_style).pack(anchor="w")
        cdir_f = tk.Frame(c, bg="#1e1e2e"); cdir_f.pack(fill="x", pady=(5, 15))
        self._cdir = tk.Entry(cdir_f, **ent_style); self._cdir.pack(side="left", fill="x", expand=True, ipady=3); self._cdir.insert(0, self._m.get("cache_dir", ""))
        tk.Button(cdir_f, text="📂", bg="#45475a", fg="#cdd6f4", relief="flat", command=self._browse_cache).pack(side="left", padx=(5, 0))
        tk.Label(c, text="캐시 모드", **lbl_style).pack(anchor="w")
        self._cmode = ttk.Combobox(c, values=["off", "minimal", "writes", "full"], font=("Segoe UI", 10), state="readonly"); self._cmode.pack(fill="x", pady=(5, 15)); self._cmode.set(self._m.get("cache_mode", "full"))
        tk.Label(c, text="추가 플래그", **lbl_style).pack(anchor="w")
        self._ext = tk.Text(c, height=6, **ent_style); self._ext.pack(fill="x", pady=(5, 15)); self._ext.insert("1.0", self._m.get("extra_flags", ""))
        self._auto = tk.BooleanVar(value=self._m.get("auto_mount", False))
        tk.Checkbutton(c, text="시작 시 자동 마운트", variable=self._auto, bg="#1e1e2e", fg="#cdd6f4", font=("Segoe UI", 10)).pack(anchor="w", pady=5)
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

# ── 5. 메인 앱 클래스 ──
class App(tk.Tk):
    def __init__(self):
        self._tray = None
        super().__init__()
        self.title("RcloneManager"); self.geometry("1200x800"); self.configure(bg="#1e1e2e")
        self.protocol("WM_DELETE_WINDOW", self.hide_window)
        self._cfg = load_config(); self._status = {}; self._latest_rc = ""; self._latest_app_info = None
        self._build_ui(); self._refresh_list(); self._start_tray(); self._check_versions_async()
        
        # [요구사항] 창이 활성화될 때마다 rclone 확인 (Scenario 29)
        self.bind("<FocusIn>", self._on_focus_in)
        
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
        
        # rclone 상태 레이블 (Scenario 28)
        self._rc_ver_label = tk.Label(rcf, text="v체크 중...", bg="#1e1e2e", fg="#94e2d5", font=("Segoe UI", 10), cursor="hand2")
        self._rc_ver_label.pack(side="left", padx=15)
        self._rc_ver_label.bind("<Button-1>", self._handle_rc_click)
        
        opt = tk.Frame(self, bg="#1e1e2e"); opt.pack(fill="x", padx=20, pady=10)
        self._st_var = tk.BooleanVar(value=is_startup_enabled())
        ttk.Checkbutton(opt, text="시작 시 자동 실행", variable=self._st_var, command=self._toggle_st).pack(side="left", padx=(0, 20))
        self._am_var = tk.BooleanVar(value=self._cfg.get("auto_mount", False))
        ttk.Checkbutton(opt, text="시작 시 자동 마운트", variable=self._am_var, command=self._save_settings).pack(side="left")
        btn_f = tk.Frame(self, bg="#1e1e2e"); btn_f.pack(fill="x", padx=20, pady=10)
        tk.Button(btn_f, text="+ 마운트 추가", bg="#cba6f7", fg="#1e1e2e", font=("Segoe UI", 10, "bold"), relief="flat", command=self._add).pack(side="left", padx=(0,10), ipady=5, ipadx=10)
        tk.Button(btn_f, text="📥 conf에서 리모트 가져오기", bg="#89b4fa", fg="#1e1e2e", font=("Segoe UI", 10, "bold"), relief="flat", command=self._import_conf).pack(side="left", ipady=5, ipadx=10)
        cols = ("id", "remote", "drive", "path", "status", "auto", "actions")
        self._tree = ttk.Treeview(self, columns=cols, show="headings", height=15); self._tree.pack(fill="both", expand=True, padx=20, pady=10)
        self._tree.heading("remote", text="리모트"); self._tree.heading("drive", text="드라이브"); self._tree.heading("path", text="경로"); self._tree.heading("status", text="상태"); self._tree.heading("auto", text="자동"); self._tree.heading("actions", text="작업")
        self._tree.column("id", width=0, stretch=False); self._tree.column("remote", width=150); self._tree.column("drive", width=80, anchor="center"); self._tree.column("path", width=250); self._tree.column("status", width=100, anchor="center"); self._tree.column("auto", width=80, anchor="center"); self._tree.column("actions", width=200, anchor="center")
        st_bar = tk.Frame(self, bg="#313244", height=30); st_bar.pack(fill="x", side="bottom")
        tk.Label(st_bar, text=f" System: {get_sys_info()}", bg="#313244", fg="#9399b2", font=("Segoe UI", 9)).pack(side="left", padx=10)

    def _open_issue(self):
        """이슈 리포트 페이지 열기 (Scenario 21)"""
        url = f"https://github.com/{GITHUB_REPO}/issues"
        webbrowser.open(url)

    def _check_rclone_presence(self):
        """rclone 경로 재확인 및 UI 업데이트 (Scenario 28)"""
        exe = get_rclone_exe(self._cfg)
        if not exe.exists():
            self._rc_ver_label.config(text="rclone 다운로드", fg="#f38ba8")
        else:
            self._check_versions_async()

    def _on_focus_in(self, event):
        """창 활성화 시 재확인 (Scenario 29)"""
        self._check_rclone_presence()

    def _check_versions_async(self, manual=False):
        def run():
            exe = get_rclone_exe(self._cfg); v_str = "알 수 없음"
            if exe.exists():
                try:
                    res = subprocess.run([str(exe), "version"], capture_output=True, text=True, creationflags=0x08000000)
                    m = re.search(r"rclone v([\d\.]+)", res.stdout); v_str = m.group(1) if m else "알 수 없음"
                except: pass
            try:
                r = requests.get("https://api.github.com/repos/rclone/rclone/releases/latest", timeout=5)
                if r.status_code == 200: self._latest_rc = r.json().get("tag_name", "").replace("v", "")
            except: pass
            self.after(0, lambda: self._update_ver_ui(v_str, manual))
        threading.Thread(target=run, daemon=True).start()

    def _update_ver_ui(self, current_v, manual):
        if current_v == "알 수 없음": self._rc_ver_label.config(text="rclone 다운로드", fg="#f38ba8")
        else:
            self._rc_ver_label.config(text=f"v{current_v}", fg="#94e2d5")
            if self._latest_rc and self._latest_rc > current_v:
                self._rc_ver_label.config(text=f"v{current_v} / v{self._latest_rc} 업데이트", fg="#fab387")
                if manual: messagebox.showinfo("업데이트", f"새 버전(v{self._latest_rc}) 가능!")
        try:
            r = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", timeout=5)
            if r.status_code == 200:
                data = r.json(); tag = data.get("tag_name", "").replace("v", "")
                if tag > APP_VERSION: self._latest_app_info = data; self._app_up_btn.pack(side="right", padx=10)
                else: self._app_up_btn.pack_forget()
        except: pass

    def _handle_rc_click(self, event):
        if "다운로드" in self._rc_ver_label.cget("text") or "업데이트" in self._rc_ver_label.cget("text"):
            if self._latest_rc and messagebox.askyesno("rclone", f"v{self._latest_rc} 설치할까요?"):
                self._do_rc_download(self._latest_rc)

    def _do_rc_download(self, ver):
        def run():
            res = download_rclone(APP_DIR, ver)
            if res is True: messagebox.showinfo("완료", "설치 성공!"); self._check_versions_async()
            else: messagebox.showerror("오류", res)
        threading.Thread(target=run, daemon=True).start()

    def _browse_rc(self):
        f = filedialog.askopenfilename()
        if f: self._rc_var.set(f); self._cfg["rclone_path"] = f; save_config(self._cfg); self._check_rclone_presence()

    def _save_settings(self):
        """설정 저장 (Scenario 19)"""
        self._cfg["auto_mount"] = self._am_var.get(); save_config(self._cfg)

    def _refresh_list(self):
        for item in self._tree.get_children(): self._tree.delete(item)
        for m in self._cfg.get("mounts", []):
            st = "● 마운트됨" if m["id"] in active_mounts else "○ 중단됨"
            self._tree.insert("", "end", iid=m["id"], values=(m["id"], m["remote"], m.get("drive") or "N/A", m.get("remote_path") or "/", st, "Y" if m.get("auto_mount") else "N", ""))

    def _add(self):
        d = MountDialog(self, app_cfg=self._cfg); self.wait_window(d)
        if d.result: d.result["id"] = str(uuid.uuid4()); self._cfg["mounts"].append(d.result); save_config(self._cfg); self._refresh_list()

    def _delete_mount(self, mid):
        """마운트 삭제 (Scenario 17)"""
        if messagebox.askyesno("삭제", "삭제할까요?"):
            unmount(mid); self._cfg["mounts"] = [m for m in self._cfg["mounts"] if m["id"] != mid]
            save_config(self._cfg); self._refresh_list()

    def _mount_single(self, mid):
        """단일 마운트 실행 (Scenario 18)"""
        m = next((x for x in self._cfg["mounts"] if x["id"] == mid), None)
        if m:
            exe = get_rclone_exe(self._cfg)
            p = subprocess.Popen(build_cmd(exe, m), creationflags=0x08000000); active_mounts[mid] = p; self._refresh_list()

    def _toggle_st(self): set_startup(self._st_var.get())
    def _toggle_am(self): self._save_settings()
    def _import_conf(self):
        p = find_default_rclone_conf(); remotes = parse_rclone_conf(p) if p else []
        d = ConfImportDialog(self, remotes); self.wait_window(d)
        if d.selected:
            for n, t in d.selected: self._cfg["mounts"].append({"id": str(uuid.uuid4()), "remote": n, "auto_mount": False})
            save_config(self._cfg); self._refresh_list()

    def _automount_all(self):
        for m in self._cfg.get("mounts", []):
            if m.get("auto_mount"): self._mount_single(m["id"])

    def _show_app_update_confirm(self):
        if self._latest_app_info:
            d = UpdateDialog(self, self._latest_app_info["tag_name"], self._latest_app_info["body"]); self.wait_window(d)
            if d.confirmed: webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")

    def _start_tray(self):
        if pystray:
            img = Image.new('RGB', (64, 64), color="#cba6f7")
            menu = pystray.Menu(pystray.MenuItem("열기", self.show_window, default=True), pystray.MenuItem("종료", self._quit_app))
            self._tray = pystray.Icon("RcloneManager", img, "RcloneManager", menu)
            threading.Thread(target=self._tray.run, daemon=True).start()

    def hide_window(self): self.withdraw()
    def show_window(self): self.deiconify(); self.lift(); self.focus_force()
    def _quit_app(self):
        for mid in list(active_mounts.keys()): unmount(mid)
        if self._tray: self._tray.stop()
        self.quit()

if __name__ == "__main__":
    if not activate_existing_window(): app = App(); app.mainloop()
