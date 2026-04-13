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
APP_VERSION = "1.1.1"
GITHUB_REPO = "Murianwind/rclone_mount_manager"

# ── 1. 시스템 환경 및 해상도 대응 ──
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

def get_dpi_scale():
    """현재 시스템의 DPI 배율을 반환"""
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return dpi / 96
    except:
        return 1.0

def scale(pixel):
    """해상도 배율에 따른 픽셀 값 계산"""
    return int(pixel * get_dpi_scale())

def get_sys_info():
    try:
        user32 = ctypes.windll.user32
        w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        return f"Resolution: {w}x{h}, Scaling: {int(get_dpi_scale() * 100)}%"
    except Exception: return "N/A"

# ── 2. 유틸리티 및 rclone 관리 ──
def is_startup_enabled():
    if not winreg: return False
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, "RcloneManager")
        winreg.CloseKey(key)
        return True
    except: return False

def set_startup(enable: bool):
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
    remotes = []
    try:
        cfg = configparser.ConfigParser()
        cfg.read(str(conf_path), encoding="utf-8")
        for section in cfg.sections():
            remotes.append({"name": section, "type": cfg.get(section, "type", fallback="")})
    except Exception: pass
    return remotes

def find_default_rclone_conf():
    if getattr(sys, 'frozen', False): app_dir = Path(sys.executable).parent
    else: app_dir = Path(__file__).parent
    for p in [Path(os.environ.get("APPDATA", "")) / "rclone" / "rclone.conf", Path.home() / ".config" / "rclone" / "rclone.conf", app_dir / "rclone.conf"]:
        if p.exists(): return p
    return None

def download_rclone(dest_dir: Path, version: str, progress_cb=None):
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
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if "mounts" not in cfg: cfg["mounts"] = []
            return cfg
        except: pass
    return {"remotes": [], "mounts": [], "rclone_path": "", "auto_mount": False}

def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def get_rclone_exe(cfg):
    p = cfg.get("rclone_path", "").strip()
    if p and Path(p).exists(): return Path(p)
    return None

active_mounts = {}

def build_cmd(exe: Path, mount: dict):
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
    p = active_mounts.get(mid)
    if p:
        p.terminate()
        try: p.wait(timeout=3)
        except: p.kill()
        active_mounts.pop(mid, None)

def activate_existing_window():
    hwnd = ctypes.windll.user32.FindWindowW(None, "RcloneManager")
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        return True
    return False

# ── 4. 다이얼로그 ──
class ConfImportDialog(tk.Toplevel):
    def __init__(self, parent, remotes):
        super().__init__(parent)
        self.title("리모트 선택")
        self.geometry(f"{scale(350)}x{scale(450)}")
        self.configure(bg="#1e1e2e"); self.grab_set(); self.selected = []; self._remotes = remotes; self._vars = []
        
        main_f = tk.Frame(self, bg="#1e1e2e")
        main_f.pack(fill="both", expand=True, padx=16, pady=10)
        tk.Label(main_f, text="가져올 리모트 선택:", bg="#1e1e2e", fg="#cba6f7", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 10))
        
        canvas = tk.Canvas(main_f, bg="#1e1e2e", highlightthickness=0)
        scrollbar = ttk.Scrollbar(main_f, orient="vertical", command=canvas.yview)
        scroll_f = tk.Frame(canvas, bg="#1e1e2e")
        
        canvas.create_window((0, 0), window=scroll_f, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        for r in self._remotes:
            v = tk.BooleanVar(value=True); self._vars.append((v, r))
            row = tk.Frame(scroll_f, bg="#1e1e2e"); row.pack(fill="x", pady=2)
            tk.Checkbutton(row, variable=v, bg="#1e1e2e", fg="#cdd6f4", selectcolor="#313244").pack(side="left")
            tk.Label(row, text=f"{r['name']} [{r['type']}]", bg="#1e1e2e", fg="#cdd6f4").pack(side="left")
            
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        scroll_f.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        
        tk.Button(self, text="가져오기", bg="#cba6f7", fg="#1e1e2e", font=("Segoe UI", 10, "bold"), relief="flat", command=self._ok).pack(pady=10, ipady=3, ipadx=10)

    def _ok(self): self.selected = [(r["name"], r["type"]) for v, r in self._vars if v.get()]; self.destroy()

class UpdateDialog(tk.Toplevel):
    def __init__(self, parent, tag, body, download_url=None):
        super().__init__(parent)
        self.title(f"업데이트 확인 - {tag}")
        self.geometry(f"{scale(550)}x{scale(450)}")
        self.configure(bg="#1e1e2e"); self.grab_set(); self.confirmed = False; self.download_url = download_url
        
        c = tk.Frame(self, padx=25, pady=20, bg="#1e1e2e")
        c.pack(fill="both", expand=True)
        
        tk.Label(c, text=f"✨ 새 버전({tag}) 업데이트", bg="#1e1e2e", fg="#cba6f7", font=("Segoe UI", 12, "bold")).pack(pady=(0, 10))
        
        txt_f = tk.Frame(c, bg="#1e1e2e")
        txt_f.pack(fill="both", expand=True, pady=5)
        self.txt = tk.Text(txt_f, bg="#313244", fg="#cdd6f4", relief="flat", font=("Segoe UI", 10), padx=10, pady=10)
        sc = ttk.Scrollbar(txt_f, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sc.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sc.pack(side="right", fill="y")
        self.txt.insert("1.0", body); self.txt.config(state="disabled")
        
        btn_f = tk.Frame(c, bg="#1e1e2e")
        btn_f.pack(fill="x", side="bottom", pady=(10, 0))
        tk.Button(btn_f, text="업데이트 실행", bg="#a6e3a1", fg="#1e1e2e", font=("Segoe UI", 10, "bold"), relief="flat", command=self._ok, width=15).pack(side="right", padx=5, ipady=5)
        tk.Button(btn_f, text="취소", bg="#45475a", fg="#cdd6f4", font=("Segoe UI", 10, "bold"), relief="flat", command=self.destroy, width=12).pack(side="right", padx=5, ipady=5)

    def _ok(self): self.confirmed = True; self.destroy()

class MountDialog(tk.Toplevel):
    def __init__(self, parent, mount=None, app_cfg=None):
        super().__init__(parent)
        self.title("마운트 설정")
        self.geometry(f"{scale(600)}x{scale(750)}")
        self.configure(bg="#1e1e2e"); self.grab_set(); self.result = None; self._m = mount or {}; self._app_cfg = app_cfg; self._build()

    def _build(self):
        c = tk.Frame(self, padx=30, pady=20, bg="#1e1e2e")
        c.pack(fill="both", expand=True)
        lbl_s = {"bg": "#1e1e2e", "fg": "#cba6f7", "font": ("Segoe UI", 10, "bold")}
        ent_s = {"bg": "#313244", "fg": "#cdd6f4", "insertbackground": "#cdd6f4", "relief": "flat", "font": ("Segoe UI", 10)}
        
        def add_field(label, var_name, is_text=False, combo_vals=None):
            tk.Label(c, text=label, **lbl_s).pack(anchor="w", pady=(5, 0))
            if combo_vals:
                cb = ttk.Combobox(c, values=combo_vals, font=("Segoe UI", 10), state="readonly")
                cb.pack(fill="x", pady=(2, 10)); cb.set(self._m.get(var_name, combo_vals[0] if var_name=="cache_mode" else ""))
                return cb
            elif is_text:
                t = tk.Text(c, height=4, **ent_s); t.pack(fill="x", pady=(2, 10)); t.insert("1.0", self._m.get(var_name, "")); return t
            else:
                f = tk.Frame(c, bg="#1e1e2e"); f.pack(fill="x", pady=(2, 10))
                e = tk.Entry(f, **ent_s); e.pack(side="left", fill="x", expand=True, ipady=3); e.insert(0, self._m.get(var_name, ""))
                return e

        self._rem = add_field("리모트 이름", "remote")
        pth_f = tk.Frame(c, bg="#1e1e2e"); pth_f.pack(fill="x")
        tk.Label(pth_f, text="서브 디렉토리", **lbl_s).pack(side="left")
        tk.Button(pth_f, text="연결 테스트", bg="#89b4fa", fg="#1e1e2e", font=("Segoe UI", 8, "bold"), command=self._test).pack(side="right")
        self._pth = tk.Entry(c, **ent_s); self._pth.pack(fill="x", pady=(2, 10), ipady=3); self._pth.insert(0, self._m.get("remote_path", ""))
        
        self._drv = add_field("드라이브 문자", "drive", combo_vals=[""] + [f"{chr(i)}:" for i in range(ord('D'), ord('Z')+1)])
        self._cdir = add_field("캐시 디렉토리", "cache_dir")
        self._cmode = add_field("캐시 모드", "cache_mode", combo_vals=["off", "minimal", "writes", "full"])
        self._ext = add_field("추가 플래그", "extra_flags", is_text=True)
        
        self._auto = tk.BooleanVar(value=self._m.get("auto_mount", False))
        tk.Checkbutton(c, text="시작 시 자동 마운트", variable=self._auto, bg="#1e1e2e", fg="#cdd6f4", selectcolor="#313244").pack(anchor="w")
        
        btn_f = tk.Frame(c, bg="#1e1e2e")
        btn_f.pack(fill="x", side="bottom", pady=(10, 0))
        tk.Button(btn_f, text="저장", bg="#cba6f7", fg="#1e1e2e", font=("Segoe UI", 10, "bold"), relief="flat", command=self._save, width=15).pack(side="right", padx=5, ipady=5)
        tk.Button(btn_f, text="취소", bg="#45475a", fg="#cdd6f4", font=("Segoe UI", 10, "bold"), relief="flat", command=self.destroy, width=12).pack(side="right", padx=5, ipady=5)

    def _test(self):
        target = f"{self._rem.get().strip()}:{self._pth.get().strip().strip('/')}"
        exe = get_rclone_exe(self._app_cfg)
        if not exe: return messagebox.showerror("오류", "rclone이 등록되지 않았습니다.")
        def r():
            try:
                p = subprocess.run([str(exe), "lsf", target, "--max-depth", "1"], capture_output=True, text=True, timeout=10, creationflags=0x08000000)
                if p.returncode == 0: messagebox.showinfo("성공", "연결 확인 완료!")
                else: messagebox.showerror("실패", f"연결 불가:\n{p.stderr.strip()}")
            except Exception as e: messagebox.showerror("오류", str(e))
        threading.Thread(target=r, daemon=True).start()

    def _save(self):
        self.result = {"remote": self._rem.get().strip(), "drive": self._drv.get(), "remote_path": self._pth.get().strip(), "cache_dir": self._cdir.get().strip(), "cache_mode": self._cmode.get(), "extra_flags": self._ext.get("1.0", tk.END).strip(), "auto_mount": self._auto.get()}
        self.destroy()

# ── 5. 메인 앱 ──
class App(tk.Tk):
    def __init__(self):
        self._tray = None
        super().__init__()
        self.title("RcloneManager")
        self.geometry(f"{scale(1100)}x{scale(700)}")
        self.configure(bg="#1e1e2e")
        self.protocol("WM_DELETE_WINDOW", self.hide_window)
        self._cfg = load_config(); self._status = {}; self._latest_rc = ""; self._latest_app_info = None
        self._version_check_running = False
        self._build_ui(); self._refresh_list(); self._start_tray()
        self._init_rc_label()
        self._check_versions_async()
        self.bind("<FocusIn>", self._on_focus_in)
        if self._cfg.get("auto_mount"): self.after(1500, self._automount_all)

    def _init_rc_label(self):
        exe = get_rclone_exe(self._cfg)
        if not exe: self._rc_ver_label.config(text="rclone 다운로드", fg="#f38ba8")
        else: self._rc_ver_label.config(text="v체크 중...", fg="#94e2d5")

    def _build_ui(self):
        s = ttk.Style(self); s.theme_use("clam")
        s.configure("TFrame", background="#1e1e2e")
        s.configure("TLabel", background="#1e1e2e", foreground="#cdd6f4", font=("Segoe UI", 10))
        s.configure("Header.TLabel", font=("Segoe UI", 16, "bold"), foreground="#cba6f7")
        s.configure("Treeview", background="#313244", foreground="#cdd6f4", fieldbackground="#313244", rowheight=30)
        s.configure("Treeview.Heading", background="#45475a", foreground="#cba6f7", font=("Segoe UI", 10, "bold"))
        
        hdr = ttk.Frame(self); hdr.pack(fill="x", padx=20, pady=10)
        ttl_f = ttk.Frame(hdr); ttl_f.pack(side="left")
        ttk.Label(ttl_f, text="🚀 RcloneManager", style="Header.TLabel").pack(side="left")
        ttk.Label(ttl_f, text=f"v{APP_VERSION}", foreground="#fab387", font=("Segoe UI", 10, "bold")).pack(side="left", padx=8)
        self._app_up_btn = tk.Button(hdr, text="✨ 새 버전 업데이트", bg="#a6e3a1", fg="#1e1e2e", font=("Segoe UI", 9, "bold"), relief="flat", command=self._show_app_update_confirm)
        
        rcf = tk.Frame(self, bg="#1e1e2e"); rcf.pack(fill="x", padx=20, pady=5)
        tk.Label(rcf, text="rclone 경로:", bg="#1e1e2e", fg="#cba6f7", font=("Segoe UI", 10, "bold")).pack(side="left")
        self._rc_var = tk.StringVar(value=self._cfg.get("rclone_path", ""))
        tk.Entry(rcf, textvariable=self._rc_var, bg="#313244", fg="#cdd6f4", relief="flat", width=50).pack(side="left", padx=10, ipady=3)
        tk.Button(rcf, text="📂", bg="#45475a", fg="#cdd6f4", relief="flat", command=self._browse_rc).pack(side="left")
        self._rc_ver_label = tk.Label(rcf, text="", bg="#1e1e2e", fg="#94e2d5", font=("Segoe UI", 10), cursor="hand2")
        self._rc_ver_label.pack(side="left", padx=15); self._rc_ver_label.bind("<Button-1>", self._handle_rc_click)
        
        opt = tk.Frame(self, bg="#1e1e2e"); opt.pack(fill="x", padx=20, pady=5)
        self._st_var = tk.BooleanVar(value=is_startup_enabled()); ttk.Checkbutton(opt, text="시작 시 자동 실행", variable=self._st_var, command=self._toggle_st).pack(side="left", padx=(0, 20))
        self._am_var = tk.BooleanVar(value=self._cfg.get("auto_mount", False)); ttk.Checkbutton(opt, text="시작 시 자동 마운트", variable=self._am_var, command=self._toggle_am).pack(side="left")
        
        cols = ("type", "auto", "drive", "remote", "status")
        self._tree = ttk.Treeview(self, columns=cols, show="headings", height=12)
        for col, head in zip(cols, ("구분", "자동", "드라이브", "리모트 정보", "상태")): 
            self._tree.heading(col, text=head); self._tree.column(col, width=100, anchor="center")
        self._tree.column("remote", width=400, anchor="w")
        self._tree.pack(fill="both", expand=True, padx=20, pady=5)
        
        btn_f = ttk.Frame(self); btn_f.pack(fill="x", padx=20, pady=10)
        ttk.Button(btn_f, text="➕ 추가", command=self._add).pack(side="left", padx=2)
        ttk.Button(btn_f, text="✏️ 편집", command=self._edit).pack(side="left", padx=2)
        ttk.Button(btn_f, text="🗑️ 삭제", command=self._del).pack(side="left", padx=2)
        ttk.Button(btn_f, text="📥 conf 가져오기", command=self._import_conf).pack(side="left", padx=10)
        ttk.Button(btn_f, text="▶ 마운트", command=self._mount_sel).pack(side="left", padx=2)
        ttk.Button(btn_f, text="■ 언마운트", command=self._unmount_sel).pack(side="left", padx=2)
        
        st_bar = tk.Frame(self, bg="#313244", height=25); st_bar.pack(fill="x", side="bottom")
        tk.Label(st_bar, text=f" System: {get_sys_info()}", bg="#313244", fg="#9399b2", font=("Segoe UI", 9)).pack(side="left", padx=10)

    def _on_focus_in(self, event):
        if event.widget is self:
            self._check_rclone_presence()
            self._check_versions_async()

    def _check_rclone_presence(self):
        exe = get_rclone_exe(self._cfg)
        if not exe: self._rc_ver_label.config(text="rclone 다운로드", fg="#f38ba8")
        else: self._check_versions_async()

    def _check_versions_async(self):
        if self._version_check_running: return
        self._version_check_running = True
        def _task():
            try:
                exe = get_rclone_exe(self._cfg)
                # rclone 버전 체크
                try:
                    res = requests.get("https://api.github.com/repos/rclone/rclone/releases/latest", timeout=5)
                    self._latest_rc = res.json().get("tag_name", "").lstrip("v")
                except: pass
                
                if exe:
                    try:
                        r = subprocess.run([str(exe), "version"], capture_output=True, text=True, timeout=5, creationflags=0x08000000)
                        loc_match = re.search(r"rclone v([\d.]+)", r.stdout)
                        loc_rc = loc_match.group(1) if loc_match else ""
                        if loc_rc:
                            if self._latest_rc and loc_rc < self._latest_rc:
                                self.after(0, lambda: self._rc_ver_label.config(text=f"v{loc_rc} / v{self._latest_rc} 업데이트", fg="#fab387"))
                            else:
                                self.after(0, lambda: self._rc_ver_label.config(text=f"v{loc_rc} (최신)", fg="#94e2d5"))
                    except: pass
                
                # 앱 버전 체크
                try:
                    res = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", timeout=5)
                    data = res.json()
                    self._latest_app_info = data
                    if data.get("tag_name", "").lstrip("v") > APP_VERSION:
                        self.after(0, lambda: self._app_up_btn.pack(side="right"))
                    else:
                        self.after(0, lambda: self._app_up_btn.pack_forget())
                except: pass
            finally: self._version_check_running = False
        threading.Thread(target=_task, daemon=True).start()

    def _show_app_update_confirm(self):
        if not self._latest_app_info: return
        tag = self._latest_app_info.get("tag_name", "New Version")
        body = self._latest_app_info.get("body", "업데이트 내역이 없습니다.")
        asset_url = next((a["browser_download_url"] for a in self._latest_app_info.get("assets", []) if ".zip" in a["name"]), None)
        dlg = UpdateDialog(self, tag, body, asset_url)
        self.wait_window(dlg)
        if dlg.confirmed:
            threading.Thread(target=self._do_app_update, args=(asset_url,), daemon=True).start()

    def _do_app_update(self, url):
        if not url: return messagebox.showerror("오류", "다운로드 링크가 없습니다.")
        try:
            self.after(0, lambda: messagebox.showinfo("업데이트", "파일을 다운로드하고 프로그램을 교체합니다. 완료 후 자동 종료됩니다."))
            r = requests.get(url, stream=True)
            tmp_zip = Path(tempfile.gettempdir()) / "RcloneManager_Update.zip"
            with open(tmp_zip, "wb") as f:
                for chunk in r.iter_content(65536): f.write(chunk)
            
            # 업데이트 배치 스크립트 작성 (실행 파일 교체용)
            cur_exe = sys.executable
            batch_path = Path(tempfile.gettempdir()) / "update_rcm.bat"
            with open(batch_path, "w") as f:
                f.write(f'@echo off\ntimeout /t 2 /nobreak > nul\npowershell -Command "Expand-Archive -Path \'{tmp_zip}\' -DestinationPath \'{APP_DIR}\' -Force"\ndel "{tmp_zip}"\nstart "" "{cur_exe}"\ndel "%~f0"')
            
            os.startfile(batch_path)
            self.after(0, self._quit_app)
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("오류", f"업데이트 실패: {e}"))

    def _handle_rc_click(self, event):
        txt = self._rc_ver_label.cget("text")
        if "다운로드" in txt or "업데이트" in txt:
            if self._latest_rc and messagebox.askyesno("rclone", f"rclone v{self._latest_rc}를 설치/업데이트할까요?"):
                threading.Thread(target=self._do_rc_down, daemon=True).start()

    def _do_rc_down(self):
        self.after(0, lambda: self._rc_ver_label.config(text="다운로드 중... 0%", fg="#89b4fa"))
        res = download_rclone(APP_DIR, self._latest_rc, lambda p: self.after(0, lambda: self._rc_ver_label.config(text=f"다운로드 중... {p}%")))
        if res is True:
            new_path = str(APP_DIR / "rclone.exe")
            self._rc_var.set(new_path); self._cfg["rclone_path"] = new_path; save_config(self._cfg)
            messagebox.showinfo("완료", "rclone 설치 완료!")
            self._version_check_running = False; self._check_versions_async()
        else:
            messagebox.showerror("오류", res)
            self._init_rc_label()

    def _refresh_list(self):
        for i in self._tree.get_children(): self._tree.delete(i)
        for r in self._cfg.get("remotes", []):
            self._tree.insert("", "end", iid=f"remote_{r['name']}", values=("☁️ 원본", "—", "—", f"[{r['type']}] {r['name']}", "설정 대기"))
        for m in self._cfg.get("mounts", []):
            st = self._status.get(m["id"], "stopped")
            lbl = "🟢 실행중" if st == "mounted" else "⚫ 중지됨"
            self._tree.insert("", "end", iid=m["id"], values=("💾 마운트", "✅" if m.get("auto_mount") else "—", m.get("drive",""), f"{m['remote']}:{m.get('remote_path','')}", lbl))
        if self._tray: self._update_tray_menu()

    def _start_tray(self):
        if not pystray: return
        try:
            img = Image.new("RGBA", (64,64), (0,0,0,0)); d = ImageDraw.Draw(img); d.ellipse([2,2,62,62], fill="#cba6f7")
            self._tray = pystray.Icon("RcloneManager", img, "RcloneManager")
            self._update_tray_menu()
            threading.Thread(target=self._tray.run, daemon=True).start()
        except: pass

    def _update_tray_menu(self):
        if not self._tray: return
        menu_items = [pystray.MenuItem("📂 열기", lambda: self.after(0, self.show_window), default=True)]
        for m in self._cfg.get("mounts", []):
            mid = m["id"]; st = self._status.get(mid, "stopped")
            label = f"{'🟢' if st == 'mounted' else '⚫'} {m['remote']} ({m.get('drive','')})"
            menu_items.append(pystray.MenuItem(label, lambda item, mid=mid: self.after(0, lambda: self._handle_tray_mount(mid))))
        menu_items.append(pystray.MenuItem("❌ 종료", lambda: self.after(0, self._quit_app)))
        self._tray.menu = pystray.Menu(*menu_items)

    def _handle_tray_mount(self, mid):
        if mid in active_mounts: unmount(mid)
        else: self._mount_single(mid)
        self._refresh_list()

    def _toggle_st(self): set_startup(self._st_var.get())
    def _toggle_am(self): self._cfg["auto_mount"] = self._am_var.get(); save_config(self._cfg)
    def _browse_rc(self):
        p = filedialog.askopenfilename()
        if p: self._rc_var.set(p); self._cfg["rclone_path"] = p; save_config(self._cfg); self._check_rclone_presence()

    def _import_conf(self):
        p = find_default_rclone_conf(); path = filedialog.askopenfilename(initialdir=str(p.parent) if p else None)
        if not path: return
        remotes = parse_rclone_conf(Path(path)); dlg = ConfImportDialog(self, remotes); self.wait_window(dlg)
        if dlg.selected:
            exist = [r["name"] for r in self._cfg.get("remotes", [])]
            for n, t in dlg.selected:
                if n not in exist: self._cfg.setdefault("remotes", []).append({"name": n, "type": t})
            save_config(self._cfg); self._refresh_list()

    def _add(self):
        sel = self._tree.selection(); pre = sel[0].split("remote_", 1)[1] if sel and sel[0].startswith("remote_") else ""
        dlg = MountDialog(self, mount={"remote": pre}, app_cfg=self._cfg); self.wait_window(dlg)
        if dlg.result: dlg.result["id"] = str(uuid.uuid4()); self._cfg["mounts"].append(dlg.result); save_config(self._cfg); self._refresh_list()

    def _edit(self):
        sel = self._tree.selection()
        if not sel or sel[0].startswith("remote_"): return
        idx = next(i for i, m in enumerate(self._cfg["mounts"]) if m["id"] == sel[0])
        dlg = MountDialog(self, mount=self._cfg["mounts"][idx], app_cfg=self._cfg); self.wait_window(dlg)
        if dlg.result: dlg.result["id"] = sel[0]; self._cfg["mounts"][idx] = dlg.result; save_config(self._cfg); self._refresh_list()

    def _del(self):
        sel = self._tree.selection()
        if not sel: return
        if sel[0].startswith("remote_"):
            n = sel[0].split("remote_", 1)[1]
            if messagebox.askyesno("삭제", f"원본 '{n}' 삭제?"): 
                self._cfg["remotes"] = [r for r in self._cfg["remotes"] if r["name"] != n]; save_config(self._cfg); self._refresh_list()
        elif messagebox.askyesno("삭제", "마운트 항목 삭제?"):
            unmount(sel[0]); self._cfg["mounts"] = [m for m in self._cfg["mounts"] if m["id"] != sel[0]]; save_config(self._cfg); self._refresh_list()

    def _mount_sel(self):
        sel = self._tree.selection()
        if sel and not sel[0].startswith("remote_"): self._mount_single(sel[0])

    def _unmount_sel(self):
        sel = self._tree.selection()
        if sel and not sel[0].startswith("remote_"): unmount(sel[0])

    def _do_mount(self, mid, m):
        exe = get_rclone_exe(self._cfg)
        if exe and exe.exists():
            self._status[mid] = "mounted"; self._refresh_list()
            threading.Thread(target=self._mount_task, args=(mid, exe, m), daemon=True).start()
        else: messagebox.showerror("오류", "rclone 실행 파일을 찾을 수 없습니다.")

    def _mount_task(self, mid, exe, m):
        try:
            p = subprocess.Popen(build_cmd(exe, m), creationflags=0x08000000); active_mounts[mid] = p; p.wait()
        finally: active_mounts.pop(mid, None); self._status[mid] = "stopped"; self.after(0, self._refresh_list)

    def _automount_all(self):
        for m in self._cfg.get("mounts", []):
            if m.get("auto_mount"): self._do_mount(m["id"], m)

    def hide_window(self): self.withdraw()
    def show_window(self): self.deiconify(); self.lift(); self.focus_force()
    def _quit_app(self):
        for mid in list(active_mounts.keys()): unmount(mid)
        if self._tray: self._tray.stop()
        self.destroy()

if __name__ == "__main__":
    if activate_existing_window(): sys.exit(0)
    app = App(); app.mainloop()
