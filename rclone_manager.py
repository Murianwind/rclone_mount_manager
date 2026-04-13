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
APP_VERSION = "1.0.2"
GITHUB_REPO = "Murianwind/rclone_mount_manager"

# ── 1. 고해상도(DPI) 대응 및 단일 인스턴스 활성화 ──
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

def get_sys_info():
    """사용자의 해상도 및 배율 정보를 문자열로 반환합니다."""
    try:
        user32 = ctypes.windll.user32
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        hdc = user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88) # LOGPIXELSX
        user32.ReleaseDC(0, hdc)
        scale = int((dpi / 96) * 100)
        return f"Resolution: {w}x{h}, Scaling: {scale}%"
    except Exception:
        return "Resolution/Scaling info unavailable"

def activate_existing_window():
    hwnd = ctypes.windll.user32.FindWindowW(None, "RcloneManager")
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        return True
    return False

# ── 경로 설정 ──
if getattr(sys, 'frozen', False):
    APP_DIR = Path(sys.executable).parent
    APP_EXE = Path(sys.executable)
else:
    APP_DIR = Path(__file__).parent
    APP_EXE = Path(sys.executable)

CONFIG_FILE = APP_DIR / "mounts.json"
STARTUP_NAME = "RcloneManager"

def load_config():
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            for m in cfg.get("mounts", []):
                if "id" not in m: m["id"] = str(uuid.uuid4())
                if "remote" not in m: m["remote"] = "Unknown"
            if "remotes" not in cfg: cfg["remotes"] = []
            if "auto_mount" not in cfg: cfg["auto_mount"] = False
            return cfg
        except Exception: pass
    return {"remotes": [], "mounts": [], "rclone_path": "", "auto_mount": False}

def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def get_rclone_exe(cfg):
    custom = cfg.get("rclone_path", "").strip()
    if custom and Path(custom).exists(): return Path(custom)
    return APP_DIR / "rclone.exe"

# ── 유틸리티 ──
def build_cmd(rclone_exe: Path, mount: dict):
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
    p = active_mounts.get(m_id)
    if p:
        p.terminate()
        try: p.wait(timeout=3)
        except Exception: p.kill()
        active_mounts.pop(m_id, None)

active_mounts = {}

# ══════════════════════════════════════════════════════════════════════════════
#  메인 앱 클래스
# ══════════════════════════════════════════════════════════════════════════════
class App(tk.Tk):
    def __init__(self):
        self._tray = None # RecursionError 방지
        super().__init__()
        self.title("RcloneManager")
        
        # 메인 창 크기 확대 (요청 사항 반영)
        self.geometry("1150x850")
        self.minsize(1000, 750)
        
        self.protocol("WM_DELETE_WINDOW", self.hide_window)
        self._cfg = load_config()
        self._status = {}
        
        self._build_ui()
        self.update_idletasks()
        self._refresh_list()
        self._start_tray()
        self._check_versions_async()

    def _build_ui(self):
        self.configure(bg="#1e1e2e")
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame", background="#1e1e2e")
        s.configure("TLabel", background="#1e1e2e", foreground="#cdd6f4", font=("Segoe UI", 10))
        s.configure("Header.TLabel", font=("Segoe UI", 16, "bold"), foreground="#cba6f7")
        s.configure("AppVer.TLabel", font=("Segoe UI", 10, "bold"), foreground="#fab387")
        s.configure("TButton", font=("Segoe UI", 9), padding=4)
        s.configure("Treeview", background="#313244", foreground="#cdd6f4", fieldbackground="#313244", rowheight=32, font=("Segoe UI", 10))
        s.configure("Treeview.Heading", background="#45475a", foreground="#cba6f7", font=("Segoe UI", 11, "bold"))

        # ── 1. 헤더 영역 (App Version & Issue Button) ──
        hdr = ttk.Frame(self)
        hdr.pack(fill="x", padx=20, pady=15)
        
        ttl_f = ttk.Frame(hdr)
        ttl_f.pack(side="left")
        ttk.Label(ttl_f, text="🚀 RcloneManager", style="Header.TLabel").pack(side="left")
        ttk.Label(ttl_f, text=f" v{APP_VERSION}", style="AppVer.TLabel").pack(side="left", padx=(8, 0), pady=(5, 0))
        
        # 이슈 등록 버튼 (!)
        tk.Button(ttl_f, text="!", bg="#f38ba8", fg="#1e1e2e", font=("Segoe UI", 9, "bold"), 
                  relief="flat", width=2, command=self._open_issue_report).pack(side="left", padx=12, pady=(5, 0))

        # 앱 업데이트 버튼 (업데이트 발견 시 활성화)
        self._app_up_btn = tk.Button(hdr, text="✨ 새 버전 업데이트", bg="#a6e3a1", fg="#1e1e2e", 
                                     font=("Segoe UI", 9, "bold"), relief="flat", command=self._manual_app_update)
        # 기본적으로 숨김 (pack 안 함)

        # ── 2. Rclone 경로 및 버전 정보 (배치 수정) ──
        rcf = tk.Frame(self, bg="#1e1e2e")
        rcf.pack(fill="x", padx=20, pady=5)
        
        tk.Label(rcf, text="rclone 경로:", bg="#1e1e2e", fg="#cba6f7", font=("Segoe UI", 10, "bold")).pack(side="left")
        self._rc_var = tk.StringVar(value=self._cfg.get("rclone_path", ""))
        tk.Entry(rcf, textvariable=self._rc_var, bg="#313244", fg="#cdd6f4", insertbackground="#cdd6f4", 
                 relief="flat", font=("Segoe UI", 10), highlightthickness=1, highlightbackground="#585b70", 
                 highlightcolor="#cba6f7", width=65).pack(side="left", padx=10, ipady=4)
        
        tk.Button(rcf, text="📂", bg="#45475a", fg="#cdd6f4", relief="flat", command=self._browse_rc).pack(side="left")
        
        # rclone 버전 정보 (경로 입력창 옆으로 이동)
        self._rc_ver_label = ttk.Label(rcf, text="rclone 버전 체크 중...", foreground="#94e2d5")
        self._rc_ver_label.pack(side="left", padx=20)

        # ── 3. 옵션 ──
        opt = tk.Frame(self, bg="#1e1e2e")
        opt.pack(fill="x", padx=20, pady=12)
        self._st_var = tk.BooleanVar(value=self._is_startup())
        ttk.Checkbutton(opt, text="시작 시 자동 실행", variable=self._st_var, command=self._toggle_st).pack(side="left", padx=(0, 25))
        self._am_var = tk.BooleanVar(value=self._cfg.get("auto_mount", False))
        ttk.Checkbutton(opt, text="시작 시 자동 마운트", variable=self._am_var, command=self._toggle_am).pack(side="left")

        # ── 4. 리스트 (Treeview) ──
        cols = ("type", "auto", "drive", "remote", "status")
        self._tree = ttk.Treeview(self, columns=cols, show="headings", height=18)
        for col, head in zip(cols, ("구분", "자동", "드라이브", "리모트 (서브경로)", "상태")):
            self._tree.heading(col, text=head)
        
        self._tree.column("type", width=130, anchor="center")
        self._tree.column("auto", width=80, anchor="center")
        self._tree.column("drive", width=100, anchor="center")
        self._tree.column("remote", width=550)
        self._tree.column("status", width=140, anchor="center")
        self._tree.pack(fill="both", expand=True, padx=20, pady=5)

        # ── 5. 하단 버튼 ──
        btn_f = ttk.Frame(self)
        btn_f.pack(fill="x", padx=20, pady=15)
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
        """GitHub 이슈 생성 페이지를 열고 배율/해상도 정보를 입력합니다."""
        info = get_sys_info()
        body = f"\n\n--- Debug Info ---\n- App Version: {APP_VERSION}\n- {info}"
        url = f"https://github.com/{GITHUB_REPO}/issues/new?body=" + urllib.parse.quote(body)
        webbrowser.open(url)

    def _check_versions_async(self):
        """rclone 및 앱 버전을 비동기로 체크합니다."""
        def _task():
            # 1. rclone 버전 체크
            exe = get_rclone_exe(self._cfg)
            try:
                r = subprocess.run([str(exe), "version"], capture_output=True, text=True, timeout=5)
                m = re.search(r"rclone v([\d.]+)", r.stdout)
                local_rc = m.group(1) if m else "Error"
                
                res = requests.get("https://api.github.com/repos/rclone/rclone/releases/latest", timeout=5)
                latest_rc = res.json().get("tag_name", "").lstrip("v")
                self.after(0, lambda: self._rc_ver_label.config(text=f"rclone: v{local_rc} (최신: v{latest_rc})"))
            except:
                self.after(0, lambda: self._rc_ver_label.config(text="rclone 버전 확인 불가"))

            # 2. 앱 버전 업데이트 체크 (가정)
            try:
                # 실제 배포 시에는 GitHub API를 통해 최신 태그를 가져옵니다.
                res = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", timeout=5)
                latest_app = res.json().get("tag_name", "").lstrip("v")
                if latest_app != APP_VERSION:
                    self.after(0, lambda: self._app_up_btn.pack(side="right"))
            except: pass

        threading.Thread(target=_task, daemon=True).start()

    def _manual_app_update(self):
        """앱 업데이트를 진행합니다."""
        if messagebox.askyesno("업데이트", "최신 버전이 있습니다. 저장소 페이지를 열어 다운로드할까요?"):
            webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")

    def _refresh_list(self):
        for i in self._tree.get_children(): self._tree.delete(i)
        for r in self._cfg.get("remotes", []):
            self._tree.insert("", "end", iid=f"remote_{r['name']}", values=("☁️ 원본", "—", "—", f"[{r['type']}] {r['name']}", "설정 대기"), tags=("remote_tag",))
        for m in self._cfg.get("mounts", []):
            st = self._status.get(m["id"], "stopped")
            auto = "✅" if m.get("auto_mount") else "—"
            tag = "on" if st == "mounted" else "off"
            lbl = "🟢 실행중" if st == "mounted" else "⚫ 중지됨"
            rem_name = m.get('remote', 'Unknown')
            rpath = m.get("remote_path", "").replace("\\", "/").strip("/")
            remote_str = f"{rem_name}:{rpath}" if rpath else rem_name
            self._tree.insert("", "end", iid=m["id"], values=("💾 마운트", auto, m.get("drive","?"), remote_str, lbl), tags=(tag,))
        
        if self._tray is not None:
            try: self._tray.update_menu()
            except: pass

    # --- 기존 설정 관리 메서드 (안전하게 유지) ---
    def _is_startup(self):
        return is_startup_enabled()

    def _toggle_st(self): set_startup(self._st_var.get())
    def _toggle_am(self):
        self._cfg["auto_mount"] = self._am_var.get()
        save_config(self._cfg)

    def _browse_rc(self):
        p = filedialog.askopenfilename()
        if p:
            self._rc_var.set(p)
            self._cfg["rclone_path"] = p
            save_config(self._cfg)
            self._check_versions_async()

    def _sel_id(self):
        sel = self._tree.selection()
        return sel[0] if sel else None

    def _get_m(self, mid):
        for i, m in enumerate(self._cfg["mounts"]):
            if m.get("id") == mid: return i, m
        return None, None

    def _add(self):
        mid = self._sel_id()
        pre = mid.split("remote_", 1)[1] if mid and mid.startswith("remote_") else ""
        dlg = MountDialog(self, {"remote": pre}, self._cfg)
        self.wait_window(dlg)
        if dlg.result:
            dlg.result["id"] = str(uuid.uuid4())
            self._cfg["mounts"].append(dlg.result)
            save_config(self._cfg); self._refresh_list()

    def _edit(self):
        mid = self._sel_id()
        if not mid or mid.startswith("remote_"): return
        idx, m = self._get_m(mid)
        dlg = MountDialog(self, m, self._cfg)
        self.wait_window(dlg)
        if dlg.result:
            dlg.result["id"] = mid
            self._cfg["mounts"][idx] = dlg.result
            save_config(self._cfg); self._refresh_list()

    def _del(self):
        mid = self._sel_id()
        if not mid: return
        if messagebox.askyesno("삭제", "항목을 삭제할까요?"):
            if mid.startswith("remote_"):
                r_name = mid.split("remote_", 1)[1]
                self._cfg["remotes"] = [r for r in self._cfg["remotes"] if r["name"] != r_name]
            else:
                idx, _ = self._get_m(mid)
                unmount(mid); self._cfg["mounts"].pop(idx)
            save_config(self._cfg); self._refresh_list()

    def _move_up(self):
        mid = self._sel_id()
        if not mid: return
        idx, m = self._get_m(mid)
        if idx and idx > 0:
            self._cfg["mounts"][idx], self._cfg["mounts"][idx-1] = self._cfg["mounts"][idx-1], self._cfg["mounts"][idx]
            save_config(self._cfg); self._refresh_list(); self._tree.selection_set(mid)

    def _move_down(self):
        mid = self._sel_id()
        if not mid: return
        idx, m = self._get_m(mid)
        if idx is not None and idx < len(self._cfg["mounts"])-1:
            self._cfg["mounts"][idx], self._cfg["mounts"][idx+1] = self._cfg["mounts"][idx+1], self._cfg["mounts"][idx]
            save_config(self._cfg); self._refresh_list(); self._tree.selection_set(mid)

    def _mount_sel(self):
        mid = self._sel_id()
        if mid and not mid.startswith("remote_") and mid not in active_mounts:
            _, m = self._get_m(mid)
            exe = get_rclone_exe(self._cfg)
            if not exe.exists(): return
            self._status[mid] = "mounted"
            self._refresh_list()
            threading.Thread(target=self._mount_task, args=(mid, exe, m), daemon=True).start()

    def _mount_task(self, mid, exe, m):
        cmd = build_cmd(exe, m)
        try:
            p = subprocess.Popen(cmd, creationflags=0x08000000)
            active_mounts[mid] = p
            p.wait()
        finally:
            active_mounts.pop(mid, None)
            self._status[mid] = "stopped"
            self.after(0, self._refresh_list)

    def _unmount_sel(self):
        mid = self._sel_id()
        if mid: unmount(mid)

    def _start_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
            img = Image.new("RGBA", (64,64), (0,0,0,0)); d = ImageDraw.Draw(img)
            d.ellipse([2,2,62,62], fill="#cba6f7")
            def on_show(icon, item): self.after(0, self.show_window)
            def on_quit(icon, item): self.after(0, self._quit_app)
            self._tray = pystray.Icon("RcloneManager", img, "RcloneManager", 
                                       menu=pystray.Menu(pystray.MenuItem("열기", on_show), pystray.MenuItem("종료", on_quit)))
            threading.Thread(target=self._tray.run, daemon=True).start()
        except: pass

    def hide_window(self): self.withdraw()
    def show_window(self): self.deiconify(); self.lift(); self.focus_force()
    def _quit_app(self):
        for mid in list(active_mounts.keys()): unmount(mid)
        if self._tray: self._tray.stop()
        self.destroy()

class MountDialog(tk.Toplevel):
    def __init__(self, parent, mount=None, app_cfg=None):
        super().__init__(parent)
        self.title("마운트 설정")
        self.geometry("600x750")
        self.grab_set()
        self.result = None
        self._m = mount or {}
        self._app_cfg = app_cfg
        self._build()

    def _build(self):
        c = tk.Frame(self, padx=25, pady=25)
        c.pack(fill="both", expand=True)
        tk.Label(c, text="리모트 이름:").pack(anchor="w")
        self._remote = tk.StringVar(value=self._m.get("remote", ""))
        tk.Entry(c, textvariable=self._remote).pack(fill="x", pady=8)
        
        tk.Label(c, text="드라이브 문자 (예: Z:):").pack(anchor="w")
        self._drive = tk.StringVar(value=self._m.get("drive", ""))
        tk.Entry(c, textvariable=self._drive).pack(fill="x", pady=8)

        tk.Label(c, text="추가 플래그:").pack(anchor="w")
        self._extra_text = tk.Text(c, height=6)
        self._extra_text.pack(fill="x", pady=8)
        self._extra_text.insert("1.0", self._m.get("extra_flags", ""))

        tk.Button(c, text="저장", bg="#cba6f7", fg="#1e1e2e", font=("Segoe UI", 10, "bold"), 
                  command=self._save).pack(pady=30, fill="x")

    def _save(self):
        self.result = {
            "remote": self._remote.get().strip(),
            "drive": self._drive.get().strip(),
            "extra_flags": self._extra_text.get("1.0", tk.END).strip()
        }
        self.destroy()

if __name__ == "__main__":
    if activate_existing_window(): sys.exit(0)
    app = App(); app.mainloop()
