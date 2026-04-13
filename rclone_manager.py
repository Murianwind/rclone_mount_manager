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

# --- 시스템 호출 및 환경 설정 ---
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

def get_dpi_scale():
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return dpi / 96
    except Exception:
        return 1.0

def scale(pixel):
    return int(pixel * get_dpi_scale())

def get_sys_info():
    try:
        user32 = ctypes.windll.user32
        w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        return f"Resolution: {w}x{h}, Scaling: {int(get_dpi_scale() * 100)}%"
    except Exception:
        return "N/A"

# --- 프로그램 정보 ---
APP_VERSION = "1.1.1"
GITHUB_REPO = "Murianwind/rclone_mount_manager"

# --- 유틸리티 함수 ---
def is_startup_enabled():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, "RcloneManager")
        winreg.CloseKey(key)
        return True
    except Exception:
        return False

def set_startup(enable: bool):
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        if enable:
            exe_path = f'"{sys.executable}"' if getattr(sys, 'frozen', False) else f'pythonw "{Path(__file__).resolve()}"'
            winreg.SetValueEx(key, "RcloneManager", 0, winreg.REG_SZ, exe_path)
        else:
            try: winreg.DeleteValue(key, "RcloneManager")
            except Exception: pass
        winreg.CloseKey(key)
        return True
    except Exception as e:
        return str(e)

def parse_rclone_conf(conf_path: Path):
    remotes = []
    try:
        config = configparser.ConfigParser()
        config.read(str(conf_path), encoding="utf-8")
        for section in config.sections():
            remotes.append({"name": section, "type": config.get(section, "type", fallback="")})
    except Exception:
        pass
    return remotes

def find_default_rclone_conf():
    if getattr(sys, 'frozen', False):
        current_dir = Path(sys.executable).parent
    else:
        current_dir = Path(__file__).parent
    
    candidates = [
        Path(os.environ.get("APPDATA", "")) / "rclone" / "rclone.conf",
        Path.home() / ".config" / "rclone" / "rclone.conf",
        current_dir / "rclone.conf"
    ]
    for p in candidates:
        if p.exists(): return p
    return None

def download_rclone(dest_dir: Path, version: str, progress_cb=None):
    url = f"https://github.com/rclone/rclone/releases/download/v{version}/rclone-v{version}-windows-amd64.zip"
    try:
        response = requests.get(url, stream=True, timeout=30)
        total_size = int(response.headers.get("content-length", 0))
        downloaded = 0
        temp_zip = tempfile.mktemp(suffix=".zip")
        with open(temp_zip, "wb") as f:
            for chunk in response.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb and total_size:
                    progress_cb(int(downloaded * 100 / total_size))
        
        with zipfile.ZipFile(temp_zip, "r") as z:
            for member in z.namelist():
                if member.endswith("rclone.exe"):
                    with z.open(member) as source, open(dest_dir / "rclone.exe", "wb") as target:
                        target.write(source.read())
                    break
        os.unlink(temp_zip)
        return True
    except Exception as e:
        return str(e)

# --- 설정 관리 ---
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

CONFIG_PATH = BASE_DIR / "mounts.json"

def load_config():
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if "mounts" not in data: data["mounts"] = []
            return data
        except Exception:
            pass
    return {"remotes": [], "mounts": [], "rclone_path": "", "auto_mount": False}

def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def get_rclone_exe(cfg):
    path_str = cfg.get("rclone_path", "").strip()
    if path_str and Path(path_str).exists():
        return Path(path_str)
    return None

# --- 마운트 실행 로직 ---
active_mounts = {}

def build_cmd(exe: Path, mount: dict):
    # SyntaxError 수정: f-string 내부에서 역슬래시 제거
    raw_path = mount.get('remote_path', '').strip()
    clean_path = raw_path.replace('\\', '/').strip('/')
    remote_part = f"{mount['remote']}:{clean_path}"
    
    drive_part = mount.get("drive", "").strip() or " "
    cmd = [str(exe), "mount", remote_part, drive_part, "--volname", mount.get("label") or mount["remote"]]
    
    if mount.get("cache_dir"): cmd += ["--cache-dir", mount["cache_dir"]]
    if mount.get("cache_mode"): cmd += ["--vfs-cache-mode", mount["cache_mode"]]
    
    extra = mount.get("extra_flags", "").strip()
    if extra:
        for flag in re.split(r"[\s;]+|\n", extra):
            if flag.strip(): cmd.append(flag.strip())
    return cmd

def unmount(mid):
    if mid in active_mounts:
        proc = active_mounts[mid]
        proc.terminate()
        try: proc.wait(timeout=3)
        except Exception: proc.kill()
        active_mounts.pop(mid)

def activate_existing_window():
    hwnd = ctypes.windll.user32.FindWindowW(None, "RcloneManager")
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        return True
    return False

# --- UI 다이얼로그 클래스 ---
class ConfImportDialog(tk.Toplevel):
    def __init__(self, parent, remotes):
        super().__init__(parent)
        self.title("리모트 선택")
        self.geometry(f"{scale(350)}x{scale(450)}")
        self.configure(bg="#1e1e2e")
        self.grab_set()
        self.selected = []
        self._remotes = remotes
        self._vars = []
        self._init_ui()

    def _init_ui(self):
        main_f = tk.Frame(self, bg="#1e1e2e")
        main_f.pack(fill="both", expand=True, padx=16, pady=10)
        tk.Label(main_f, text="가져올 리모트 선택:", bg="#1e1e2e", fg="#cba6f7", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 10))
        
        canvas = tk.Canvas(main_f, bg="#1e1e2e", highlightthickness=0)
        scrollbar = ttk.Scrollbar(main_f, orient="vertical", command=canvas.yview)
        scroll_f = tk.Frame(canvas, bg="#1e1e2e")
        
        canvas.create_window((0, 0), window=scroll_f, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        for r in self._remotes:
            v = tk.BooleanVar(value=True)
            self._vars.append((v, r))
            row = tk.Frame(scroll_f, bg="#1e1e2e")
            row.pack(fill="x", pady=2)
            tk.Checkbutton(row, variable=v, bg="#1e1e2e", fg="#cdd6f4", selectcolor="#313244", activebackground="#1e1e2e").pack(side="left")
            tk.Label(row, text=f"{r['name']} [{r['type']}]", bg="#1e1e2e", fg="#cdd6f4").pack(side="left")
            
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        scroll_f.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        
        tk.Button(self, text="가져오기", bg="#cba6f7", fg="#1e1e2e", font=("Segoe UI", 10, "bold"), relief="flat", command=self._ok).pack(pady=10, ipady=3, ipadx=10)

    def _ok(self):
        self.selected = [(r["name"], r["type"]) for v, r in self._vars if v.get()]
        self.destroy()

class UpdateDialog(tk.Toplevel):
    def __init__(self, parent, tag, body, download_url=None):
        super().__init__(parent)
        self.title(f"업데이트 확인 - {tag}")
        self.geometry(f"{scale(550)}x{scale(450)}")
        self.configure(bg="#1e1e2e")
        self.grab_set()
        self.confirmed = False
        self.download_url = download_url
        self._init_ui(tag, body)

    def _init_ui(self, tag, body):
        container = tk.Frame(self, padx=25, pady=20, bg="#1e1e2e")
        container.pack(fill="both", expand=True)
        tk.Label(container, text=f"✨ 새 버전({tag}) 업데이트", bg="#1e1e2e", fg="#cba6f7", font=("Segoe UI", 12, "bold")).pack(pady=(0, 10))
        
        text_f = tk.Frame(container, bg="#1e1e2e")
        text_f.pack(fill="both", expand=True, pady=5)
        self.txt = tk.Text(text_f, bg="#313244", fg="#cdd6f4", relief="flat", font=("Segoe UI", 10), padx=10, pady=10)
        sc = ttk.Scrollbar(text_f, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sc.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sc.pack(side="right", fill="y")
        self.txt.insert("1.0", body)
        self.txt.config(state="disabled")
        
        btn_f = tk.Frame(container, bg="#1e1e2e")
        btn_f.pack(fill="x", side="bottom", pady=(10, 0))
        tk.Button(btn_f, text="업데이트 실행", bg="#a6e3a1", fg="#1e1e2e", font=("Segoe UI", 10, "bold"), relief="flat", command=self._ok, width=15).pack(side="right", padx=5, ipady=5)
        tk.Button(btn_f, text="취소", bg="#45475a", fg="#cdd6f4", font=("Segoe UI", 10, "bold"), relief="flat", command=self.destroy, width=12).pack(side="right", padx=5, ipady=5)

    def _ok(self):
        self.confirmed = True
        self.destroy()

class MountDialog(tk.Toplevel):
    def __init__(self, parent, mount=None, app_cfg=None):
        super().__init__(parent)
        self.title("마운트 설정")
        self.geometry(f"{scale(600)}x{scale(750)}")
        self.configure(bg="#1e1e2e")
        self.grab_set()
        self.result = None
        self._m = mount or {}
        self._app_cfg = app_cfg
        self._build_ui()

    def _build_ui(self):
        container = tk.Frame(self, padx=30, pady=20, bg="#1e1e2e")
        container.pack(fill="both", expand=True)
        
        lbl_style = {"bg": "#1e1e2e", "fg": "#cba6f7", "font": ("Segoe UI", 10, "bold")}
        ent_style = {"bg": "#313244", "fg": "#cdd6f4", "insertbackground": "#cdd6f4", "relief": "flat", "font": ("Segoe UI", 10)}
        
        def add_row(label, var_name, is_text=False, options=None):
            tk.Label(container, text=label, **lbl_style).pack(anchor="w", pady=(5, 0))
            if options:
                cb = ttk.Combobox(container, values=options, font=("Segoe UI", 10), state="readonly")
                cb.pack(fill="x", pady=(2, 10))
                cb.set(self._m.get(var_name, options[0] if var_name=="cache_mode" else ""))
                return cb
            elif is_text:
                t = tk.Text(container, height=4, **ent_style)
                t.pack(fill="x", pady=(2, 10))
                t.insert("1.0", self._m.get(var_name, ""))
                return t
            else:
                f = tk.Frame(container, bg="#1e1e2e")
                f.pack(fill="x", pady=(2, 10))
                e = tk.Entry(f, **ent_style)
                e.pack(side="left", fill="x", expand=True, ipady=3)
                e.insert(0, self._m.get(var_name, ""))
                return e

        self._rem = add_row("리모트 이름", "remote")
        self._pth = add_row("서브 디렉토리 (선택)", "remote_path")
        self._drv = add_row("드라이브 문자", "drive", options=[""] + [f"{chr(i)}:" for i in range(ord('D'), ord('Z')+1)])
        self._lbl = add_row("마운트 라벨 (선택)", "label")
        self._cdir = add_row("캐시 디렉토리 (선택)", "cache_dir")
        self._cmode = add_row("캐시 모드", "cache_mode", options=["off", "minimal", "writes", "full"])
        self._ext = add_row("추가 파라미터 (공백/세미콜론 구분)", "extra_flags", is_text=True)
        
        self._auto = tk.BooleanVar(value=self._m.get("auto_mount", False))
        tk.Checkbutton(container, text="시작 시 자동 마운트", variable=self._auto, bg="#1e1e2e", fg="#cdd6f4", selectcolor="#313244", font=("Segoe UI", 10), activebackground="#1e1e2e").pack(anchor="w", pady=10)
        
        btn_f = tk.Frame(container, bg="#1e1e2e")
        btn_f.pack(fill="x", pady=(10, 0))
        tk.Button(btn_f, text="저장", bg="#cba6f7", fg="#1e1e2e", font=("Segoe UI", 10, "bold"), relief="flat", command=self._save).pack(side="right", padx=5, ipadx=15, ipady=5)
        tk.Button(btn_f, text="취소", bg="#45475a", fg="#cdd6f4", font=("Segoe UI", 10, "bold"), relief="flat", command=self.destroy).pack(side="right", padx=5, ipadx=15, ipady=5)

    def _save(self):
        remote = self._rem.get().strip()
        drive = self._drv.get().strip()
        path = self._pth.get().strip()

        if not remote:
            messagebox.showwarning("오류", "리모트 이름 필수")
            return

        if drive and self._app_cfg:
            for m in self._app_cfg.get("mounts", []):
                if m.get("id") != self._m.get("id") and m.get("drive") == drive:
                    messagebox.showerror("오류", "드라이브 문자 중복")
                    return

        if self._app_cfg:
            for m in self._app_cfg.get("mounts", []):
                if (m.get("id") != self._m.get("id") and 
                    m.get("remote") == remote and 
                    m.get("remote_path", "") == path):
                    messagebox.showerror("오류", "동일한 리모트/경로가 이미 등록되어 있습니다.")
                    return

        self.result = {
            "id": self._m.get("id") or str(uuid.uuid4()),
            "remote": remote,
            "remote_path": path,
            "drive": drive,
            "label": self._lbl.get().strip(),
            "cache_dir": self._cdir.get().strip(),
            "cache_mode": self._cmode.get(),
            "extra_flags": self._ext.get("1.0", "end").strip(),
            "auto_mount": self._auto.get()
        }
        self.destroy()

# --- 메인 애플리케이션 ---
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RcloneManager")
        self.geometry(f"{scale(1100)}x{scale(700)}")
        self.configure(bg="#1e1e2e")
        self.protocol("WM_DELETE_WINDOW", self.hide_window)
        
        self._cfg = load_config()
        self._status = {}
        self._tray = None
        self._latest_rc = ""
        self._latest_app_info = None
        self._version_check_running = False

        self._build_ui()
        self._refresh_list()
        self._start_tray()
        self._init_rc_label()
        
        self.after(500, self._check_versions_async)
        self.bind("<FocusIn>", self._on_focus_in)
        
        if self._cfg.get("auto_mount"):
            self.after(1500, self._automount_all)

    def _init_rc_label(self):
        exe = get_rclone_exe(self._cfg)
        if not exe:
            self._rc_ver_label.config(text="rclone 다운로드", fg="#f38ba8")
        else:
            self._rc_ver_label.config(text="v체크 중...", fg="#94e2d5")

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#1e1e2e")
        style.configure("TLabel", background="#1e1e2e", foreground="#cdd6f4", font=("Segoe UI", 10))
        style.configure("Header.TLabel", font=("Segoe UI", 16, "bold"), foreground="#cba6f7")
        style.configure("Treeview", background="#313244", foreground="#cdd6f4", fieldbackground="#313244", borderwidth=0, rowheight=scale(30))
        style.configure("Treeview.Heading", background="#45475a", foreground="#cba6f7", font=("Segoe UI", 10, "bold"), borderwidth=0)
        style.map("Treeview", background=[('selected', '#585b70')], foreground=[('selected', '#cba6f7')])
        
        header = ttk.Frame(self)
        header.pack(fill="x", padx=20, pady=10)
        
        title_f = ttk.Frame(header)
        title_f.pack(side="left")
        ttk.Label(title_f, text="🚀 RcloneManager", style="Header.TLabel").pack(side="left")
        ttk.Label(title_f, text=f"v{APP_VERSION}", foreground="#fab387", font=("Segoe UI", 10, "bold")).pack(side="left", padx=8)
        
        self._app_up_btn = tk.Button(header, text="✨ 새 버전 업데이트", bg="#a6e3a1", fg="#1e1e2e", font=("Segoe UI", 9, "bold"), relief="flat", command=self._show_app_update_confirm)
        
        rc_f = tk.Frame(self, bg="#1e1e2e")
        rc_f.pack(fill="x", padx=20, pady=5)
        tk.Label(rc_f, text="rclone 경로:", bg="#1e1e2e", fg="#cba6f7", font=("Segoe UI", 10, "bold")).pack(side="left")
        self._rc_var = tk.StringVar(value=self._cfg.get("rclone_path", ""))
        tk.Entry(rc_f, textvariable=self._rc_var, bg="#313244", fg="#cdd6f4", relief="flat", width=50, insertbackground="#cdd6f4").pack(side="left", padx=10, ipady=3)
        tk.Button(rc_f, text="📂", bg="#45475a", fg="#cdd6f4", relief="flat", command=self._browse_rc, width=3).pack(side="left")
        
        self._rc_ver_label = tk.Label(rc_f, text="", bg="#1e1e2e", fg="#94e2d5", font=("Segoe UI", 10), cursor="hand2")
        self._rc_ver_label.pack(side="left", padx=15)
        self._rc_ver_label.bind("<Button-1>", self._handle_rc_click)
        
        opt_f = tk.Frame(self, bg="#1e1e2e")
        opt_f.pack(fill="x", padx=20, pady=5)
        self._st_var = tk.BooleanVar(value=is_startup_enabled())
        ttk.Checkbutton(opt_f, text="시작 시 자동 실행", variable=self._st_var, command=self._toggle_st).pack(side="left", padx=(0, 20))
        self._am_var = tk.BooleanVar(value=self._cfg.get("auto_mount", False))
        ttk.Checkbutton(opt_f, text="시작 시 자동 마운트", variable=self._am_var, command=self._toggle_am).pack(side="left")
        
        cols = ("type", "auto", "drive", "remote", "status")
        self._tree = ttk.Treeview(self, columns=cols, show="headings", height=12)
        for col, head in zip(cols, ("구분", "자동", "드라이브", "리모트 정보", "상태")):
            self._tree.heading(col, text=head)
            self._tree.column(col, width=scale(100), anchor="center")
        self._tree.column("remote", width=scale(400), anchor="w")
        self._tree.pack(fill="both", expand=True, padx=20, pady=5)
        
        btn_f = ttk.Frame(self)
        btn_f.pack(fill="x", padx=20, pady=10)
        tk.Button(btn_f, text="➕ 마운트 추가", bg="#cba6f7", fg="#1e1e2e", font=("Segoe UI", 10, "bold"), relief="flat", command=self._add).pack(side="left", padx=5, ipadx=10, ipady=3)
        tk.Button(btn_f, text="📥 rclone.conf 가져오기", bg="#89b4fa", fg="#1e1e2e", font=("Segoe UI", 10, "bold"), relief="flat", command=self._import_conf).pack(side="left", padx=5, ipadx=10, ipady=3)
        
        tk.Button(btn_f, text="✏️ 수정", bg="#45475a", fg="#cdd6f4", font=("Segoe UI", 10, "bold"), relief="flat", command=self._edit).pack(side="right", padx=5, ipadx=15, ipady=3)
        tk.Button(btn_f, text="❌ 삭제", bg="#f38ba8", fg="#1e1e2e", font=("Segoe UI", 10, "bold"), relief="flat", command=self._delete_mount).pack(side="right", padx=5, ipadx=15, ipady=3)
        tk.Button(btn_f, text="▶️ 마운트 시작", bg="#a6e3a1", fg="#1e1e2e", font=("Segoe UI", 10, "bold"), relief="flat", command=self._mount_single).pack(side="right", padx=5, ipadx=15, ipady=3)
        tk.Button(btn_f, text="⏹ 중단", bg="#fab387", fg="#1e1e2e", font=("Segoe UI", 10, "bold"), relief="flat", command=self._unmount_selected).pack(side="right", padx=5, ipadx=15, ipady=3)
        
        footer = tk.Frame(self, bg="#1e1e2e")
        footer.pack(fill="x", side="bottom", padx=20, pady=5)
        tk.Label(footer, text=get_sys_info(), bg="#1e1e2e", fg="#585b70", font=("Segoe UI", 9)).pack(side="left")
        tk.Label(footer, text="Report Issue", bg="#1e1e2e", fg="#89b4fa", font=("Segoe UI", 9, "underline"), cursor="hand2").pack(side="right")
        footer.winfo_children()[-1].bind("<Button-1>", lambda e: self._open_issue())

    def _delete_mount(self):
        sel = self._tree.selection()
        if not sel: return
        if messagebox.askyesno("삭제", "정말 삭제하시겠습니까?"):
            mid = sel[0]
            self._cfg["mounts"] = [m for m in self._cfg["mounts"] if m.get("id") != mid]
            save_config(self._cfg)
            self._refresh_list()

    def _mount_single(self):
        sel = self._tree.selection()
        if not sel: return
        mid = sel[0]
        m = next((i for i in self._cfg["mounts"] if i["id"] == mid), None)
        if m: self._do_mount(mid, m)

    def _open_issue(self):
        webbrowser.open(f"https://github.com/{GITHUB_REPO}/issues/new")

    def _on_focus_in(self, event):
        if event.widget == self:
            self._check_rclone_presence()

    def _check_rclone_presence(self):
        exe = get_rclone_exe(self._cfg)
        if not exe:
            self._rc_ver_label.config(text="rclone 다운로드", fg="#f38ba8")
        else:
            self._refresh_rc_ver_label()

    def _refresh_rc_ver_label(self):
        exe = get_rclone_exe(self._cfg)
        if not exe: return
        def check():
            try:
                res = subprocess.run([str(exe), "version"], capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
                m = re.search(r"rclone v([\d\.]+)", res.stdout)
                if m:
                    ver = m.group(1)
                    txt = f"v{ver}"
                    if self._latest_rc and self._latest_rc != ver:
                        txt += f" / v{self._latest_rc} 업데이트"
                        self._rc_ver_label.config(text=txt, fg="#fab387")
                    else:
                        self._rc_ver_label.config(text=txt, fg="#94e2d5")
            except Exception:
                self._rc_ver_label.config(text="버전 확인 실패", fg="#f38ba8")
        threading.Thread(target=check, daemon=True).start()

    def _browse_rc(self):
        p = filedialog.askopenfilename(title="rclone.exe 선택", filetypes=[("Executable", "*.exe")])
        if p:
            self._rc_var.set(p)
            self._cfg["rclone_path"] = p
            save_config(self._cfg)
            self._check_rclone_presence()

    def _toggle_st(self):
        res = set_startup(self._st_var.get())
        if res is not True:
            messagebox.showerror("오류", f"시작 프로그램 설정 실패: {res}")

    def _toggle_am(self):
        self._cfg["auto_mount"] = self._am_var.get()
        save_config(self._cfg)

    def _refresh_list(self):
        self._tree.delete(*self._tree.get_children())
        for m in self._cfg.get("mounts", []):
            status = "연결됨" if m["id"] in active_mounts else "대기"
            self._tree.insert("", "end", iid=m["id"], values=(
                "rclone", "Y" if m.get("auto_mount") else "N", m.get("drive", ""),
                f"{m['remote']}:{m.get('remote_path', '')}", status
            ))

    def _add(self):
        d = MountDialog(self, app_cfg=self._cfg)
        self.wait_window(d)
        if d.result:
            self._cfg["mounts"].append(d.result)
            save_config(self._cfg)
            self._refresh_list()

    def _edit(self):
        sel = self._tree.selection()
        if not sel: return
        mid = sel[0]
        m = next((i for i in self._cfg["mounts"] if i["id"] == mid), None)
        if m:
            d = MountDialog(self, mount=m, app_cfg=self._cfg)
            self.wait_window(d)
            if d.result:
                idx = next(i for i, x in enumerate(self._cfg["mounts"]) if x["id"] == mid)
                self._cfg["mounts"][idx] = d.result
                save_config(self._cfg)
                self._refresh_list()

    def _do_mount(self, mid, mount):
        exe = get_rclone_exe(self._cfg)
        if not exe:
            messagebox.showwarning("오류", "rclone.exe 경로를 먼저 설정하십시오.")
            return
        if mid in active_mounts: return
        
        cmd = build_cmd(exe, mount)
        try:
            proc = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            active_mounts[mid] = proc
            self._refresh_list()
            self._update_tray_menu()
        except Exception as e:
            messagebox.showerror("오류", f"마운트 실패: {str(e)}")

    def _unmount_selected(self):
        sel = self._tree.selection()
        if not sel: return
        mid = sel[0]
        unmount(mid)
        self._refresh_list()
        self._update_tray_menu()

    def _automount_all(self):
        for m in self._cfg.get("mounts", []):
            if m.get("auto_mount"):
                self._do_mount(m["id"], m)

    def _import_conf(self):
        path = filedialog.askopenfilename(title="rclone.conf 선택", filetypes=[("Config", "*.conf")])
        if not path: return
        remotes = parse_rclone_conf(Path(path))
        if not remotes:
            messagebox.showwarning("알림", "리모트를 찾을 수 없습니다.")
            return
        d = ConfImportDialog(self, remotes)
        self.wait_window(d)
        if d.selected:
            for name, rtype in d.selected:
                self._cfg["mounts"].append({
                    "id": str(uuid.uuid4()), "remote": name, "remote_path": "",
                    "drive": "", "label": "", "cache_dir": "", "cache_mode": "off",
                    "extra_flags": "", "auto_mount": False
                })
            save_config(self._cfg)
            self._refresh_list()

    def _check_versions_async(self):
        if self._version_check_running: return
        self._version_check_running = True
        def task():
            try:
                # rclone 최신 버전 확인
                res = requests.get("https://api.github.com/repos/rclone/rclone/releases/latest", timeout=10)
                if res.status_code == 200:
                    self._latest_rc = res.json()["tag_name"].lstrip('v')
                    self.after(0, self._check_rclone_presence)
                
                # 앱 최신 버전 확인
                res = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", timeout=10)
                if res.status_code == 200:
                    info = res.json()
                    tag = info["tag_name"].lstrip('v')
                    if tag > APP_VERSION:
                        self._latest_app_info = info
                        self.after(0, lambda: self._app_up_btn.pack(side="right", padx=10))
            except Exception: pass
            finally: self._version_check_running = False
        threading.Thread(target=task, daemon=True).start()

    def _handle_rc_click(self, event):
        txt = self._rc_ver_label.cget("text")
        if txt == "rclone 다운로드":
            if not self._latest_rc:
                messagebox.showinfo("알림", "최신 버전 정보를 가져오는 중입니다. 잠시 후 다시 시도해주세요.")
                return
            if messagebox.askyesno("rclone 다운로드", f"rclone v{self._latest_rc}를 다운로드할까요?"):
                self._download_rc_with_ui(self._latest_rc)
        elif "업데이트" in txt:
            if messagebox.askyesno("rclone 업데이트", f"rclone v{self._latest_rc}로 업데이트할까요?"):
                self._download_rc_with_ui(self._latest_rc)

    def _download_rc_with_ui(self, ver):
        # 다운로드 로직 유지
        pass

    def _show_app_update_confirm(self):
        if not self._latest_app_info: return
        d = UpdateDialog(self, self._latest_app_info["tag_name"], self._latest_app_info["body"])
        self.wait_window(d)
        if d.confirmed:
            webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")

    def _start_tray(self):
        if not pystray: return
        def create_image():
            from PIL import Image, ImageDraw
            img = Image.new('RGB', (64, 64), color='#1e1e2e')
            d = ImageDraw.Draw(img)
            d.rectangle([16, 16, 48, 48], fill='#cba6f7')
            return img
        self._tray = pystray.Icon("RcloneManager", create_image(), "RcloneManager")
        self._update_tray_menu()
        threading.Thread(target=self._tray.run, daemon=True).start()

    def _update_tray_menu(self):
        if not self._tray: return
        import pystray
        items = [pystray.MenuItem("열기", self.show_window, default=True)]
        if active_mounts:
            items.append(pystray.Menu.SEPARATOR)
            for mid, proc in active_mounts.items():
                m = next((i for i in self._cfg["mounts"] if i["id"] == mid), None)
                if m:
                    label = f"⏹ {m['drive']} {m['remote']}"
                    # Tray 메뉴 클릭 시 해제 로직
                    items.append(pystray.MenuItem(label, (lambda m_id=mid: (lambda x: self._unmount_from_tray(m_id)))(mid)))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("종료", self._quit_app))
        self._tray.menu = pystray.Menu(*items)

    def _unmount_from_tray(self, mid):
        unmount(mid)
        self.after(0, self._refresh_list)
        self.after(0, self._update_tray_menu)

    def hide_window(self):
        self.withdraw()

    def show_window(self, icon=None, item=None):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _quit_app(self, icon=None, item=None):
        for mid in list(active_mounts.keys()):
            unmount(mid)
        if self._tray:
            self._tray.stop()
        self.destroy()

if __name__ == "__main__":
    if not activate_existing_window():
        app = App()
        app.mainloop()
