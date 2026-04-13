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
APP_VERSION = "1.0.8"
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
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88) # LOGPIXELSX
        user32.ReleaseDC(0, hdc)
        scale = int((dpi / 96) * 100)
        return f"Resolution: {w}x{h}, Scaling: {scale}%"
    except Exception:
        return "Resolution/Scaling info unavailable"

# ── 2. 시작 프로그램 관련 유틸리티 (NameError 방지) ──
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
    """rclone.conf 파싱 (Scenario 2)"""
    remotes = []
    try:
        cfg = configparser.ConfigParser()
        cfg.read(str(conf_path), encoding="utf-8")
        for section in cfg.sections():
            remotes.append({"name": section, "type": cfg.get(section, "type", fallback="")})
    except Exception: pass
    return remotes

# ── 3. rclone 다운로드 및 업데이트 로직 ──
def download_rclone(dest_dir: Path, version: str, progress_cb=None):
    """rclone 실행 파일 다운로드 및 설치 (Scenario 15)"""
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
                if progress_cb and total:
                    progress_cb(int(downloaded * 100 / total))
        with zipfile.ZipFile(tmp, "r") as z:
            for name in z.namelist():
                if name.endswith("rclone.exe"):
                    data = z.read(name)
                    dest = dest_dir / "rclone.exe"
                    dest.write_bytes(data)
                    break
        os.unlink(tmp)
        return True
    except Exception as e:
        return str(e)

# ── 4. 설정 및 경로 관리 ──
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
        except: pass
    return {"remotes": [], "mounts": [], "rclone_path": "", "auto_mount": False}

def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def get_rclone_exe(cfg):
    """rclone 경로 반환 (Scenario 1)"""
    custom = cfg.get("rclone_path", "").strip()
    if custom and Path(custom).exists(): return Path(custom)
    return APP_DIR / "rclone.exe"

# ── 5. 마운트 명령어 빌드 ──
active_mounts = {}

def build_cmd(rclone_exe: Path, mount: dict):
    """명령어 생성 (Scenario 7)"""
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
    """언마운트 (Scenario 14)"""
    p = active_mounts.get(m_id)
    if p:
        p.terminate()
        try: p.wait(timeout=3)
        except: p.kill()
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
        self.geometry("1100x800")
        self.minsize(1000, 700)
        self.protocol("WM_DELETE_WINDOW", self.hide_window)
        
        self._cfg = load_config()
        self._status = {}
        self._latest_rc = ""
        self._build_ui()
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
        s.configure("Treeview", background="#313244", foreground="#cdd6f4", fieldbackground="#313244", rowheight=30)
        s.configure("Treeview.Heading", background="#45475a", foreground="#cba6f7", font=("Segoe UI", 11, "bold"))

        # 헤더 영역
        hdr = ttk.Frame(self); hdr.pack(fill="x", padx=20, pady=15)
        ttl_f = ttk.Frame(hdr); ttl_f.pack(side="left")
        ttk.Label(ttl_f, text="🚀 RcloneManager", style="Header.TLabel").pack(side="left")
        ttk.Label(ttl_f, text=f" v{APP_VERSION}", foreground="#fab387", font=("Segoe UI", 10, "bold")).pack(side="left", padx=8, pady=(5,0))
        tk.Button(ttl_f, text="!", bg="#f38ba8", fg="#1e1e2e", font=("Segoe UI", 9, "bold"), 
                  relief="flat", width=2, command=self._open_issue).pack(side="left", padx=5, pady=(5,0))

        # 업데이트 버튼 (정식 릴리스만 체크)
        self._app_up_btn = tk.Button(hdr, text="✨ 새 버전 업데이트 가능", bg="#a6e3a1", fg="#1e1e2e", 
                                     font=("Segoe UI", 9, "bold"), relief="flat", 
                                     command=lambda: webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest"))

        # Rclone 경로 및 버전 정보 영역
        rcf = tk.Frame(self, bg="#1e1e2e"); rcf.pack(fill="x", padx=20, pady=5)
        tk.Label(rcf, text="rclone 경로:", bg="#1e1e2e", fg="#cba6f7", font=("Segoe UI", 10, "bold")).pack(side="left")
        self._rc_var = tk.StringVar(value=self._cfg.get("rclone_path", ""))
        tk.Entry(rcf, textvariable=self._rc_var, bg="#313244", fg="#cdd6f4", insertbackground="#cdd6f4", 
                 relief="flat", font=("Segoe UI", 10), width=60).pack(side="left", padx=10, ipady=4)
        tk.Button(rcf, text="📂", bg="#45475a", fg="#cdd6f4", relief="flat", command=self._browse_rc).pack(side="left")
        
        # rclone 버전/업데이트 레이블 (클릭 이벤트 포함)
        self._rc_ver_label = tk.Label(rcf, text="rclone 버전 체크 중...", bg="#1e1e2e", fg="#94e2d5", cursor="hand2")
        self._rc_ver_label.pack(side="left", padx=15)
        self._rc_ver_label.bind("<Button-1>", self._handle_rc_click)

        # 옵션 영역
        opt = tk.Frame(self, bg="#1e1e2e"); opt.pack(fill="x", padx=20, pady=10)
        self._st_var = tk.BooleanVar(value=is_startup_enabled())
        ttk.Checkbutton(opt, text="시작 시 자동 실행", variable=self._st_var, command=self._toggle_st).pack(side="left", padx=(0, 25))
        self._am_var = tk.BooleanVar(value=self._cfg.get("auto_mount", False))
        ttk.Checkbutton(opt, text="시작 시 자동 마운트", variable=self._am_var, command=self._toggle_am).pack(side="left")

        # 목록 영역 (하단 버튼 노출을 위해 height 조절)
        cols = ("type", "auto", "drive", "remote", "status")
        self._tree = ttk.Treeview(self, columns=cols, show="headings", height=14)
        for col, head in zip(cols, ("구분", "자동", "드라이브", "리모트 (서브경로)", "상태")):
            self._tree.heading(col, text=head)
        self._tree.pack(fill="both", expand=True, padx=20, pady=5)

        # 하단 버튼 영역
        btn_f = ttk.Frame(self); btn_f.pack(fill="x", padx=20, pady=15)
        ttk.Button(btn_f, text="➕ 추가", command=self._add).pack(side="left", padx=2)
        ttk.Button(btn_f, text="✏️ 편집", command=self._edit).pack(side="left", padx=2)
        ttk.Button(btn_f, text="🗑️ 삭제", command=self._del).pack(side="left", padx=2)
        ttk.Button(btn_f, text="🔼", width=4, command=self._move_up).pack(side="left", padx=2)
        ttk.Button(btn_f, text="🔽", width=4, command=self._move_down).pack(side="left", padx=2)
        ttk.Button(btn_f, text="▶ 마운트", command=self._mount_sel).pack(side="left", padx=15)
        ttk.Button(btn_f, text="■ 언마운트", command=self._unmount_sel).pack(side="left")

    def _open_issue(self):
        info = get_sys_info()
        body = urllib.parse.quote(f"\n\n--- Debug Info ---\n- App Version: {APP_VERSION}\n- {info}")
        webbrowser.open(f"https://github.com/{GITHUB_REPO}/issues/new?body={body}")

    def _check_versions_async(self):
        """rclone 및 앱의 정식 릴리스 버전을 체크합니다."""
        def _task():
            # 1. rclone 버전 체크 및 업데이트 안내 로직 (사용자 요청 반영)
            exe = Path(self._rc_var.get())
            lat_rc = ""
            try:
                res = requests.get("https://api.github.com/repos/rclone/rclone/releases/latest", timeout=5)
                lat_rc = res.json().get("tag_name", "").lstrip("v")
                self._latest_rc = lat_rc
            except: pass

            if not exe.exists():
                # 등록되지 않은 경우: 최신 버전만 표시 (받기 유도)
                msg = f"v{lat_rc} 받기" if lat_rc else "rclone 없음"
                self.after(0, lambda: self._rc_ver_label.config(text=msg, fg="#f38ba8"))
            else:
                try:
                    r = subprocess.run([str(exe), "version"], capture_output=True, text=True, timeout=5)
                    loc_rc_match = re.search(r"rclone v([\d.]+)", r.stdout)
                    loc_rc = loc_rc_match.group(1) if loc_rc_match else "알수없음"
                    
                    if lat_rc and loc_rc < lat_rc:
                        # 업데이트가 있는 경우: 현재/최신 병기
                        self.after(0, lambda: self._rc_ver_label.config(text=f"v{loc_rc} / v{lat_rc} 업데이트", fg="#fab387"))
                    else:
                        # 업데이트가 없는 경우: 현재 버전만 표시
                        self.after(0, lambda: self._rc_ver_label.config(text=f"v{loc_rc}", fg="#94e2d5"))
                except: pass

            # 2. 앱 정식 릴리스 업데이트 체크
            try:
                res = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", timeout=5)
                latest_app = res.json().get("tag_name", "").lstrip("v")
                if latest_app > APP_VERSION:
                    self.after(0, lambda: self._app_up_btn.pack(side="right"))
            except: pass
        threading.Thread(target=_task, daemon=True).start()

    def _handle_rc_click(self, event):
        """rclone 레이블 클릭 시 다운로드 또는 업데이트 수행"""
        text = self._rc_ver_label.cget("text")
        if "받기" in text or "업데이트" in text:
            action = "다운로드" if "받기" in text else "업데이트"
            if messagebox.askyesno("rclone", f"rclone v{self._latest_rc}를 {action}할까요?"):
                threading.Thread(target=self._do_rc_down, daemon=True).start()

    def _do_rc_down(self):
        """실제 rclone 다운로드 및 설치 프로세스"""
        self.after(0, lambda: self._rc_ver_label.config(text="다운로드 중... 0%"))
        res = download_rclone(APP_DIR, self._latest_rc, lambda p: self.after(0, lambda: self._rc_ver_label.config(text=f"다운로드 중... {p}%")))
        if res is True:
            self.after(0, lambda: messagebox.showinfo("완료", "rclone 설치가 완료되었습니다."))
            self._check_versions_async()
        else: self.after(0, lambda: messagebox.showerror("오류", res))

    def _refresh_list(self):
        for i in self._tree.get_children(): self._tree.delete(i)
        for m in self._cfg.get("mounts", []):
            st = self._status.get(m["id"], "stopped")
            auto = "✅" if m.get("auto_mount") else "—"
            lbl = "🟢 실행중" if st == "mounted" else "⚫ 중지됨"
            remote_str = f"{m['remote']}:{m.get('remote_path','')}".strip(":")
            self._tree.insert("", "end", iid=m["id"], values=("💾 마운트", auto, m.get("drive","?"), remote_str, lbl))
        if self._tray:
            try: self._tray.update_menu()
            except: pass

    def _toggle_st(self): set_startup(self._st_var.get())
    def _toggle_am(self): self._cfg["auto_mount"] = self._am_var.get(); save_config(self._cfg)
    def _browse_rc(self):
        p = filedialog.askopenfilename()
        if p: self._rc_var.set(p); self._cfg["rclone_path"] = p; save_config(self._cfg); self._check_versions_async()

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
        if messagebox.askyesno("삭제", "선택한 항목을 삭제할까요?"):
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
        exe = get_rclone_exe(self._cfg)
        if exe.exists():
            self._status[mid] = "mounted"; self._refresh_list()
            threading.Thread(target=self._mount_task, args=(mid, exe, m), daemon=True).start()

    def _mount_task(self, mid, exe, m):
        try:
            p = subprocess.Popen(build_cmd(exe, m), creationflags=0x08000000)
            active_mounts[mid] = p; p.wait()
        finally:
            active_mounts.pop(mid, None); self._status[mid] = "stopped"; self.after(0, self._refresh_list)

    def _automount_all(self):
        for m in self._cfg.get("mounts", []):
            if m.get("auto_mount"): self._do_mount(m["id"], m)

    def _unmount_sel(self):
        sel = self._tree.selection()
        if sel: unmount(sel[0])

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
#  마운트 다이얼로그 (서브 경로 옆 연결 테스트 레이아웃 복원)
# ══════════════════════════════════════════════════════════════════════════════
class MountDialog(tk.Toplevel):
    def __init__(self, parent, mount=None, app_cfg=None):
        super().__init__(parent); self.title("마운트 설정"); self.geometry("650x750"); self.grab_set()
        self.result = None; self._m = mount or {}; self._app_cfg = app_cfg; self._build()
    
    def _build(self):
        c = tk.Frame(self, padx=25, pady=25); c.pack(fill="both", expand=True)
        tk.Label(c, text="리모트 이름:").pack(anchor="w")
        self._rem = tk.Entry(c); self._rem.pack(fill="x", pady=5); self._rem.insert(0, self._m.get("remote", ""))
        
        tk.Label(c, text="드라이브 문자 (Z:):").pack(anchor="w")
        self._drv = tk.Entry(c); self._drv.pack(fill="x", pady=5); self._drv.insert(0, self._m.get("drive", ""))
        
        # 서브 경로 입력창 + 연결 테스트 버튼 (이미지 2 복원)
        tk.Label(c, text="서브 경로:").pack(anchor="w")
        pth_f = tk.Frame(c); pth_f.pack(fill="x", pady=5)
        self._pth = tk.Entry(pth_f); self._pth.pack(side="left", fill="x", expand=True)
        self._pth.insert(0, self._m.get("remote_path", ""))
        tk.Button(pth_f, text="연결 테스트", bg="#89b4fa", fg="#1e1e2e", font=("Segoe UI", 9, "bold"), 
                  command=self._test).pack(side="left", padx=5)
        
        tk.Label(c, text="추가 플래그 (예: --read-only):").pack(anchor="w")
        self._ext = tk.Text(c, height=5); self._ext.pack(fill="x", pady=5); self._ext.insert("1.0", self._m.get("extra_flags", ""))
        
        self._auto = tk.BooleanVar(value=self._m.get("auto_mount", False))
        tk.Checkbutton(c, text="이 항목 자동 마운트", variable=self._auto).pack(anchor="w", pady=10)
        
        tk.Button(c, text="저장", bg="#cba6f7", font=("Segoe UI", 10, "bold"), command=self._save).pack(pady=20, fill="x")

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
        """저장 시 중복 체크 로직 (Scenario 06, 12 대응)"""
        rem = self._rem.get().strip()
        drv = self._drv.get().strip()
        pth = self._pth.get().strip()
        if not rem: return messagebox.showwarning("오류", "리모트 이름 필수")
        
        # 기존 마운트 목록과 비교하여 중복 체크
        for m in self._app_cfg.get("mounts", []):
            # 현재 편집 중인 항목(동일 ID)은 제외
            if m.get("id") == self._m.get("id"): continue
            
            # Scenario 06: 드라이브 문자 중복 체크
            if drv and m.get("drive") == drv:
                return messagebox.showerror("오류", "드라이브 문자 중복")
            
            # Scenario 12: 동일 리모트/경로 중복 체크
            if m.get("remote") == rem and m.get("remote_path", "") == pth:
                return messagebox.showerror("오류", "동일한 리모트/경로가 이미 등록되어 있습니다.")

        self.result = {"remote": rem, "drive": drv, "remote_path": pth, 
                       "extra_flags": self._ext.get("1.0", tk.END).strip(), "auto_mount": self._auto.get()}
        self.destroy()

if __name__ == "__main__":
    if activate_existing_window(): sys.exit(0)
    app = App(); app.mainloop()
