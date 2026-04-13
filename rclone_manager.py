"""
RcloneManager - rclone 마운트 관리 트레이 앱
Windows용 (tkinter + pystray)
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
from pathlib import Path
import ctypes

# ── 1. 고해상도(DPI) 대응 및 단일 인스턴스 활성화 ──
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

def activate_existing_window():
    """이미 실행 중인 창을 찾아 화면 앞으로 가져옵니다."""
    hwnd = ctypes.windll.user32.FindWindowW(None, "RcloneManager")
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
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

CONFIG_FILE  = APP_DIR / "mounts.json"
STARTUP_NAME = "RcloneManager"

# ── 설정 로드/저장 ──
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
        except Exception: 
            pass
    return {"remotes": [], "mounts": [], "rclone_path": "", "auto_mount": False}

def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def get_rclone_exe(cfg):
    custom = cfg.get("rclone_path", "").strip()
    if custom and Path(custom).exists(): 
        return Path(custom)
    return APP_DIR / "rclone.exe"

# ── Windows 시작 프로그램 등록 ──
STARTUP_REG = r"Software\Microsoft\Windows\CurrentVersion\Run"

def is_startup_enabled():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG, 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, STARTUP_NAME)
        winreg.CloseKey(key)
        return True
    except Exception: 
        return False

def set_startup(enable: bool):
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG, 0, winreg.KEY_SET_VALUE)
        if enable:
            exe_cmd = f'"{APP_EXE}"' if getattr(sys, 'frozen', False) else f'pythonw "{Path(__file__).resolve()}"'
            winreg.SetValueEx(key, STARTUP_NAME, 0, winreg.REG_SZ, exe_cmd)
        else:
            try: winreg.DeleteValue(key, STARTUP_NAME)
            except FileNotFoundError: pass
        winreg.CloseKey(key)
        return True
    except Exception as e: 
        return str(e)

# ── rclone 유틸리티 ──
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

def get_local_version(rclone_exe: Path):
    if not rclone_exe.exists(): return None
    try:
        r = subprocess.run([str(rclone_exe), "version"], capture_output=True, text=True, timeout=5)
        m = re.search(r"rclone v([\d.]+)", r.stdout)
        return m.group(1) if m else None
    except Exception: 
        return None

def get_latest_version():
    try:
        r = requests.get("https://api.github.com/repos/rclone/rclone/releases/latest", timeout=10)
        return r.json().get("tag_name", "").lstrip("v")
    except Exception: 
        return None

def download_rclone(dest_dir: Path, version: str, progress_cb=None):
    url = f"https://github.com/rclone/rclone/releases/download/v{version}/rclone-v{version}-windows-amd64.zip"
    try:
        r = requests.get(url, stream=True, timeout=60)
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
                    tmp_exe = dest_dir / "rclone_new.exe"
                    tmp_exe.write_bytes(data)
                    if dest.exists(): dest.unlink()
                    tmp_exe.rename(dest)
                    break
        os.unlink(tmp)
        return True
    except Exception as e: return str(e)

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

def do_mount(m_id, rclone_exe, mount, status_cb=None):
    cmd = build_cmd(rclone_exe, mount)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=0x08000000)
        active_mounts[m_id] = proc
        if status_cb: status_cb(m_id, "mounted")
        proc.wait()
        active_mounts.pop(m_id, None)
        if status_cb: status_cb(m_id, "stopped")
    except Exception:
        active_mounts.pop(m_id, None)
        if status_cb: status_cb(m_id, "stopped")

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
        # RecursionError 방지: _tray 속성을 Tk 초기화 전에 미리 선언
        self._tray = None 
        super().__init__()
        self.title("RcloneManager")
        self.geometry("850x600")
        self.minsize(800, 550)
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self.hide_window)
        
        self._cfg = load_config()
        self._status = {}
        self._build_ui()
        self.update_idletasks()
        self._refresh_list()
        self._start_tray()
        self._check_update_async()
        
        self.deiconify()
        self.lift()
        self.focus_force()

        if self._cfg.get("auto_mount"):
            self.after(1500, self._automount_all)

    def _build_ui(self):
        self.configure(bg="#1e1e2e")
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame", background="#1e1e2e")
        s.configure("TLabel", background="#1e1e2e", foreground="#cdd6f4", font=("Segoe UI", 10))
        s.configure("Header.TLabel", font=("Segoe UI", 13, "bold"), foreground="#cba6f7")
        s.configure("TButton", font=("Segoe UI", 9), padding=4)
        s.configure("TCheckbutton", background="#1e1e2e", foreground="#cdd6f4", font=("Segoe UI", 9))
        s.configure("Treeview", background="#313244", foreground="#cdd6f4", fieldbackground="#313244", rowheight=28, font=("Segoe UI", 10))
        s.configure("Treeview.Heading", background="#45475a", foreground="#cba6f7", font=("Segoe UI", 10, "bold"))
        s.map("Treeview", background=[("selected", "#585b70")])

        hdr = ttk.Frame(self)
        hdr.pack(fill="x", padx=12, pady=5)
        ttk.Label(hdr, text="🚀 RcloneManager", style="Header.TLabel").pack(side="left")
        self._ver_lbl = ttk.Label(hdr, text="", foreground="#a6e3a1")
        self._ver_lbl.pack(side="right")

        rcf = tk.Frame(self, bg="#1e1e2e")
        rcf.pack(fill="x", padx=12, pady=4)
        tk.Label(rcf, text="rclone 경로:", bg="#1e1e2e", fg="#cba6f7", font=("Segoe UI", 9, "bold")).pack(side="left")
        self._rc_var = tk.StringVar(value=self._cfg.get("rclone_path", ""))
        tk.Entry(rcf, textvariable=self._rc_var, bg="#313244", fg="#cdd6f4", insertbackground="#cdd6f4", relief="flat", font=("Segoe UI", 9), highlightthickness=1, highlightbackground="#585b70", highlightcolor="#cba6f7", width=50).pack(side="left", padx=6, ipady=3)
        tk.Button(rcf, text="📂", bg="#45475a", fg="#cdd6f4", relief="flat", command=self._browse_rc).pack(side="left")

        opt = tk.Frame(self, bg="#1e1e2e")
        opt.pack(fill="x", padx=12, pady=6)
        self._st_var = tk.BooleanVar(value=is_startup_enabled())
        ttk.Checkbutton(opt, text="시작 시 자동 실행", variable=self._st_var, command=self._toggle_st).pack(side="left", padx=(0, 20))
        self._am_var = tk.BooleanVar(value=self._cfg.get("auto_mount", False))
        ttk.Checkbutton(opt, text="시작 시 자동 마운트", variable=self._am_var, command=self._toggle_am).pack(side="left")

        cols = ("type", "auto", "drive", "remote", "status")
        self._tree = ttk.Treeview(self, columns=cols, show="headings", height=12, selectmode="browse")
        self._tree.heading("type", text="구분")
        self._tree.heading("auto", text="자동")
        self._tree.heading("drive", text="드라이브")
        self._tree.heading("remote", text="리모트 (서브경로)")
        self._tree.heading("status", text="상태")
        
        self._tree.column("type", width=100, anchor="center")
        self._tree.column("auto", width=60, anchor="center")
        self._tree.column("drive", width=80, anchor="center")
        self._tree.column("remote", width=350)
        self._tree.column("status", width=110, anchor="center")
        
        self._tree.pack(fill="both", expand=True, padx=12, pady=5)
        self._tree.bind("<Double-1>", lambda e: self._toggle_row_auto())

        btn = ttk.Frame(self)
        btn.pack(fill="x", padx=12, pady=5)
        ttk.Button(btn, text="➕ 추가", command=self._add).pack(side="left", padx=1)
        ttk.Button(btn, text="✏️ 편집", command=self._edit).pack(side="left", padx=1)
        ttk.Button(btn, text="🗑️ 삭제", command=self._del).pack(side="left", padx=1)
        ttk.Button(btn, text="🔼", width=3, command=self._move_up).pack(side="left", padx=1)
        ttk.Button(btn, text="🔽", width=3, command=self._move_down).pack(side="left", padx=1)
        ttk.Button(btn, text="📥 conf 가져오기", command=self._import_conf).pack(side="left", padx=4)
        ttk.Button(btn, text="▶ 마운트", command=self._mount_sel).pack(side="left", padx=4)
        ttk.Button(btn, text="■ 언마운트", command=self._unmount_sel).pack(side="left", padx=1)
        ttk.Button(btn, text="🔄 업데이트", command=self._manual_up).pack(side="right", padx=1)

        self._sbar = ttk.Label(self, text="준비", foreground="#a6e3a1")
        self._sbar.pack(fill="x", padx=12, pady=(2, 8))

    def _refresh_list(self):
        for i in self._tree.get_children(): self._tree.delete(i)
        for r in self._cfg.get("remotes", []):
            self._tree.insert("", "end", iid=f"remote_{r['name']}", values=("☁️ 원본", "—", "—", f"[{r['type']}] {r['name']}", "설정 대기"), tags=("remote_tag",))
        for m in self._cfg.get("mounts", []):
            st = self._status.get(m["id"], "stopped")
            auto = "✅" if m.get("auto_mount") else "—"
            tag = "on" if st == "mounted" else "off"
            lbl = "🟢 실행중" if st == "mounted" else "⚫ 중지됨"
            rpath = m.get("remote_path", "").replace("\\", "/").strip("/")
            rem_name = m.get('remote', 'Unknown')
            remote_str = f"{rem_name}:{rpath}" if rpath else rem_name
            self._tree.insert("", "end", iid=m["id"], values=("💾 마운트", auto, m.get("drive","?"), remote_str, lbl), tags=(tag,))
        self._tree.tag_configure("remote_tag", foreground="#8fa0b5")
        self._tree.tag_configure("on", foreground="#a6e3a1")
        self._tree.tag_configure("off", foreground="#cdd6f4")
        # RecursionError 방지: hasattr 대신 명시적 None 체크 사용
        if self._tray is not None:
            try: self._tray.update_menu()
            except: pass

    def _get_m(self, mid):
        for i, m in enumerate(self._cfg["mounts"]):
            if m.get("id") == mid: return i, m
        return None, None

    def _sel_id(self):
        sel = self._tree.selection()
        return sel[0] if sel else None

    def _browse_rc(self):
        p = filedialog.askopenfilename()
        if p:
            self._rc_var.set(p)
            self._cfg["rclone_path"] = p
            save_config(self._cfg)

    def _toggle_st(self): set_startup(self._st_var.get())
    def _toggle_am(self):
        self._cfg["auto_mount"] = self._am_var.get()
        save_config(self._cfg)

    def _automount_all(self):
        for m in self._cfg.get("mounts", []):
            if m.get("auto_mount") and m["id"] not in active_mounts:
                self._do_mount(m["id"], m)

    def _toggle_row_auto(self):
        mid = self._sel_id()
        if not mid: return
        if mid.startswith("remote_"):
            self._add()
            return
        idx, m = self._get_m(mid)
        if m:
            m["auto_mount"] = not m.get("auto_mount", False)
            save_config(self._cfg)
            self._refresh_list()

    def _move_up(self):
        mid = self._sel_id()
        if not mid: return
        if mid.startswith("remote_"):
            idx = next((i for i, r in enumerate(self._cfg.get("remotes", [])) if f"remote_{r['name']}" == mid), None)
            if idx is not None and idx > 0:
                self._cfg["remotes"][idx], self._cfg["remotes"][idx-1] = self._cfg["remotes"][idx-1], self._cfg["remotes"][idx]
                save_config(self._cfg); self._refresh_list(); self._tree.selection_set(mid)
        else:
            idx, m = self._get_m(mid)
            if idx is not None and idx > 0:
                self._cfg["mounts"][idx], self._cfg["mounts"][idx-1] = self._cfg["mounts"][idx-1], self._cfg["mounts"][idx]
                save_config(self._cfg); self._refresh_list(); self._tree.selection_set(mid)

    def _move_down(self):
        mid = self._sel_id()
        if not mid: return
        if mid.startswith("remote_"):
            idx = next((i for i, r in enumerate(self._cfg.get("remotes", [])) if f"remote_{r['name']}" == mid), None)
            if idx is not None and idx < len(self._cfg["remotes"])-1:
                self._cfg["remotes"][idx], self._cfg["remotes"][idx+1] = self._cfg["remotes"][idx+1], self._cfg["remotes"][idx]
                save_config(self._cfg); self._refresh_list(); self._tree.selection_set(mid)
        else:
            idx, m = self._get_m(mid)
            if idx is not None and idx < len(self._cfg["mounts"])-1:
                self._cfg["mounts"][idx], self._cfg["mounts"][idx+1] = self._cfg["mounts"][idx+1], self._cfg["mounts"][idx]
                save_config(self._cfg); self._refresh_list(); self._tree.selection_set(mid)

    def _import_conf(self):
        p = [Path(os.environ.get("APPDATA", "")) / "rclone" / "rclone.conf", Path.home() / ".config" / "rclone" / "rclone.conf", APP_DIR / "rclone.conf"]
        p_exist = next((x for x in p if x.exists()), None)
        path = filedialog.askopenfilename(initialdir=str(p_exist.parent) if p_exist else None)
        if not path: return
        remotes = parse_rclone_conf(Path(path))
        dlg = ConfImportDialog(self, remotes)
        self.wait_window(dlg)
        if dlg.selected:
            exist = [r["name"] for r in self._cfg.get("remotes", [])]
            for r_name, r_type in dlg.selected:
                if r_name not in exist: self._cfg.setdefault("remotes", []).append({"name": r_name, "type": r_type})
            save_config(self._cfg); self._refresh_list()

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
        if mid.startswith("remote_"):
            r_name = mid.split("remote_", 1)[1]
            if messagebox.askyesno("삭제", f"원본 '{r_name}'을 삭제할까요?"):
                self._cfg["remotes"] = [r for r in self._cfg.get("remotes", []) if r["name"] != r_name]
                save_config(self._cfg); self._refresh_list()
            return
        idx, m = self._get_m(mid)
        if messagebox.askyesno("삭제", "마운트를 삭제할까요?"):
            unmount(mid); self._status.pop(mid, None); self._cfg["mounts"].pop(idx)
            save_config(self._cfg); self._refresh_list()

    def _do_mount(self, mid, m):
        exe = get_rclone_exe(self._cfg)
        if not exe.exists(): return
        def cb(i, s):
            self._status[i] = s
            self.after(0, self._refresh_list)
        self._status[mid] = "mounted"
        self._refresh_list()
        threading.Thread(target=do_mount, args=(mid, exe, m, cb), daemon=True).start()

    def _mount_sel(self):
        mid = self._sel_id()
        if not mid or mid.startswith("remote_") or mid in active_mounts: return
        idx, m = self._get_m(mid)
        if m: self._do_mount(mid, m)

    def _unmount_sel(self):
        mid = self._sel_id()
        if not mid or mid.startswith("remote_"): return
        unmount(mid); self._status[mid] = "stopped"; self._refresh_list()

    def _check_update_async(self):
        def _c():
            exe = get_rclone_exe(self._cfg)
            l = get_latest_version()
            self.after(0, lambda: self._ver_lbl.config(text=f"v{get_local_version(exe) or '없음'} / 최신 v{l or '?'}"))
        threading.Thread(target=_c, daemon=True).start()

    def _manual_up(self):
        def _do():
            exe = get_rclone_exe(self._cfg)
            l = get_latest_version()
            if not l: return
            if get_local_version(exe) == l: 
                messagebox.showinfo("알림", f"이미 최신 버전(v{l})입니다.")
                return
            self.after(0, lambda: self._sbar.config(text=f"rclone v{l} 다운로드 중..."))
            res = download_rclone(exe.parent if self._cfg.get("rclone_path") else APP_DIR, l, lambda p: self.after(0, lambda: self._sbar.config(text=f"다운로드 중... {p}%")))
            if res is True: 
                self.after(0, lambda: self._sbar.config(text="업데이트 완료."))
                self.after(0, self._check_update_async)
            else: self.after(0, lambda: self._sbar.config(text=f"실패: {res}"))
        threading.Thread(target=_do, daemon=True).start()

    def _start_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
            img = Image.new("RGBA", (64,64), (0,0,0,0)); d = ImageDraw.Draw(img)
            d.ellipse([2,2,62,62], fill="#cba6f7"); d.text((14,20), "RC", fill="#1e1e2e")
            def on_show(icon, item): self.after(0, self.show_window)
            def on_quit(icon, item): self.after(0, self._quit_app)
            def on_toggle_mount(m):
                mid = m["id"]
                if mid in active_mounts: unmount(mid); self._status[mid] = "stopped"
                else: self._do_mount(mid, m)
                self.after(0, self._refresh_list)
            def get_menu_items():
                items = [pystray.MenuItem("📂 창 열기", on_show, default=True), pystray.Menu.SEPARATOR]
                for m in self._cfg.get("mounts", []):
                    is_active = self._status.get(m["id"]) == "mounted"
                    label = f"{'■' if is_active else '▶'} {m.get('remote', 'Unknown')} ({m.get('drive','')})"
                    items.append(pystray.MenuItem(label, (lambda m_obj: lambda icon, item: on_toggle_mount(m_obj))(m)))
                items += [pystray.Menu.SEPARATOR, pystray.MenuItem("❌ 종료", on_quit)]
                return items
            self._tray = pystray.Icon("RcloneManager", img, "RcloneManager", menu=pystray.Menu(get_menu_items))
            threading.Thread(target=self._tray.run, daemon=True).start()
        except Exception: pass

    def hide_window(self): self.withdraw()
    def show_window(self): self.deiconify(); self.lift(); self.focus_force()
    def _quit_app(self):
        for mid in list(active_mounts.keys()): unmount(mid)
        try: self._tray.stop()
        except Exception: pass
        self.destroy()

class ConfImportDialog(tk.Toplevel):
    def __init__(self, parent, remotes):
        super().__init__(parent); self.title("리모트 선택"); self.grab_set(); self.configure(bg="#1e1e2e"); self.selected = []; self._remotes = remotes; self._vars = []
        tk.Label(self, text="가져올 리모트 선택:", bg="#1e1e2e", fg="#cba6f7", font=("Segoe UI", 10, "bold")).pack(padx=16, pady=10, anchor="w")
        for r in self._remotes:
            v = tk.BooleanVar(value=True); self._vars.append((v, r))
            row = tk.Frame(self, bg="#1e1e2e"); row.pack(fill="x", padx=16, pady=2)
            tk.Checkbutton(row, variable=v, bg="#1e1e2e", fg="#cdd6f4", selectcolor="#313244").pack(side="left")
            tk.Label(row, text=f"{r['name']} [{r['type']}]", bg="#1e1e2e", fg="#cdd6f4").pack(side="left")
        tk.Button(self, text="가져오기", bg="#cba6f7", command=self._ok).pack(pady=10)
    def _ok(self): self.selected = [(r["name"], r["type"]) for v, r in self._vars if v.get()]; self.destroy()

class MountDialog(tk.Toplevel):
    CACHE_MODES = ["off", "minimal", "writes", "full"]
    DRIVES = [""] + [f"{c}:" for c in "DEFGHIJKLMNOPQRSTUVWXYZ"]
    FORBIDDEN_FLAGS = ["--volname", "--cache-dir", "--vfs-cache-mode"]

    def __init__(self, parent, mount=None, app_cfg=None):
        super().__init__(parent); self.title("마운트 편집" if mount and "id" in mount else "마운트 추가")
        self.resizable(True, True); self.minsize(600, 700); self.grab_set(); self.configure(bg="#1e1e2e")
        self.result = None; self._m = mount or {}; self._app_cfg = app_cfg; self._build()
        self.geometry("700x850")

    def _build(self):
        BG, FG, HL, EBG = "#1e1e2e", "#cdd6f4", "#cba6f7", "#313244"
        c = tk.Frame(self, bg=BG); c.pack(fill="both", expand=True, padx=20, pady=10)
        def lbl(t): tk.Label(c, text=t, bg=BG, fg=HL, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(5, 1))
        
        lbl("리모트 이름 (rclone.conf의 [이름])")
        self._remote = tk.StringVar(value=self._m.get("remote", ""))
        tk.Entry(c, textvariable=self._remote, bg=EBG, fg=FG, insertbackground=FG, relief="flat", font=("Segoe UI", 10), highlightthickness=1, highlightbackground="#585b70", highlightcolor=HL).pack(fill="x", ipady=3)
        
        lbl("서브 디렉토리 (예: sub/folder — 비워두면 루트 전체)")
        pf = tk.Frame(c, bg=BG); pf.pack(fill="x")
        self._rpath = tk.StringVar(value=self._m.get("remote_path", ""))
        tk.Entry(pf, textvariable=self._rpath, bg=EBG, fg=FG, insertbackground=FG, relief="flat", font=("Segoe UI", 10), highlightthickness=1, highlightbackground="#585b70", highlightcolor=HL).pack(side="left", fill="x", expand=True, ipady=3)
        tk.Button(pf, text="연결 테스트", bg="#89b4fa", fg="#1e1e2e", font=("Segoe UI", 8, "bold"), command=self._test).pack(side="left", padx=(5, 0), ipady=1)
        
        lbl("드라이브 문자")
        self._drive = tk.StringVar(value=self._m.get("drive", ""))
        ttk.Combobox(c, textvariable=self._drive, values=self.DRIVES, state="readonly", width=10).pack(anchor="w")
        
        lbl("캐시 디렉토리 (--cache-dir)")
        cf = tk.Frame(c, bg=BG); cf.pack(fill="x")
        self._cdir = tk.StringVar(value=self._m.get("cache_dir", ""))
        tk.Entry(cf, textvariable=self._cdir, bg=EBG, fg=FG, insertbackground=FG, relief="flat", font=("Segoe UI", 10), highlightthickness=1, highlightbackground="#585b70", highlightcolor=HL).pack(side="left", fill="x", expand=True, ipady=3)
        tk.Button(cf, text="📂", bg="#45475a", fg=FG, command=self._browse).pack(side="left", padx=(5, 0))
        
        lbl("캐시 모드 (--vfs-cache-mode)")
        self._cmode = tk.StringVar(value=self._m.get("cache_mode", "full"))
        ttk.Combobox(c, textvariable=self._cmode, values=self.CACHE_MODES, state="readonly", width=12).pack(anchor="w")
        
        lbl("추가 플래그 (; 또는 줄바꿈으로 구분)")
        self._extra_text = tk.Text(c, bg=EBG, fg=FG, insertbackground=FG, relief="flat", font=("Segoe UI", 9), highlightthickness=1, highlightbackground="#585b70", highlightcolor=HL, wrap="word", height=4)
        self._extra_text.pack(fill="both", expand=True, pady=2)
        self._extra_text.insert("1.0", self._m.get("extra_flags", ""))
        
        self._auto = tk.BooleanVar(value=self._m.get("auto_mount", False))
        tk.Checkbutton(c, text="시작 시 자동 마운트", variable=self._auto, bg=BG, fg=FG, selectcolor=EBG, font=("Segoe UI", 9)).pack(anchor="w", pady=(5, 0))
        
        bf = tk.Frame(c, bg=BG); bf.pack(fill="x", side="bottom", pady=(10, 0))
        tk.Button(bf, text="저장", bg=HL, fg="#1e1e2e", font=("Segoe UI", 10, "bold"), width=12, command=self._save).pack(side="right", padx=5)
        tk.Button(bf, text="취소", bg="#45475a", fg=FG, width=12, command=self.destroy).pack(side="right", padx=5)

    def _test(self):
        t = f"{self._remote.get().strip()}:{self._rpath.get().strip().strip('/')}"
        exe = get_rclone_exe(self._app_cfg)
        def r():
            try:
                p = subprocess.run([str(exe), "lsf", t, "--max-depth", "1"], capture_output=True, text=True, timeout=10, creationflags=0x08000000)
                if p.returncode == 0: messagebox.showinfo("성공", "연결 확인 완료!")
                else: messagebox.showerror("실패", f"연결 불가:\n{p.stderr.strip()}")
            except Exception as e: messagebox.showerror("오류", str(e))
        threading.Thread(target=r, daemon=True).start()

    def _browse(self):
        d = filedialog.askdirectory()
        if d: self._cdir.set(d)

    def _save(self):
        rem = self._remote.get().strip()
        drv = self._drive.get().strip()
        path = self._rpath.get().strip().strip("/")
        if not rem: return messagebox.showwarning("오류", "리모트 이름 필수")
        for m in self._app_cfg.get("mounts", []):
            if m.get("id") == self._m.get("id"): continue
            if drv and m.get("drive") == drv: return messagebox.showerror("오류", f"드라이브 문자 {drv}가 이미 등록되어 있습니다.")
            if m.get("remote") == rem and m.get("remote_path", "").strip("/") == path:
                return messagebox.showerror("오류", f"동일한 마운트({rem}:{path})가 이미 존재합니다.")
        
        extra_val = self._extra_text.get("1.0", tk.END).strip()
        # TypeError 방지: extra_val이 문자열인지 강제 확인
        if not isinstance(extra_val, str): extra_val = ""
        
        clean_flags = []
        for f in re.split(r"[\s;]+", extra_val):
            f = f.strip()
            if not f: continue
            if any(f.startswith(forbidden) for forbidden in self.FORBIDDEN_FLAGS):
                return messagebox.showerror("오류", f"금지된 플래그가 포함되어 있습니다: {f}")
            if not f.startswith("-"): f = "--" + f
            clean_flags.append(f)
        self.result = {
            "remote": rem, "remote_path": path, "drive": drv,
            "cache_dir": self._cdir.get().strip(), "cache_mode": self._cmode.get(),
            "extra_flags": " ".join(clean_flags), "auto_mount": self._auto.get()
        }
        self.destroy()

if __name__ == "__main__":
    if activate_existing_window(): sys.exit(0)
    mutex_name = "RcloneManager_SingleInstance_Mutex"
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    if ctypes.windll.kernel32.GetLastError() == 183: sys.exit(0)
    app = App(); app.mainloop()
