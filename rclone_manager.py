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

# ── 프로그램 설정 ──
APP_VERSION = "1.0.7"
GITHUB_REPO = "Murianwind/rclone_mount_manager"

# ── 1. 고해상도(DPI) 대응 및 시스템 정보 수집 ──
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

def get_sys_info():
    """사용자의 해상도 및 배율 정보를 문자열로 반환합니다. (Scenario 20)"""
    try:
        user32 = ctypes.windll.user32
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        hdc = user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        user32.ReleaseDC(0, hdc)
        scale = int((dpi / 96) * 100)
        return f"Resolution: {w}x{h}, Scaling: {scale}%"
    except Exception:
        return "Resolution/Scaling info unavailable"

# ── 2. 시작 프로그램 및 유틸리티 함수 (클래스 정의 전 선언) ──
def is_startup_enabled():
    """윈도우 시작 프로그램 등록 여부 확인 (Scenario 16)"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, "RcloneManager")
        winreg.CloseKey(key)
        return True
    except Exception:
        return False

def set_startup(enable: bool):
    """윈도우 시작 프로그램 등록/해제 (Scenario 16)"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        if enable:
            if getattr(sys, 'frozen', False):
                exe_path = f'"{sys.executable}"'
            else:
                exe_path = f'pythonw "{Path(__file__).resolve()}"'
            winreg.SetValueEx(key, "RcloneManager", 0, winreg.REG_SZ, exe_path)
        else:
            try: winreg.DeleteValue(key, "RcloneManager")
            except FileNotFoundError: pass
        winreg.CloseKey(key)
        return True
    except Exception as e:
        return str(e)

def parse_rclone_conf(conf_path: Path):
    """rclone.conf 파일을 파싱하여 리모트 목록 반환 (Scenario 2)"""
    remotes = []
    try:
        cfg = configparser.ConfigParser()
        cfg.read(str(conf_path), encoding="utf-8")
        for section in cfg.sections():
            remotes.append({"name": section, "type": cfg.get(section, "type", fallback="")})
    except Exception: pass
    return remotes

# ── 3. 설정 및 경로 관리 ──
if getattr(sys, 'frozen', False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).parent

CONFIG_FILE = APP_DIR / "mounts.json"

def load_config():
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if "mounts" not in cfg: cfg["mounts"] = []
            if "remotes" not in cfg: cfg["remotes"] = []
            return cfg
        except Exception: pass
    return {"remotes": [], "mounts": [], "rclone_path": "", "auto_mount": False}

def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def get_rclone_exe(cfg):
    """설정된 rclone 경로 반환 (Scenario 1)"""
    custom = cfg.get("rclone_path", "").strip()
    if custom and Path(custom).exists():
        return Path(custom)
    return APP_DIR / "rclone.exe"

# ── 4. 마운트 명령어 및 종료 ──
active_mounts = {}

def build_cmd(rclone_exe: Path, mount: dict):
    """마운트 명령어 빌드 (Scenario 7)"""
    rpath = mount.get("remote_path", "").strip().replace("\\", "/").strip("/")
    cmd = [str(rclone_exe), "mount", f"{mount['remote']}:{rpath}", mount["drive"], "--volname", mount.get("label") or mount["remote"]]
    if mount.get("cache_dir"): cmd += ["--cache-dir", mount["cache_dir"]]
    if mount.get("cache_mode"): cmd += ["--vfs-cache-mode", mount["cache_mode"]]
    extra = mount.get("extra_flags", "").strip()
    if extra:
        for f in re.split(r"[\s;]+", extra):
            if f.strip(): cmd.append(f.strip())
    return cmd

def unmount(m_id):
    """마운트 해제 (Scenario 14)"""
    p = active_mounts.get(m_id)
    if p:
        p.terminate()
        try: p.wait(timeout=3)
        except Exception: p.kill()
        active_mounts.pop(m_id, None)

def activate_existing_window():
    """중복 실행 방지 (Scenario 19)"""
    hwnd = ctypes.windll.user32.FindWindowW(None, "RcloneManager")
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        return True
    return False

# ══════════════════════════════════════════════════════════════════════════════
#  메인 앱 클래스
# ══════════════════════════════════════════════════════════════════════════════
class App(tk.Tk):
    def __init__(self):
        self._tray = None 
        super().__init__()
        self.title("RcloneManager")
        self.geometry("1150x850") # 확대된 창 크기
        self.minsize(1050, 750)
        self.protocol("WM_DELETE_WINDOW", self.hide_window)
        
        self._cfg = load_config()
        self._status = {}
        self._build_ui()
        
        self.update_idletasks()
        self._refresh_list()
        self._start_tray()
        self._check_versions_async()
        
        if self._cfg.get("auto_mount"):
            self.after(1500, self._automount_all)

    def _build_ui(self):
        self.configure(bg="#1e1e2e")
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame", background="#1e1e2e")
        s.configure("TLabel", background="#1e1e2e", foreground="#cdd6f4", font=("Segoe UI", 10))
        s.configure("Header.TLabel", font=("Segoe UI", 16, "bold"), foreground="#cba6f7")
        s.configure("AppVer.TLabel", font=("Segoe UI", 10, "bold"), foreground="#fab387")
        s.configure("Treeview", background="#313244", foreground="#cdd6f4", fieldbackground="#313244", rowheight=32)
        s.configure("Treeview.Heading", background="#45475a", foreground="#cba6f7", font=("Segoe UI", 11, "bold"))

        # ── 헤더 ──
        hdr = ttk.Frame(self)
        hdr.pack(fill="x", padx=20, pady=15)
        ttl_f = ttk.Frame(hdr); ttl_f.pack(side="left")
        ttk.Label(ttl_f, text="🚀 RcloneManager", style="Header.TLabel").pack(side="left")
        ttk.Label(ttl_f, text=f" v{APP_VERSION}", style="AppVer.TLabel").pack(side="left", padx=(8, 0), pady=(5, 0))
        tk.Button(ttl_f, text="!", bg="#f38ba8", fg="#1e1e2e", font=("Segoe UI", 9, "bold"), 
                  relief="flat", width=2, command=self._open_issue_report).pack(side="left", padx=12, pady=(5, 0))

        self._app_up_btn = tk.Button(hdr, text="✨ 새 버전 업데이트 가능", bg="#a6e3a1", fg="#1e1e2e", 
                                     font=("Segoe UI", 9, "bold"), relief="flat", command=self._manual_app_update)

        # ── Rclone 경로 및 버전 ──
        rcf = tk.Frame(self, bg="#1e1e2e"); rcf.pack(fill="x", padx=20, pady=5)
        tk.Label(rcf, text="rclone 경로:", bg="#1e1e2e", fg="#cba6f7", font=("Segoe UI", 10, "bold")).pack(side="left")
        self._rc_var = tk.StringVar(value=self._cfg.get("rclone_path", ""))
        tk.Entry(rcf, textvariable=self._rc_var, bg="#313244", fg="#cdd6f4", insertbackground="#cdd6f4", 
                 relief="flat", font=("Segoe UI", 10), width=65).pack(side="left", padx=10, ipady=4)
        tk.Button(rcf, text="📂", bg="#45475a", fg="#cdd6f4", relief="flat", command=self._browse_rc).pack(side="left")
        self._rc_ver_label = ttk.Label(rcf, text="rclone 버전 체크 중...", foreground="#94e2d5")
        self._rc_ver_label.pack(side="left", padx=20)

        # ── 옵션 ──
        opt = tk.Frame(self, bg="#1e1e2e"); opt.pack(fill="x", padx=20, pady=10)
        self._st_var = tk.BooleanVar(value=is_startup_enabled())
        ttk.Checkbutton(opt, text="시작 시 자동 실행", variable=self._st_var, command=self._toggle_st).pack(side="left", padx=(0, 25))
        self._am_var = tk.BooleanVar(value=self._cfg.get("auto_mount", False))
        ttk.Checkbutton(opt, text="시작 시 자동 마운트", variable=self._am_var, command=self._toggle_am).pack(side="left")

        # ── 목록 ──
        cols = ("type", "auto", "drive", "remote", "status")
        self._tree = ttk.Treeview(self, columns=cols, show="headings", height=18)
        for col, head in zip(cols, ("구분", "자동", "드라이브", "리모트 (서브경로)", "상태")):
            self._tree.heading(col, text=head)
        self._tree.pack(fill="both", expand=True, padx=20, pady=5)

        # ── 하단 버튼 ──
        btn_f = ttk.Frame(self); btn_f.pack(fill="x", padx=20, pady=15)
        ttk.Button(btn_f, text="➕ 추가", command=self._add).pack(side="left", padx=2)
        ttk.Button(btn_f, text="✏️ 편집", command=self._edit).pack(side="left", padx=2)
        ttk.Button(btn_f, text="🗑️ 삭제", command=self._del).pack(side="left", padx=2)
        ttk.Button(btn_f, text="🔼", width=4, command=self._move_up).pack(side="left", padx=2)
        ttk.Button(btn_f, text="🔽", width=4, command=self._move_down).pack(side="left", padx=2)
        ttk.Button(btn_f, text="▶ 마운트", command=self._mount_sel).pack(side="left", padx=15)
        ttk.Button(btn_f, text="■ 언마운트", command=self._unmount_sel).pack(side="left")
        self._sbar = ttk.Label(self, text="준비됨", foreground="#a6e3a1")
        self._sbar.pack(fill="x", padx=20, pady=(0, 15))

    def _open_issue_report(self):
        info = get_sys_info()
        body = f"\n\n--- Debug Info ---\n- App Version: {APP_VERSION}\n- {info}"
        url = f"https://github.com/{GITHUB_REPO}/issues/new?body=" + urllib.parse.quote(body)
        webbrowser.open(url)

    def _check_versions_async(self):
        def _task():
            exe = Path(self._rc_var.get())
            if exe.exists():
                try:
                    r = subprocess.run([str(exe), "version"], capture_output=True, text=True, timeout=5)
                    m = re.search(r"rclone v([\d.]+)", r.stdout)
                    local_rc = m.group(1) if m else "Error"
                    res = requests.get("https://api.github.com/repos/rclone/rclone/releases/latest", timeout=5)
                    latest_rc = res.json().get("tag_name", "").lstrip("v")
                    self.after(0, lambda: self._rc_ver_label.config(text=f"rclone: v{local_rc} (최신: v{latest_rc})"))
                except: pass
            try:
                res = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", timeout=5)
                latest_app = res.json().get("tag_name", "").lstrip("v")
                if latest_app != APP_VERSION: self.after(0, lambda: self._app_up_btn.pack(side="right"))
            except: pass
        threading.Thread(target=_task, daemon=True).start()

    def _manual_app_update(self): webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")

    def _refresh_list(self):
        for i in self._tree.get_children(): self._tree.delete(i)
        for m in self._cfg.get("mounts", []):
            st = self._status.get(m["id"], "stopped")
            auto = "✅" if m.get("auto_mount") else "—"
            lbl = "🟢 실행중" if st == "mounted" else "⚫ 중지됨"
            rem_name = m.get('remote', 'Unknown')
            rpath = m.get("remote_path", "").replace("\\", "/").strip("/")
            remote_str = f"{rem_name}:{rpath}" if rpath else rem_name
            self._tree.insert("", "end", iid=m["id"], values=("💾 마운트", auto, m.get("drive","?"), remote_str, lbl))
        if self._tray:
            try: self._tray.update_menu()
            except: pass

    def _automount_all(self):
        for m in self._cfg.get("mounts", []):
            if m.get("auto_mount") and m["id"] not in active_mounts: self._do_mount(m["id"], m)

    def _toggle_st(self): set_startup(self._st_var.get())
    def _toggle_am(self):
        self._cfg["auto_mount"] = self._am_var.get()
        save_config(self._cfg)
    def _browse_rc(self):
        p = filedialog.askopenfilename()
        if p:
            self._rc_var.set(p); self._cfg["rclone_path"] = p
            save_config(self._cfg); self._check_versions_async()

    def _add(self):
        dlg = MountDialog(self, app_cfg=self._cfg)
        self.wait_window(dlg)
        if dlg.result:
            dlg.result["id"] = str(uuid.uuid4())
            self._cfg["mounts"].append(dlg.result); save_config(self._cfg); self._refresh_list()

    def _edit(self):
        sel = self._tree.selection()
        if not sel: return
        idx = next(i for i, m in enumerate(self._cfg["mounts"]) if m["id"] == sel[0])
        dlg = MountDialog(self, mount=self._cfg["mounts"][idx], app_cfg=self._cfg)
        self.wait_window(dlg)
        if dlg.result:
            dlg.result["id"] = sel[0]
            self._cfg["mounts"][idx] = dlg.result; save_config(self._cfg); self._refresh_list()

    def _del(self):
        sel = self._tree.selection()
        if not sel: return
        if messagebox.askyesno("삭제", "선택한 마운트를 삭제하시겠습니까?"):
            self._cfg["mounts"] = [m for m in self._cfg["mounts"] if m["id"] != sel[0]]
            save_config(self._cfg); self._refresh_list()

    def _move_up(self):
        sel = self._tree.selection()
        if not sel: return
        idx = next(i for i, m in enumerate(self._cfg["mounts"]) if m["id"] == sel[0])
        if idx > 0:
            self._cfg["mounts"][idx], self._cfg["mounts"][idx-1] = self._cfg["mounts"][idx-1], self._cfg["mounts"][idx]
            save_config(self._cfg); self._refresh_list(); self._tree.selection_set(sel[0])

    def _move_down(self):
        sel = self._tree.selection()
        if not sel: return
        idx = next(i for i, m in enumerate(self._cfg["mounts"]) if m["id"] == sel[0])
        if idx < len(self._cfg["mounts"]) - 1:
            self._cfg["mounts"][idx], self._cfg["mounts"][idx+1] = self._cfg["mounts"][idx+1], self._cfg["mounts"][idx]
            save_config(self._cfg); self._refresh_list(); self._tree.selection_set(sel[0])

    def _mount_sel(self):
        sel = self._tree.selection()
        if not sel or sel[0] in active_mounts: return
        m = next(m for m in self._cfg["mounts"] if m["id"] == sel[0])
        self._do_mount(sel[0], m)

    def _do_mount(self, mid, m):
        exe = Path(self._cfg.get("rclone_path", ""))
        if not exe.exists(): return
        self._status[mid] = "mounted"; self._refresh_list()
        threading.Thread(target=self._mount_task, args=(mid, exe, m), daemon=True).start()

    def _mount_task(self, mid, exe, m):
        cmd = build_cmd(exe, m)
        try:
            p = subprocess.Popen(cmd, creationflags=0x08000000)
            active_mounts[mid] = p; p.wait()
        finally:
            active_mounts.pop(mid, None); self._status[mid] = "stopped"
            self.after(0, self._refresh_list)

    def _unmount_sel(self):
        sel = self._tree.selection()
        if sel and sel[0] in active_mounts: unmount(sel[0])

    def _start_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
            img = Image.new("RGBA", (64,64), (0,0,0,0)); d = ImageDraw.Draw(img)
            d.ellipse([2,2,62,62], fill="#cba6f7")
            self._tray = pystray.Icon("RcloneManager", img, "RcloneManager", 
                                       menu=pystray.Menu(pystray.MenuItem("열기", lambda: self.after(0, self.show_window)), 
                                                         pystray.MenuItem("종료", lambda: self.after(0, self._quit_app))))
            threading.Thread(target=self._tray.run, daemon=True).start()
        except: pass

    def hide_window(self): self.withdraw()
    def show_window(self): self.deiconify(); self.lift(); self.focus_force()
    def _quit_app(self):
        for mid in list(active_mounts.keys()): unmount(mid)
        if self._tray: self._tray.stop()
        self.destroy()

# ══════════════════════════════════════════════════════════════════════════════
#  마운트 추가/편집 다이얼로그
# ══════════════════════════════════════════════════════════════════════════════
class MountDialog(tk.Toplevel):
    def __init__(self, parent, mount=None, app_cfg=None):
        super().__init__(parent); self.title("마운트 설정"); self.geometry("600x750"); self.grab_set()
        self.result = None; self._m = mount or {}; self._app_cfg = app_cfg; self._build()
    
    def _build(self):
        c = tk.Frame(self, padx=25, pady=25); c.pack(fill="both", expand=True)
        tk.Label(c, text="리모트 이름:").pack(anchor="w")
        self._rem = tk.Entry(c); self._rem.pack(fill="x", pady=5); self._rem.insert(0, self._m.get("remote", ""))
        
        tk.Label(c, text="드라이브 문자 (Z:):").pack(anchor="w")
        self._drv = tk.Entry(c); self._drv.pack(fill="x", pady=5); self._drv.insert(0, self._m.get("drive", ""))
        
        tk.Label(c, text="서브 경로:").pack(anchor="w")
        self._pth = tk.Entry(c); self._pth.pack(fill="x", pady=5); self._pth.insert(0, self._m.get("remote_path", ""))
        
        tk.Label(c, text="캐시 폴더 (선택):").pack(anchor="w")
        cf = tk.Frame(c); cf.pack(fill="x")
        self._cdir = tk.StringVar(value=self._m.get("cache_dir", ""))
        tk.Entry(cf, textvariable=self._cdir).pack(side="left", fill="x", expand=True)
        tk.Button(cf, text="📂", command=self._browse).pack(side="left")

        tk.Label(c, text="추가 플래그 (예: --read-only):").pack(anchor="w")
        self._ext = tk.Text(c, height=5); self._ext.pack(fill="x", pady=5); self._ext.insert("1.0", self._m.get("extra_flags", ""))
        
        tk.Button(c, text="연결 테스트", bg="#89b4fa", command=self._test).pack(fill="x", pady=5)
        
        self._auto = tk.BooleanVar(value=self._m.get("auto_mount", False))
        tk.Checkbutton(c, text="이 항목 자동 마운트", variable=self._auto).pack(anchor="w", pady=10)
        
        tk.Button(c, text="저장", bg="#cba6f7", command=self._save).pack(pady=20, fill="x")

    def _browse(self):
        d = filedialog.askdirectory()
        if d: self._cdir.set(d)

    def _test(self):
        """연결 테스트 (Scenario 4)"""
        rem = self._rem.get().strip()
        pth = self._pth.get().strip().strip("/")
        target = f"{rem}:{pth}"
        exe = get_rclone_exe(self._app_cfg)
        def r():
            try:
                p = subprocess.run([str(exe), "lsf", target, "--max-depth", "1"], capture_output=True, text=True, timeout=10, creationflags=0x08000000)
                if p.returncode == 0: messagebox.showinfo("성공", "연결 확인 완료!")
                else: messagebox.showerror("실패", f"연결 불가:\n{p.stderr.strip()}")
            except Exception as e: messagebox.showerror("오류", str(e))
        threading.Thread(target=r, daemon=True).start()

    def _save(self):
        rem = self._rem.get().strip()
        drv = self._drv.get().strip()
        if not rem: return messagebox.showwarning("오류", "리모트 이름 필수")
        for m in self._app_cfg.get("mounts", []):
            if m.get("id") == self._m.get("id"): continue
            if drv and m.get("drive") == drv: return messagebox.showerror("오류", "드라이브 문자 중복")
        self.result = {
            "remote": rem, "drive": drv, "remote_path": self._pth.get().strip(), 
            "cache_dir": self._cdir.get(),
            "extra_flags": self._ext.get("1.0", tk.END).strip(), "auto_mount": self._auto.get()
        }
        self.destroy()

if __name__ == "__main__":
    if activate_existing_window(): sys.exit(0)
    app = App(); app.mainloop()
