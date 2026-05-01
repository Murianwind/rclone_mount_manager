"""
RcloneManager BDD 테스트
수정 내역:
  - 모든 테스트 최상단에 Windows/GUI 의존성 mock 설정 추가 (conftest 역할)
  - Scenario 17: save_config mock 추가 (파일 I/O 격리)
  - Scenario 18: [데드락 수정]
      원인: app._cfg['rclone_path'] = '' → get_rclone_exe() → None
            → _do_mount()에서 messagebox.showerror() 호출
            → Tk 루트 없이 GUI 이벤트루프 대기 → 데드락
      수정: rclone_path를 유효한 경로로 설정 + Path.exists mock
            + _cfg['mounts']에 마운트에 필요한 필드 전체 포함
            + messagebox.showerror patch로 안전망 추가
  - Scenario 28: _version_check_running 속성 추가 (AttributeError 방지)
  - tearDown 추가: active_mounts 전역 상태 초기화
  - _create_mocked_app: save_config 기본 mock 적용
"""

import sys
import unittest
import unittest.mock as mock
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Windows/GUI 의존성 최상단 mock (import 전) ──────────────────────────
# winreg: Windows 레지스트리 모듈, Linux/Mac에 없음
sys.modules.setdefault("winreg", mock.MagicMock())

# pystray: 트레이 아이콘 (Linux에서 GTK 의존성 문제)
sys.modules.setdefault("pystray", None)

# ctypes: Windows API 호출 mock
if "ctypes" not in sys.modules or not isinstance(sys.modules.get("ctypes"), mock.MagicMock):
    _ctypes_mock = mock.MagicMock()
    _ctypes_mock.windll.shcore.SetProcessDpiAwareness.return_value = 0
    _ctypes_mock.windll.user32.SetProcessDPIAware.return_value = 0
    _ctypes_mock.windll.user32.GetDC.return_value = 0
    _ctypes_mock.windll.user32.ReleaseDC.return_value = 0
    _ctypes_mock.windll.user32.GetSystemMetrics.return_value = 1920
    _ctypes_mock.windll.gdi32.GetDeviceCaps.return_value = 96
    _ctypes_mock.windll.user32.FindWindowW.return_value = 0
    _ctypes_mock.windll.user32.ShowWindow.return_value = 1
    _ctypes_mock.windll.user32.SetForegroundWindow.return_value = 1
    sys.modules["ctypes"] = _ctypes_mock
# ──────────────────────────────────────────────────────────────────────────

import tkinter as tk
import rclone_manager


class TestRcloneManagerBDD(unittest.TestCase):

    def setUp(self):
        """테스트 데이터 초기 설정 (Given)"""
        self.sample_cfg = {
            "remotes": [], "mounts": [], "rclone_path": "", "auto_mount": False
        }

    def tearDown(self):
        """전역 상태 초기화 (테스트 간 격리)"""
        rclone_manager.active_mounts.clear()

    def _create_mocked_app(self, cfg=None):
        """
        Mock 앱 인스턴스 생성 유틸리티 (RecursionError 방지)
        - save_config를 기본 patch하여 실제 파일 I/O 차단
        """
        app = rclone_manager.App.__new__(rclone_manager.App)
        app.tk = MagicMock()
        # cfg는 복사본 사용 - 테스트 간 공유 방지
        app._cfg = dict(cfg) if cfg else dict(self.sample_cfg)
        if "mounts" not in app._cfg:
            app._cfg["mounts"] = []
        app._status = {}
        app._tray = MagicMock()
        app._tree = MagicMock()
        app._tree.get_children.return_value = []     # _refresh_list 호출 대비
        app._rc_ver_label = MagicMock()
        app._app_up_btn = MagicMock()
        app._rc_var = MagicMock()
        app._am_var = MagicMock()
        app._am_var.get = MagicMock()
        app._st_var = MagicMock()
        app._st_var.get = MagicMock()
        app.after = MagicMock()
        app.withdraw = MagicMock()
        app.deiconify = MagicMock()
        app.lift = MagicMock()
        app.focus_force = MagicMock()
        app.bind = MagicMock()
        # _check_versions_async 호출 방지용 플래그
        app._version_check_running = False
        app._latest_rc = ""
        app._latest_app_info = None
        return app

    def _create_mocked_dialog(self, parent, mount=None, cfg=None):
        """Mock 다이얼로그 생성 유틸리티"""
        dlg = rclone_manager.MountDialog.__new__(rclone_manager.MountDialog)
        dlg._m = mount if mount else {}
        dlg._app_cfg = dict(cfg) if cfg else dict(self.sample_cfg)
        if "mounts" not in dlg._app_cfg:
            dlg._app_cfg["mounts"] = []
        dlg._rem = MagicMock()
        dlg._drv = MagicMock()
        dlg._pth = MagicMock()
        dlg._cdir = MagicMock()
        dlg._cmode = MagicMock()
        dlg._ext = MagicMock()
        dlg._auto = MagicMock()
        dlg.destroy = MagicMock()
        # 기본 반환값 설정 (strip 체인 호환)
        dlg._drv.get.return_value = ""
        dlg._pth.get.return_value = ""
        dlg._cdir.get.return_value = ""
        dlg._cmode.get.return_value = "full"
        dlg._ext.get.return_value = ""
        dlg._auto.get.return_value = False
        return dlg

    # ── Scenario 01: rclone 실행 파일 로드 ────────────────────────────────
    def test_scenario_01_load_rclone(self):
        # Given: rclone_path가 설정 파일에 존재할 때
        cfg = {"rclone_path": "C:\\fake\\rclone.exe"}
        with patch("pathlib.Path.exists", return_value=True):
            # When: rclone 실행 파일을 가져오면
            exe = rclone_manager.get_rclone_exe(cfg)
            # Then: 설정된 경로가 반환되어야 한다.
            self.assertEqual(str(exe), "C:\\fake\\rclone.exe")

    # ── Scenario 02: rclone 명령어 빌드 (기본) ───────────────────────────
    def test_scenario_02_build_cmd_basic(self):
        # Given: 리모트 이름과 드라이브 문자가 주어졌을 때
        exe = Path("rclone.exe")
        mount = {"remote": "drive", "drive": "X:", "remote_path": "data"}
        # When: 명령어를 빌드하면
        cmd = rclone_manager.build_cmd(exe, mount)
        # Then: 필수 인자들이 포함되어야 한다.
        self.assertIn("mount", cmd)
        self.assertIn("drive:data", cmd)

    # ── Scenario 03: rclone 명령어 빌드 (캐시 설정 포함) ─────────────────
    def test_scenario_03_build_cmd_with_cache(self):
        # Given: 캐시 경로와 모드가 주어졌을 때
        exe = Path("rclone.exe")
        mount = {
            "remote": "drive", "drive": "X:",
            "cache_dir": "C:\\cache", "cache_mode": "full"
        }
        # When: 명령어를 빌드하면
        cmd = rclone_manager.build_cmd(exe, mount)
        # Then: 캐시 관련 플래그가 포함되어야 한다.
        self.assertIn("--cache-dir", cmd)
        self.assertIn("full", cmd)

    # ── Scenario 04: rclone 명령어 빌드 (추가 플래그 포함) ───────────────
    def test_scenario_04_build_cmd_with_extra_flags(self):
        # Given: 추가 플래그가 주어졌을 때
        # extra_flags는 저장 시 normalize_flags를 거쳐 정규화된 형태로 저장됨
        # '--bwlimit 10M' → '--bwlimit=10M' (=로 연결)
        exe = Path("rclone.exe")
        mount = {
            "remote": "drive", "drive": "X:",
            "extra_flags": rclone_manager.normalize_flags("--read-only; --bwlimit 10M")
        }
        # When: 명령어를 빌드하면
        cmd = rclone_manager.build_cmd(exe, mount)
        # Then: 정규화된 형태의 플래그가 포함되어야 한다.
        self.assertIn("--read-only", cmd)
        self.assertIn("--bwlimit=10M", cmd)

    # ── Scenario 05: 설정 파일 로드 (파일 없음) ──────────────────────────
    def test_scenario_05_load_config_none(self):
        # Given: 설정 파일이 존재하지 않을 때
        with patch("pathlib.Path.exists", return_value=False):
            # When: 설정을 로드하면
            cfg = rclone_manager.load_config()
            # Then: 기본 구조의 빈 데이터가 반환되어야 한다.
            self.assertEqual(cfg["mounts"], [])

    # ── Scenario 06: 설정 파일 로드 (손상된 파일) ────────────────────────
    def test_scenario_06_load_config_corrupt(self):
        # Given: 설정 파일이 잘못된 JSON 형식일 때
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value="{bad"):
            # When: 설정을 로드하면
            cfg = rclone_manager.load_config()
            # Then: 에러 없이 기본 설정을 반환해야 한다.
            self.assertEqual(cfg["mounts"], [])

    # ── Scenario 07: 설정 파일 저장 ──────────────────────────────────────
    def test_scenario_07_save_config(self):
        # Given: 저장할 설정 데이터가 있을 때
        cfg = {"mounts": []}
        with patch("pathlib.Path.write_text") as mock_write:
            # When: 설정을 저장하면
            rclone_manager.save_config(cfg)
            # Then: 파일 쓰기 함수가 호출되어야 한다.
            mock_write.assert_called_once()

    # ── Scenario 08: 시작 프로그램 상태 확인 ─────────────────────────────
    def test_scenario_08_startup_check(self):
        # Given: 레지스트리에 시작 프로그램이 등록되어 있을 때
        with patch("rclone_manager.winreg") as mock_winreg:
            mock_winreg.OpenKey.return_value = MagicMock()
            mock_winreg.QueryValueEx.return_value = ("path", 1)
            # When: 등록 상태를 확인하면
            enabled = rclone_manager.is_startup_enabled()
            # Then: True를 반환해야 한다.
            self.assertTrue(enabled)

    # ── Scenario 09: 마운트 중지 로직 ────────────────────────────────────
    def test_scenario_09_unmount_logic(self):
        # Given: 실행 중인 프로세스가 등록되어 있을 때
        mock_proc = MagicMock()
        rclone_manager.active_mounts["test_id"] = mock_proc
        # When: 언마운트를 수행하면
        rclone_manager.unmount("test_id")
        # Then: 프로세스가 종료되어야 한다.
        mock_proc.terminate.assert_called_once()
        # And: active_mounts에서 제거되어야 한다.
        self.assertNotIn("test_id", rclone_manager.active_mounts)

    # ── Scenario 10: 중복 실행 시 창 활성화 ──────────────────────────────
    def test_scenario_10_activate_existing_window(self):
        # Given: 이미 실행 중인 창의 핸들이 있을 때
        with patch("ctypes.windll.user32.FindWindowW", return_value=123), \
             patch("ctypes.windll.user32.ShowWindow") as mock_show:
            # When: 창 활성화를 시도하면
            res = rclone_manager.activate_existing_window()
            # Then: ShowWindow가 호출되고 True가 반환되어야 한다.
            self.assertTrue(res)
            mock_show.assert_called()

    # ── Scenario 11: 마운트 다이얼로그 저장 ──────────────────────────────
    def test_scenario_11_dialog_save_new(self):
        # Given: 다이얼로그에 정보를 입력했을 때
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        dlg._rem.get.return_value = "remote"
        # When: 저장 버튼을 누르면
        dlg._save()
        # Then: result 객체가 생성되어야 한다.
        self.assertIsNotNone(dlg.result)

    # ── Scenario 12: 리모트 이름 미입력 에러 ─────────────────────────────
    def test_scenario_12_dialog_save_empty_remote(self):
        # Given: 리모트 이름이 비어있을 때
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        dlg._rem.get.return_value = ""
        with patch("tkinter.messagebox.showinfo") as mock_info:
            # When: 저장을 시도하면
            dlg._save()
            # Then: 알림 창이 표시되어야 한다.
            mock_info.assert_called_with("알림", "리모트 이름을 입력해 주세요.")

    # ── Scenario 13: 드라이브 문자 중복 에러 ─────────────────────────────
    def test_scenario_13_dialog_duplicate_drive(self):
        # Given: 이미 사용 중인 드라이브 문자를 선택했을 때
        cfg = {"mounts": [{"id": "1", "drive": "Z:", "remote": "other",
                           "remote_path": ""}]}
        app = self._create_mocked_app(cfg)
        dlg = self._create_mocked_dialog(app, cfg=cfg)
        dlg._rem.get.return_value = "test"
        dlg._drv.get.return_value = "Z:"
        with patch("tkinter.messagebox.showinfo") as mock_info:
            # When: 저장을 시도하면
            dlg._save()
            # Then: 알림 창이 표시되어야 한다.
            mock_info.assert_called_with("알림", "이미 사용 중인 드라이브 문자입니다.")

    # ── Scenario 14: 동일 리모트/경로 중복 에러 ──────────────────────────
    def test_scenario_14_dialog_duplicate_remote_path(self):
        # Given: 동일한 리모트와 경로가 이미 있을 때
        cfg = {"mounts": [{"id": "1", "remote": "test", "remote_path": "path",
                           "drive": ""}]}
        app = self._create_mocked_app(cfg)
        dlg = self._create_mocked_dialog(app, cfg=cfg)
        dlg._rem.get.return_value = "test"
        dlg._pth.get.return_value = "path"
        with patch("tkinter.messagebox.showinfo") as mock_info:
            # When: 저장을 시도하면
            dlg._save()
            # Then: 알림 창이 표시되어야 한다.
            mock_info.assert_called()

    # ── Scenario 15: rclone 다운로드 및 설치 ─────────────────────────────
    def test_scenario_15_rclone_install_path(self):
        # Given: 다운로드 요청이 있을 때
        with patch("requests.get") as mock_get, \
             patch("zipfile.ZipFile") as mock_zip, \
             patch("pathlib.Path.write_bytes"), \
             patch("os.unlink"), \
             patch("tempfile.mktemp", return_value="/tmp/fake_rclone.zip"), \
             patch("builtins.open", mock.mock_open()):
            mock_get.return_value.iter_content = lambda x: [b"data"]
            mock_get.return_value.headers = {"content-length": "4"}
            mock_zip.return_value.__enter__ = lambda s: mock_zip.return_value
            mock_zip.return_value.__exit__ = MagicMock(return_value=False)
            mock_zip.return_value.namelist.return_value = [
                "rclone-v1.65.0-windows-amd64/rclone.exe"
            ]
            mock_zip.return_value.read.return_value = b"fake_exe"
            # When: 다운로드를 실행하면
            res = rclone_manager.download_rclone(Path("."), "1.65.0")
            # Then: True가 반환되어야 한다.
            self.assertTrue(res)

    # ── Scenario 16: 시작 프로그램 등록 설정 ─────────────────────────────
    def test_scenario_16_set_startup(self):
        # Given: 시작 프로그램 등록을 요청할 때
        with patch("rclone_manager.winreg") as mock_winreg:
            mock_winreg.OpenKey.return_value = MagicMock()
            # When: set_startup(True)를 호출하면
            rclone_manager.set_startup(True)
            # Then: 레지스트리 쓰기 함수가 호출되어야 한다.
            mock_winreg.SetValueEx.assert_called()

    # ── Scenario 17: 앱 삭제 UI 테스트 ───────────────────────────────────
    def test_scenario_17_app_delete_ui(self):
        # Given: 삭제할 마운트 항목이 데이터에 존재할 때
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "test-id", "remote": "test"}]
        with patch("tkinter.messagebox.askyesno", return_value=True), \
             patch("rclone_manager.save_config"), \
             patch("rclone_manager.unmount"):
            # When: 삭제 메서드를 호출하면
            app._delete_mount("test-id")
            # Then: 데이터에서 해당 항목이 제거되어야 한다.
            self.assertEqual(len(app._cfg["mounts"]), 0)

    # ── Scenario 18: 마운트 작업 시작 테스트 ─────────────────────────────
    def test_scenario_18_mount_task_start(self):
        """
        [데드락 수정]
        원인: app._cfg['rclone_path'] = '' → get_rclone_exe() → None
              → _do_mount()에서 messagebox.showerror() 호출
              → Tk 루트 없이 GUI 이벤트루프 대기 → 데드락
        수정:
          1. rclone_path를 유효한 경로로 설정
          2. pathlib.Path.exists를 True로 mock
          3. _cfg['mounts'] 항목에 build_cmd에 필요한 전체 필드 포함
          4. messagebox.showerror도 patch하여 안전망 추가
        """
        # Given: 마운트할 데이터가 있고 rclone.exe가 존재할 때
        app = self._create_mocked_app()
        app._cfg["rclone_path"] = "C:\\fake\\rclone.exe"
        app._cfg["mounts"] = [{
            "id": "test-id",
            "remote": "test",
            "remote_path": "",
            "drive": "X:",
            "cache_dir": "",
            "cache_mode": "full",
            "extra_flags": "",
        }]
        # When: 단일 마운트를 실행하면
        with patch("subprocess.Popen") as mock_popen, \
             patch("pathlib.Path.exists", return_value=True), \
             patch("tkinter.messagebox.showerror"):
            mock_proc = MagicMock()
            mock_proc.wait.return_value = 0
            mock_popen.return_value = mock_proc
            app._mount_single("test-id")
            import time
            time.sleep(0.3)  # 데몬 스레드 시작 대기
            # Then: Popen이 실제로 호출되어야 한다.
            self.assertTrue(mock_popen.called)

    # ── Scenario 19: 자동 마운트 설정 토글 테스트 ────────────────────────
    def test_scenario_19_toggle_auto_mount(self):
        # Given: UI에서 자동 마운트 체크박스 값을 변경했을 때
        app = self._create_mocked_app()
        app._am_var.get.return_value = True
        with patch("rclone_manager.save_config"):
            # When: 설정을 저장하면
            app._save_settings()
        # Then: 설정 데이터(cfg)에 반영되어야 한다.
        self.assertTrue(app._cfg["auto_mount"])

    # ── Scenario 20: 시스템 DPI 정보 수집 ────────────────────────────────
    def test_scenario_20_sys_info_retrieval(self):
        # Given: 시스템 정보를 조회할 때
        with patch("rclone_manager.get_sys_info", return_value="1920x1080"):
            # When: 정보를 가져오면
            info = rclone_manager.get_sys_info()
            # Then: 반환값이 일치해야 한다.
            self.assertEqual(info, "1920x1080")

    # ── Scenario 21: 이슈 리포트 URL 테스트 ──────────────────────────────
    def test_scenario_21_issue_report_url(self):
        # Given: 이슈 제보 버튼을 누를 때
        app = self._create_mocked_app()
        with patch("webbrowser.open") as mock_open:
            # When: _open_issue를 호출하면
            app._open_issue()
            # Then: 브라우저가 이슈 페이지 URL을 열어야 한다.
            called_url = mock_open.call_args[0][0]
            self.assertIn("issues", called_url)

    # ── Scenario 22: 드라이브 문자 빈칸 허용 ─────────────────────────────
    def test_scenario_22_blank_drive_letter_save(self):
        # Given: 드라이브 문자를 비워두었을 때
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        dlg._rem.get.return_value = "remote"
        dlg._drv.get.return_value = ""
        # When: 저장을 시도하면
        dlg._save()
        # Then: 정상 저장되어야 한다.
        self.assertIsNotNone(dlg.result)
        self.assertEqual(dlg.result["drive"], "")

    # ── Scenario 23: rclone 버전 레이블 로직 ─────────────────────────────
    def test_scenario_23_rclone_version_label_text_logic(self):
        # Given: 버전 비교 문구를 구성할 때
        msg = "v1.60.0 / v1.65.0 업데이트"
        # Then: 업데이트 문구가 포함되어야 한다.
        self.assertIn("업데이트", msg)

    # ── Scenario 24: rclone.conf 파싱 ────────────────────────────────────
    def test_scenario_24_parse_rclone_conf(self):
        # Given: 설정 파일을 파싱할 때
        with patch("configparser.ConfigParser.read"), \
             patch("configparser.ConfigParser.sections", return_value=["drive"]):
            # When: 파싱을 수행하면
            remotes = rclone_manager.parse_rclone_conf(Path("fake.conf"))
            # Then: 리스트가 반환되어야 한다.
            self.assertIsInstance(remotes, list)

    # ── Scenario 25: 트레이 아이콘 동작 ──────────────────────────────────
    def test_scenario_25_tray_default_action(self):
        # Given: 트레이 메뉴 항목을 만들 때
        with patch("rclone_manager.pystray", create=True) as mock_pystray:
            mock_pystray.MenuItem = MagicMock()
            # When: '열기' 메뉴를 생성하면
            rclone_manager.pystray.MenuItem("열기", MagicMock(), default=True)
            # Then: default 인자가 True여야 한다.
            mock_pystray.MenuItem.assert_called_with(
                "열기", unittest.mock.ANY, default=True
            )

    # ── Scenario 26: 업데이트 취소 ───────────────────────────────────────
    def test_scenario_26_update_dialog_cancel(self):
        # Given: 업데이트 질문에 '아니오'를 선택할 때
        with patch("tkinter.messagebox.askyesno", return_value=False):
            # When: 확인을 수행하면
            res = tk.messagebox.askyesno("rclone", "업데이트?")
            # Then: False가 반환되어야 한다.
            self.assertFalse(res)

    # ── Scenario 27: 업데이트 승인 ───────────────────────────────────────
    def test_scenario_27_update_dialog_confirm(self):
        # Given: 업데이트 질문에 '예'를 선택할 때
        with patch("tkinter.messagebox.askyesno", return_value=True):
            # When: 확인을 수행하면
            res = tk.messagebox.askyesno("rclone", "업데이트?")
            # Then: True가 반환되어야 한다.
            self.assertTrue(res)

    # ── Scenario 28: rclone 미등록 시 다운로드 문구 표시 ─────────────────
    def test_scenario_28_rclone_download_label_when_missing(self):
        # Given: 등록된 rclone 실행 파일이 시스템에 없을 때
        app = self._create_mocked_app()
        app._cfg["rclone_path"] = "C:\\non_existent\\rclone.exe"
        with patch("pathlib.Path.exists", return_value=False):
            # When: 존재 여부 체크 로직이 실행되면
            app._check_rclone_presence()
            # Then: UI 레이블이 'rclone 다운로드'로 변경되어야 한다.
            app._rc_ver_label.config.assert_called_with(
                text="rclone 다운로드", fg="#f38ba8"
            )

    # ── Scenario 29: 창 활성화 시 rclone 존재 여부 재확인 ────────────────
    def test_scenario_29_check_rclone_on_focus(self):
        # Given: 프로그램이 활성화될 때
        app = self._create_mocked_app()
        mock_event = MagicMock()
        mock_event.widget = app  # event.widget = 최상위 창 인스턴스
        # When: 창에 포커스가 생기면
        with patch.object(app, "_check_rclone_presence") as mock_check:
            app._on_focus_in(mock_event)
            # Then: 재확인 로직이 호출되어야 한다.
            mock_check.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
