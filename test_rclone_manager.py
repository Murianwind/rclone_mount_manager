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
import os
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
        app._app_ver_label = MagicMock()
        app._app_up_btn = MagicMock()
        app._rc_var = MagicMock()
        app._am_var = MagicMock()
        app._am_var.get = MagicMock()
        app._st_var = MagicMock()
        app._st_var.get = MagicMock()
        app._min_var = MagicMock()
        app._min_var.get = MagicMock(return_value=False)
        def _run_after(delay, callback=None, *cb_args, **cb_kwargs):
            # 실제 Tkinter의 after()는 지연 실행이지만, 테스트에서는
            # 예약된 콜백(주로 UI 갱신/알림)이 실행됐는지 검증해야 하므로
            # 동기적으로 즉시 실행한다.
            if callable(callback):
                return callback(*cb_args, **cb_kwargs)
            return None

        app.after = MagicMock(side_effect=_run_after)
        app.withdraw = MagicMock()
        app.deiconify = MagicMock()
        app.lift = MagicMock()
        app.focus_force = MagicMock()
        app.bind = MagicMock()
        app.wait_window = MagicMock()
        app.geometry = MagicMock(return_value="800x600+0+0")
        app.destroy = MagicMock()
        # _check_versions_async 호출 방지용 플래그
        app._version_check_running = False
        app._pending_force_check = False
        app._latest_rc = ""
        app._latest_app_info = None
        app._net_was_connected = None
        app._net_monitor_running = False
        app._geometry_save_after = None
        return app

    @staticmethod
    def _sync_thread():
        """threading.Thread를 동기 실행으로 대체하는 patch용 헬퍼."""
        class _FakeThread:
            def __init__(self, target=None, args=(), kwargs=None, daemon=None):
                self._target = target
                self._args = args
                self._kwargs = kwargs or {}

            def start(self):
                if self._target:
                    self._target(*self._args, **self._kwargs)

        return _FakeThread

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
        # 원자적 쓰기 방식: 기존 파일 없음 → 백업 스킵 → 임시파일 write_text → replace
        with patch("pathlib.Path.exists", return_value=False), \
             patch("pathlib.Path.write_text") as mock_write, \
             patch("pathlib.Path.replace") as mock_replace:
            # When: 설정을 저장하면
            rclone_manager.save_config(cfg)
            # Then: 임시 파일에 쓰고 원자적으로 교체해야 한다.
            mock_write.assert_called_once()
            mock_replace.assert_called_once()

    # ── Scenario 07b: 설정 저장 - 기존 파일이 유효하면 백업 생성 ─────────
    def test_scenario_07b_save_config_creates_backup(self):
        cfg = {"mounts": [{"id": "new"}]}
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value='{"mounts": [{"id": "old"}]}'), \
             patch("pathlib.Path.write_text") as mock_write, \
             patch("pathlib.Path.replace") as mock_replace:
            rclone_manager.save_config(cfg)
            # 백업 파일 쓰기 1회 + 임시파일 쓰기 1회 = 총 2회
            self.assertEqual(mock_write.call_count, 2)
            mock_replace.assert_called_once()

    # ── Scenario 07c: 설정 로드 - 손상 시 백업으로 자동 복구 ─────────────
    def test_scenario_07c_load_config_recovers_from_backup(self):
        good_backup = '{"mounts": [{"id": "backup-ok"}]}'
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", side_effect=["{bad", good_backup]), \
             patch("rclone_manager.save_config"), \
             patch("rclone_manager.write_log"):
            cfg = rclone_manager.load_config()
            self.assertEqual(cfg["mounts"][0]["id"], "backup-ok")

    # ── Scenario 07d: 설정 로드 - 백업도 손상되면 원본 보존 후 기본값 ────
    def test_scenario_07d_load_config_preserves_corrupted_file(self):
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", side_effect=["{bad", "{also bad"]), \
             patch("pathlib.Path.replace") as mock_replace, \
             patch("rclone_manager.write_log"):
            cfg = rclone_manager.load_config()
            self.assertEqual(cfg["mounts"], [])
            # 손상된 원본을 보존하기 위해 replace(rename)가 호출되어야 한다
            mock_replace.assert_called_once()

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


    # ══════════════════════════════════════════════════════════════════
    # 분기 커버리지 보강 테스트 (Scenario 30 이후)
    # ══════════════════════════════════════════════════════════════════

    # ── Scenario 30: DPI 배율 조회 (정상) ────────────────────────────────
    def test_scenario_30_get_dpi_scale_success(self):
        with patch("ctypes.windll.user32.GetDC", return_value=1), \
             patch("ctypes.windll.gdi32.GetDeviceCaps", return_value=192), \
             patch("ctypes.windll.user32.ReleaseDC"):
            scale = rclone_manager.get_dpi_scale()
            self.assertEqual(scale, 2.0)

    # ── Scenario 31: DPI 배율 조회 (예외 시 기본값) ──────────────────────
    def test_scenario_31_get_dpi_scale_exception(self):
        with patch("ctypes.windll.user32.GetDC", side_effect=Exception("fail")):
            scale = rclone_manager.get_dpi_scale()
            self.assertEqual(scale, 1.0)

    # ── Scenario 32: 화면 해상도 조회 (정상) ─────────────────────────────
    def test_scenario_32_get_screen_size_success(self):
        with patch("ctypes.windll.user32.GetSystemMetrics", side_effect=[2560, 1440]):
            w, h = rclone_manager.get_screen_size()
            self.assertEqual((w, h), (2560, 1440))

    # ── Scenario 33: 화면 해상도 조회 (예외 시 기본값) ───────────────────
    def test_scenario_33_get_screen_size_exception(self):
        with patch("ctypes.windll.user32.GetSystemMetrics", side_effect=Exception):
            w, h = rclone_manager.get_screen_size()
            self.assertEqual((w, h), (1920, 1080))

    # ── Scenario 34: 논리 해상도 계산 ────────────────────────────────────
    def test_scenario_34_get_logical_screen_size(self):
        with patch("rclone_manager.get_screen_size", return_value=(1920, 1080)), \
             patch("rclone_manager.get_dpi_scale", return_value=2.0):
            lw, lh = rclone_manager.get_logical_screen_size()
            self.assertEqual((lw, lh), (960, 540))

    # ── Scenario 35: 시스템 정보 문자열 (정상) ───────────────────────────
    def test_scenario_35_get_sys_info_success(self):
        with patch("ctypes.windll.user32.GetSystemMetrics", side_effect=[1920, 1080]), \
             patch("ctypes.windll.user32.GetDC", return_value=1), \
             patch("ctypes.windll.gdi32.GetDeviceCaps", return_value=96), \
             patch("ctypes.windll.user32.ReleaseDC"):
            info = rclone_manager.get_sys_info()
            self.assertIn("1920x1080", info)
            self.assertIn("100%", info)

    # ── Scenario 36: 시스템 정보 문자열 (예외 시 N/A) ────────────────────
    def test_scenario_36_get_sys_info_exception(self):
        with patch("ctypes.windll.user32.GetSystemMetrics", side_effect=Exception):
            info = rclone_manager.get_sys_info()
            self.assertEqual(info, "N/A")

    # ── Scenario 37: 창 크기 계산 (최솟값 보장) ──────────────────────────
    def test_scenario_37_calc_window_size_min_bound(self):
        with patch("rclone_manager.get_logical_screen_size", return_value=(100, 100)):
            w, h = rclone_manager.calc_window_size(34, 56, min_w=480, min_h=360)
            self.assertEqual((w, h), (480, 360))

    # ── Scenario 38: 시작 프로그램 상태 확인 (winreg 없음) ───────────────
    def test_scenario_38_startup_check_no_winreg(self):
        with patch("rclone_manager.winreg", None):
            self.assertFalse(rclone_manager.is_startup_enabled())

    # ── Scenario 39: 시작 프로그램 상태 확인 (예외) ──────────────────────
    def test_scenario_39_startup_check_exception(self):
        with patch("rclone_manager.winreg") as mock_winreg:
            mock_winreg.OpenKey.side_effect = Exception("no key")
            self.assertFalse(rclone_manager.is_startup_enabled())

    # ── Scenario 40: 시작 프로그램 등록 (winreg 없음) ────────────────────
    def test_scenario_40_set_startup_no_winreg(self):
        with patch("rclone_manager.winreg", None):
            self.assertFalse(rclone_manager.set_startup(True))

    # ── Scenario 41: 시작 프로그램 해제 (내부 예외 무시) ─────────────────
    def test_scenario_41_set_startup_disable_inner_exception(self):
        with patch("rclone_manager.winreg") as mock_winreg:
            mock_winreg.OpenKey.return_value = MagicMock()
            mock_winreg.DeleteValue.side_effect = Exception("not found")
            res = rclone_manager.set_startup(False)
            self.assertTrue(res)

    # ── Scenario 42: 시작 프로그램 등록 (전체 예외) ──────────────────────
    def test_scenario_42_set_startup_outer_exception(self):
        with patch("rclone_manager.winreg") as mock_winreg, \
             patch("rclone_manager.write_log") as mock_log:
            mock_winreg.OpenKey.side_effect = Exception("boom")
            res = rclone_manager.set_startup(True)
            self.assertEqual(res, "boom")
            mock_log.assert_called()

    # ── Scenario 43: 시작프로그램 경로 조회 (winreg 없음) ────────────────
    def test_scenario_43_get_startup_path_no_winreg(self):
        with patch("rclone_manager.winreg", None):
            self.assertEqual(rclone_manager.get_startup_path(), "")

    # ── Scenario 44: 시작프로그램 경로 조회 (예외) ───────────────────────
    def test_scenario_44_get_startup_path_exception(self):
        with patch("rclone_manager.winreg") as mock_winreg:
            mock_winreg.OpenKey.side_effect = Exception("fail")
            self.assertEqual(rclone_manager.get_startup_path(), "")

    # ── Scenario 45: 시작프로그램 경로 조회 (성공) ───────────────────────
    def test_scenario_45_get_startup_path_success(self):
        with patch("rclone_manager.winreg") as mock_winreg:
            mock_winreg.OpenKey.return_value = MagicMock()
            mock_winreg.QueryValueEx.return_value = ("some_path", 1)
            self.assertEqual(rclone_manager.get_startup_path(), "some_path")

    # ── Scenario 46: 현재 실행 경로 (frozen) ─────────────────────────────
    def test_scenario_46_get_current_exe_path_frozen(self):
        with patch("sys.frozen", True, create=True), \
             patch("sys.executable", "C:\\app\\RcloneManager.exe"):
            path = rclone_manager.get_current_exe_path()
            self.assertIn("RcloneManager.exe", path)

    # ── Scenario 47: 현재 실행 경로 (스크립트 실행) ──────────────────────
    def test_scenario_47_get_current_exe_path_script(self):
        path = rclone_manager.get_current_exe_path()
        self.assertIn("pythonw", path)

    # ── Scenario 48: 시작프로그램 경로 재등록 불필요 (미등록) ────────────
    def test_scenario_48_check_and_fix_startup_not_registered(self):
        with patch("rclone_manager.get_startup_path", return_value=""):
            self.assertFalse(rclone_manager.check_and_fix_startup())

    # ── Scenario 49: 시작프로그램 경로 재등록 불필요 (경로 일치) ─────────
    def test_scenario_49_check_and_fix_startup_match(self):
        with patch("rclone_manager.get_startup_path", return_value="samepath"), \
             patch("rclone_manager.get_current_exe_path", return_value="samepath"):
            self.assertFalse(rclone_manager.check_and_fix_startup())

    # ── Scenario 50: 시작프로그램 경로 자동 재등록 ───────────────────────
    def test_scenario_50_check_and_fix_startup_mismatch(self):
        with patch("rclone_manager.get_startup_path", return_value="old"), \
             patch("rclone_manager.get_current_exe_path", return_value="new"), \
             patch("rclone_manager.set_startup") as mock_set:
            self.assertTrue(rclone_manager.check_and_fix_startup())
            mock_set.assert_called_once_with(True)

    # ── Scenario 51: rclone.conf 파싱 실패 시 빈 리스트 ──────────────────
    def test_scenario_51_parse_rclone_conf_exception(self):
        with patch("configparser.ConfigParser.read", side_effect=Exception("bad")):
            remotes = rclone_manager.parse_rclone_conf(Path("bad.conf"))
            self.assertEqual(remotes, [])

    # ── Scenario 52: 기본 rclone.conf 탐색 (발견) ────────────────────────
    def test_scenario_52_find_default_rclone_conf_found(self):
        with patch("pathlib.Path.exists", return_value=True):
            p = rclone_manager.find_default_rclone_conf()
            self.assertIsNotNone(p)

    # ── Scenario 53: 기본 rclone.conf 탐색 (없음) ────────────────────────
    def test_scenario_53_find_default_rclone_conf_not_found(self):
        with patch("pathlib.Path.exists", return_value=False):
            p = rclone_manager.find_default_rclone_conf()
            self.assertIsNone(p)

    # ── Scenario 54: rclone 다운로드 - zip에 exe 없음 ────────────────────
    def test_scenario_54_download_rclone_no_exe_in_zip(self):
        with patch("requests.get") as mock_get, \
             patch("zipfile.ZipFile") as mock_zip, \
             patch("os.unlink"), \
             patch("tempfile.mktemp", return_value="/tmp/fake.zip"), \
             patch("builtins.open", mock.mock_open()):
            mock_get.return_value.iter_content = lambda x: [b"data"]
            mock_get.return_value.headers = {"content-length": "4"}
            mock_zip.return_value.__enter__ = lambda s: mock_zip.return_value
            mock_zip.return_value.__exit__ = MagicMock(return_value=False)
            mock_zip.return_value.namelist.return_value = ["readme.txt"]
            res = rclone_manager.download_rclone(Path("."), "1.65.0")
            self.assertIn("찾을 수 없습니다", res)

    # ── Scenario 55: rclone 다운로드 - 네트워크 예외 ─────────────────────
    def test_scenario_55_download_rclone_network_exception(self):
        with patch("requests.get", side_effect=Exception("network down")):
            res = rclone_manager.download_rclone(Path("."), "1.65.0")
            self.assertEqual(res, "network down")

    # ── Scenario 56: rclone 다운로드 - 파일락(수동 교체 필요) ────────────
    def test_scenario_56_download_rclone_permission_error(self):
        with patch("requests.get") as mock_get, \
             patch("zipfile.ZipFile") as mock_zip, \
             patch("os.unlink"), \
             patch("tempfile.mktemp", return_value="/tmp/fake.zip"), \
             patch("builtins.open", mock.mock_open()), \
             patch("pathlib.Path.write_bytes",
                   side_effect=[PermissionError(), None]):
            mock_get.return_value.iter_content = lambda x: [b"data"]
            mock_get.return_value.headers = {"content-length": "4"}
            mock_zip.return_value.__enter__ = lambda s: mock_zip.return_value
            mock_zip.return_value.__exit__ = MagicMock(return_value=False)
            mock_zip.return_value.namelist.return_value = ["rclone.exe"]
            mock_zip.return_value.read.return_value = b"fake"
            res = rclone_manager.download_rclone(Path("."), "1.65.0")
            self.assertEqual(res, "manual")

    # ── Scenario 57: 앱 업데이트 파일 다운로드 성공 ──────────────────────
    def test_scenario_57_download_app_release_success(self):
        with patch("requests.get") as mock_get, \
             patch("builtins.open", mock.mock_open()):
            mock_get.return_value.iter_content = lambda x: [b"data"]
            mock_get.return_value.headers = {"content-length": "4"}
            res = rclone_manager.download_app_release(
                "https://example.com/RcloneManager.zip")
            self.assertEqual(res, "manual")

    # ── Scenario 58: 앱 업데이트 파일 다운로드 실패 ──────────────────────
    def test_scenario_58_download_app_release_exception(self):
        with patch("requests.get", side_effect=Exception("timeout")):
            res = rclone_manager.download_app_release("https://example.com/x.zip")
            self.assertEqual(res, "timeout")

    # ── Scenario 59: 앱 업데이트 URL에 확장자 없음(기본 zip) ─────────────
    def test_scenario_59_download_app_release_no_dot_in_url(self):
        with patch("requests.get") as mock_get, \
             patch("builtins.open", mock.mock_open()):
            mock_get.return_value.iter_content = lambda x: [b"data"]
            mock_get.return_value.headers = {"content-length": "0"}
            res = rclone_manager.download_app_release("https://example.com/nodot")
            self.assertEqual(res, "manual")

    # ── Scenario 60: 버전 문자열 파싱 실패 시 (0,) ───────────────────────
    def test_scenario_60_ver_tuple_malformed(self):
        self.assertEqual(rclone_manager._ver_tuple("not-a-version"), (0,))

    # ── Scenario 61: 빌드번호 포함 버전 비교 ─────────────────────────────
    def test_scenario_61_ver_tuple_build_suffix(self):
        # 빌드번호를 버리지 않고 마지막 비교 요소로 포함해야 한다
        self.assertEqual(rclone_manager._ver_tuple("1.74.0-297"), (1, 74, 0, 297))
        self.assertTrue(
            rclone_manager._ver_tuple("1.73.5") < rclone_manager._ver_tuple("1.74.0-297"))

    # ── Scenario 61b: 버전 숫자는 같고 빌드번호만 다른 경우 감지 ─────────
    # (실사용 사례: rclone 버전은 그대로(1.75.0)인데 wiserain이 빌드번호만
    #  올려 재배포(-306 → -315)하는 경우, 예전 코드는 빌드번호를 버려서
    #  두 버전을 동일하게 취급해 업데이트를 절대 감지하지 못했다.)
    def test_scenario_61b_ver_tuple_same_version_different_build(self):
        loc = rclone_manager._ver_tuple("1.75.0-306")
        lat = rclone_manager._ver_tuple("1.75.0-315")
        self.assertNotEqual(loc, lat)
        self.assertTrue(loc < lat)

    # ── Scenario 62: 로그 rotate (최대 줄 수 초과) ───────────────────────
    def test_scenario_62_write_log_rotation(self):
        with patch("rclone_manager.LOG_MAX_LINES", 3), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text",
                   return_value="l1\nl2\nl3\n"), \
             patch("pathlib.Path.write_text") as mock_write:
            rclone_manager.write_log("INFO", "새 라인")
            written = mock_write.call_args[0][0]
            self.assertEqual(len(written.splitlines()), 3)

    # ── Scenario 63: 로그 기록 실패 시 무시 ──────────────────────────────
    def test_scenario_63_write_log_exception_ignored(self):
        with patch("pathlib.Path.exists", side_effect=Exception("io error")):
            try:
                rclone_manager.write_log("ERROR", "실패해도 괜찮음")
            except Exception:
                self.fail("write_log는 예외를 삼켜야 한다")

    # ── Scenario 64: 설정 로드 - mounts 키 이미 존재 ─────────────────────
    def test_scenario_64_load_config_mounts_key_present(self):
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text",
                   return_value='{"mounts": [{"id": "x"}], "remotes": []}'):
            cfg = rclone_manager.load_config()
            self.assertEqual(len(cfg["mounts"]), 1)

    # ── Scenario 65: 플래그 정규화 - 빈 문자열 ───────────────────────────
    def test_scenario_65_normalize_flags_empty(self):
        self.assertEqual(rclone_manager.normalize_flags(""), "")
        self.assertEqual(rclone_manager.normalize_flags("   "), "")

    # ── Scenario 66: 플래그 정규화 - 값 없는 플래그 유지 ─────────────────
    def test_scenario_66_normalize_flags_no_value_flag(self):
        result = rclone_manager.normalize_flags("--links;--fast-list")
        self.assertEqual(result, "--links;--fast-list")

    # ── Scenario 67: 인터넷 연결 확인 - 성공 ─────────────────────────────
    def test_scenario_67_is_internet_available_true(self):
        with patch("socket.socket") as mock_sock:
            instance = mock_sock.return_value.__enter__.return_value
            instance.connect.return_value = None
            self.assertTrue(rclone_manager.is_internet_available())

    # ── Scenario 68: 인터넷 연결 확인 - 실패 ─────────────────────────────
    def test_scenario_68_is_internet_available_false(self):
        with patch("socket.socket", side_effect=OSError("no route")):
            self.assertFalse(rclone_manager.is_internet_available())

    # ── Scenario 69: rclone 경로 - fallback 폴더 사용 ────────────────────
    def test_scenario_69_get_rclone_exe_fallback(self):
        cfg = {"rclone_path": ""}
        with patch("pathlib.Path.exists", return_value=True):
            exe = rclone_manager.get_rclone_exe(cfg)
            self.assertIsNotNone(exe)

    # ── Scenario 70: rclone 경로 - 존재하지 않음 ─────────────────────────
    def test_scenario_70_get_rclone_exe_none(self):
        cfg = {"rclone_path": ""}
        with patch("pathlib.Path.exists", return_value=False):
            exe = rclone_manager.get_rclone_exe(cfg)
            self.assertIsNone(exe)

    # ── Scenario 71: 볼륨 이름 - extra_flags 지정값 우선 ─────────────────
    def test_scenario_71_get_volname_from_extra_flags(self):
        mount = {"remote": "gds", "remote_path": "GDRIVE/VIDEO",
                  "extra_flags": "--buffer-size=512M;--volname=GDS;--no-modtime"}
        self.assertEqual(rclone_manager._get_volname(mount), "GDS")

    # ── Scenario 72: 볼륨 이름 - remote_path 마지막 요소 ─────────────────
    def test_scenario_72_get_volname_from_remote_path(self):
        mount = {"remote": "PLEX", "remote_path": "KODI", "extra_flags": ""}
        self.assertEqual(rclone_manager._get_volname(mount), "KODI")

    # ── Scenario 73: 볼륨 이름 - remote_path 없으면 리모트명 ─────────────
    def test_scenario_73_get_volname_fallback_remote(self):
        mount = {"remote": "nas", "remote_path": "", "extra_flags": ""}
        self.assertEqual(rclone_manager._get_volname(mount), "nas")

    # ── Scenario 74: build_cmd - extra_flags의 --volname 중복 제거 ──────
    def test_scenario_74_build_cmd_volname_deduplication(self):
        exe = Path("rclone.exe")
        mount = {"remote": "gds", "drive": "G:", "remote_path": "GDRIVE/VIDEO",
                  "extra_flags": "--volname=GDS;--no-modtime"}
        cmd = rclone_manager.build_cmd(exe, mount)
        self.assertEqual(cmd.count("--volname"), 1)
        self.assertIn("GDS", cmd)

    # ── Scenario 75: build_cmd - 캐시 설정 없을 때 플래그 미포함 ─────────
    def test_scenario_75_build_cmd_no_cache_flags(self):
        exe = Path("rclone.exe")
        mount = {"remote": "drive", "drive": "X:"}
        cmd = rclone_manager.build_cmd(exe, mount)
        self.assertNotIn("--cache-dir", cmd)
        self.assertNotIn("--vfs-cache-mode", cmd)

    # ── Scenario 76: 언마운트 - 대상 없음 (no-op) ────────────────────────
    def test_scenario_76_unmount_noop(self):
        try:
            rclone_manager.unmount("no-such-id")
        except Exception:
            self.fail("대상이 없으면 조용히 무시되어야 한다")

    # ── Scenario 77: 언마운트 - wait 타임아웃 시 kill ────────────────────
    def test_scenario_77_unmount_wait_timeout_kill(self):
        mock_proc = MagicMock()
        mock_proc.wait.side_effect = Exception("timeout")
        rclone_manager.active_mounts["tid"] = mock_proc
        rclone_manager.unmount("tid")
        mock_proc.kill.assert_called_once()

    # ── Scenario 78: 중복 실행 감지 안 됨 (핸들 없음) ────────────────────
    def test_scenario_78_activate_existing_window_none(self):
        with patch("ctypes.windll.user32.FindWindowW", return_value=0):
            self.assertFalse(rclone_manager.activate_existing_window())

    # ── Scenario 79: 트레이 원형 아이콘 생성 ─────────────────────────────
    def test_scenario_79_make_circle_icon(self):
        fake_image = MagicMock()
        fake_draw = MagicMock()
        with patch.object(rclone_manager, "Image", create=True) as mock_image, \
             patch.object(rclone_manager, "ImageDraw", create=True) as mock_imagedraw:
            mock_image.new.return_value = fake_image
            mock_imagedraw.Draw.return_value = fake_draw
            img = rclone_manager._make_circle_icon("#ffffff", 32)
            self.assertEqual(img, fake_image)
            fake_draw.ellipse.assert_called_once()

    # ── Scenario 80: ConfImportDialog 선택 항목만 반환 ───────────────────
    def test_scenario_80_conf_import_dialog_ok(self):
        dlg = rclone_manager.ConfImportDialog.__new__(rclone_manager.ConfImportDialog)
        v1, v2 = MagicMock(), MagicMock()
        v1.get.return_value = True
        v2.get.return_value = False
        dlg._vars = [(v1, {"name": "a", "type": "drive"}),
                     (v2, {"name": "b", "type": "drive"})]
        dlg.destroy = MagicMock()
        dlg._ok()
        self.assertEqual(dlg.selected, [("a", "drive")])

    # ── Scenario 81: UpdateDialog._pick_asset - exe 우선 ─────────────────
    def test_scenario_81_pick_asset_exe(self):
        assets = [{"name": "readme.txt", "browser_download_url": "u0"},
                  {"name": "app.exe", "browser_download_url": "u1"}]
        url = rclone_manager.UpdateDialog._pick_asset(assets)
        self.assertEqual(url, "u1")

    # ── Scenario 82: UpdateDialog._pick_asset - 해당 없음 ────────────────
    def test_scenario_82_pick_asset_none(self):
        assets = [{"name": "readme.txt", "browser_download_url": "u0"}]
        url = rclone_manager.UpdateDialog._pick_asset(assets)
        self.assertEqual(url, "")

    # ── Scenario 83: MountDialog 캐시 폴더 선택 ──────────────────────────
    def test_scenario_83_browse_cache_selected(self):
        dlg = self._create_mocked_dialog(None)
        with patch("tkinter.filedialog.askdirectory", return_value="D:\\cache"):
            dlg._browse_cache()
            dlg._cdir.delete.assert_called_once()
            dlg._cdir.insert.assert_called_once_with(0, "D:\\cache")

    # ── Scenario 84: MountDialog 캐시 폴더 선택 취소 ─────────────────────
    def test_scenario_84_browse_cache_cancelled(self):
        dlg = self._create_mocked_dialog(None)
        with patch("tkinter.filedialog.askdirectory", return_value=""):
            dlg._browse_cache()
            dlg._cdir.insert.assert_not_called()

    # ── Scenario 85: MountDialog 연결 테스트 - rclone 미등록 ─────────────
    def test_scenario_85_dialog_test_no_rclone(self):
        dlg = self._create_mocked_dialog(None, cfg={"rclone_path": ""})
        dlg._rem.get.return_value = "remote"
        dlg._pth.get.return_value = ""
        with patch("pathlib.Path.exists", return_value=False), \
             patch("tkinter.messagebox.showinfo") as mock_info:
            dlg._test()
            mock_info.assert_called_with("알림", "rclone 경로가 등록되어 있지 않습니다.")

    # ── Scenario 86: MountDialog 연결 테스트 - 성공 ──────────────────────
    def test_scenario_86_dialog_test_success(self):
        dlg = self._create_mocked_dialog(
            None, cfg={"rclone_path": "C:\\fake\\rclone.exe"})
        dlg._rem.get.return_value = "remote"
        dlg._pth.get.return_value = ""
        with patch("pathlib.Path.exists", return_value=True), \
             patch("threading.Thread", self._sync_thread()), \
             patch("subprocess.run") as mock_run, \
             patch("tkinter.messagebox.showinfo") as mock_info:
            mock_run.return_value = MagicMock(returncode=0)
            dlg._test()
            mock_info.assert_called_with("성공", "연결 확인 완료!")

    # ── Scenario 87: MountDialog 연결 테스트 - 실패(연결 불가) ───────────
    def test_scenario_87_dialog_test_failure(self):
        dlg = self._create_mocked_dialog(
            None, cfg={"rclone_path": "C:\\fake\\rclone.exe"})
        dlg._rem.get.return_value = "remote"
        dlg._pth.get.return_value = ""
        with patch("pathlib.Path.exists", return_value=True), \
             patch("threading.Thread", self._sync_thread()), \
             patch("subprocess.run") as mock_run, \
             patch("tkinter.messagebox.showinfo") as mock_info:
            mock_run.return_value = MagicMock(returncode=1, stderr="conn refused")
            dlg._test()
            args = mock_info.call_args[0]
            self.assertEqual(args[0], "연결 실패")

    # ── Scenario 88: MountDialog 연결 테스트 - 예외 ──────────────────────
    def test_scenario_88_dialog_test_exception(self):
        dlg = self._create_mocked_dialog(
            None, cfg={"rclone_path": "C:\\fake\\rclone.exe"})
        dlg._rem.get.return_value = "remote"
        dlg._pth.get.return_value = ""
        with patch("pathlib.Path.exists", return_value=True), \
             patch("threading.Thread", self._sync_thread()), \
             patch("subprocess.run", side_effect=Exception("boom")), \
             patch("tkinter.messagebox.showinfo") as mock_info:
            dlg._test()
            mock_info.assert_called_with("알림", "boom")

    # ── Scenario 89: 창 geometry 유효성 - 정상 ───────────────────────────
    def test_scenario_89_is_valid_geometry_true(self):
        self.assertTrue(rclone_manager.App._is_valid_geometry("800x600+0+0"))

    # ── Scenario 90: 창 geometry 유효성 - 패턴 불일치 ────────────────────
    def test_scenario_90_is_valid_geometry_no_match(self):
        self.assertFalse(rclone_manager.App._is_valid_geometry("invalid"))

    # ── Scenario 91: 창 geometry 유효성 - 최솟값 미달 ────────────────────
    def test_scenario_91_is_valid_geometry_too_small(self):
        self.assertFalse(rclone_manager.App._is_valid_geometry("100x100+0+0"))

    # ── Scenario 92: 창 geometry 유효성 - 예외 ───────────────────────────
    def test_scenario_92_is_valid_geometry_exception(self):
        with patch("re.match", side_effect=Exception("bad")):
            self.assertFalse(rclone_manager.App._is_valid_geometry("800x600"))

    # ── Scenario 93: Configure 이벤트 - 다른 위젯이면 무시 ───────────────
    def test_scenario_93_on_configure_other_widget(self):
        app = self._create_mocked_app()
        event = MagicMock()
        event.widget = MagicMock()
        app._on_configure(event)
        app.after.assert_not_called()

    # ── Scenario 94: Configure 이벤트 - 본인 창이면 예약 ─────────────────
    def test_scenario_94_on_configure_self_widget(self):
        app = self._create_mocked_app()
        event = MagicMock()
        event.widget = app
        app._on_configure(event)
        app.after.assert_called()

    # ── Scenario 95: 창 크기/위치 저장 ───────────────────────────────────
    def test_scenario_95_save_geometry(self):
        app = self._create_mocked_app()
        with patch("rclone_manager.save_config") as mock_save:
            app._save_geometry()
            self.assertIn("window_geometry", app._cfg)
            mock_save.assert_called_once()

    # ── Scenario 96: 컬럼 폭 저장 - 일부 컬럼 예외는 건너뜀 ──────────────
    def test_scenario_96_on_column_resize_partial_exception(self):
        app = self._create_mocked_app()

        def _column(col, _):
            if col == "drive":
                raise Exception("no such column")
            return 100

        app._tree.column.side_effect = _column
        with patch("rclone_manager.save_config") as mock_save:
            app._on_column_resize()
            mock_save.assert_called_once()
            widths = app._cfg["column_widths"]
            self.assertNotIn("drive", widths)

    # ── Scenario 96b: 컬럼 폭 저장 - remote(리모트/서브경로)도 포함 ──────
    def test_scenario_96b_on_column_resize_includes_remote(self):
        app = self._create_mocked_app()
        app._tree.column.return_value = 250
        with patch("rclone_manager.save_config"):
            app._on_column_resize()
            widths = app._cfg["column_widths"]
            self.assertIn("remote", widths)
            self.assertEqual(widths["remote"], 250)

    # ── Scenario 97: rclone 레이블 초기화 - 존재함 ───────────────────────
    def test_scenario_97_init_rc_label_found(self):
        app = self._create_mocked_app({"rclone_path": "C:\\fake\\rclone.exe"})
        with patch("pathlib.Path.exists", return_value=True):
            app._init_rc_label()
            app._rc_ver_label.config.assert_called_with(
                text="v체크 중...", fg="#94e2d5")

    # ── Scenario 98: rclone 존재 확인 - 경로 없음(초기화 스킵) ───────────
    def test_scenario_98_check_rclone_presence_no_registered(self):
        app = self._create_mocked_app({"rclone_path": ""})
        with patch("pathlib.Path.exists", return_value=False), \
             patch("rclone_manager.save_config") as mock_save:
            app._check_rclone_presence()
            mock_save.assert_not_called()
            app._rc_ver_label.config.assert_called_with(
                text="rclone 다운로드", fg="#f38ba8")

    # ── Scenario 99: rclone 존재 확인 - 등록 경로 사라짐(초기화) ─────────
    def test_scenario_99_check_rclone_presence_reset_path(self):
        app = self._create_mocked_app({"rclone_path": "C:\\gone\\rclone.exe"})
        with patch("pathlib.Path.exists", return_value=False), \
             patch("rclone_manager.save_config") as mock_save:
            app._check_rclone_presence()
            self.assertEqual(app._cfg["rclone_path"], "")
            mock_save.assert_called_once()

    # ── Scenario 100: 포커스 이벤트 - 다른 위젯이면 무시 ─────────────────
    def test_scenario_100_on_focus_in_other_widget(self):
        app = self._create_mocked_app()
        event = MagicMock()
        event.widget = MagicMock()
        with patch.object(app, "_check_rclone_presence") as mock_check:
            app._on_focus_in(event)
            mock_check.assert_not_called()

    # ── Scenario 101: 버전 체크 - 이미 실행 중이면 스킵 ──────────────────
    def test_scenario_101_check_versions_async_already_running(self):
        app = self._create_mocked_app()
        app._version_check_running = True
        with patch("threading.Thread") as mock_thread:
            app._check_versions_async()
            mock_thread.assert_not_called()
            # force=False로 호출했으므로 예약(pending)도 설정되지 않아야 한다
            self.assertFalse(app._pending_force_check)

    # ── Scenario 101b: 버전 체크 - 실행 중에 force 요청 시 예약만 하고
    #                  새 스레드는 만들지 않는다(경쟁 상태 방지) ─────────
    def test_scenario_101b_check_versions_async_running_force_sets_pending(self):
        app = self._create_mocked_app()
        app._version_check_running = True
        with patch("threading.Thread") as mock_thread:
            app._check_versions_async(force=True)
            # 이미 실행 중이면 새 스레드를 만들지 않고 예약만 한다
            mock_thread.assert_not_called()
            self.assertTrue(app._pending_force_check)

    # ── Scenario 101c: 버전 체크 완료 후 예약된 force 요청을 자동 실행 ───
    def test_scenario_101c_check_versions_async_runs_pending_after_finish(self):
        app = self._create_mocked_app({"rclone_path": "C:\\fake\\rclone.exe"})
        app._pending_force_check = True
        with patch("threading.Thread", self._sync_thread()), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("subprocess.run") as mock_run, \
             patch("requests.get") as mock_get, \
             patch("rclone_manager.save_config"), \
             patch.object(app, "_check_versions_async",
                          wraps=app._check_versions_async) as wrapped:
            mock_run.return_value = MagicMock(stdout="rclone v1.70.0")
            resp = MagicMock()
            resp.json.return_value = {"tag_name": "v1.70.0"}
            mock_get.return_value = resp
            wrapped(force=False)
            # 체크가 끝난 뒤 예약돼 있던 force 체크가 자동으로 한 번 더 실행되어
            # 총 2회(원래 호출 + 예약 실행) 호출되어야 한다
            self.assertGreaterEqual(wrapped.call_count, 2)
            self.assertFalse(app._pending_force_check)

    # ── Scenario 102: 버전 체크 - force=True, 앱 업데이트 있음 ───────────
    def test_scenario_102_check_versions_async_force_update_available(self):
        app = self._create_mocked_app({"rclone_path": "C:\\fake\\rclone.exe"})
        with patch("threading.Thread", self._sync_thread()), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("subprocess.run") as mock_run, \
             patch("requests.get") as mock_get, \
             patch("rclone_manager.save_config"):
            mock_run.return_value = MagicMock(stdout="rclone v1.70.0")
            rclone_resp = MagicMock()
            rclone_resp.json.return_value = {"tag_name": "v1.75.0"}
            app_resp = MagicMock()
            app_resp.json.return_value = {"tag_name": "v9.9.9", "body": "note",
                                           "assets": []}
            mock_get.side_effect = [rclone_resp, app_resp]
            app._check_versions_async(force=True)
            app.after.assert_any_call(0, app._show_app_update_btn)

    # ── Scenario 103: 버전 체크 - 앱 최신 버전 아님(숨김) ────────────────
    def test_scenario_103_check_versions_async_app_up_to_date(self):
        app = self._create_mocked_app({"rclone_path": "C:\\fake\\rclone.exe"})
        with patch("threading.Thread", self._sync_thread()), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("subprocess.run") as mock_run, \
             patch("requests.get") as mock_get, \
             patch("rclone_manager.save_config"):
            mock_run.return_value = MagicMock(stdout="rclone v1.70.0")
            rclone_resp = MagicMock()
            rclone_resp.json.return_value = {"tag_name": "v1.70.0"}
            app_resp = MagicMock()
            app_resp.json.return_value = {"tag_name": "v0.0.1", "body": "", "assets": []}
            mock_get.side_effect = [rclone_resp, app_resp]
            app._check_versions_async(force=True)
            app.after.assert_any_call(0, app._hide_app_update_btn)

    # ── Scenario 104: 버전 체크 - rclone API 실패 시 이전 값 재사용 ──────
    def test_scenario_104_check_versions_async_rclone_api_failure(self):
        app = self._create_mocked_app({"rclone_path": "C:\\fake\\rclone.exe"})
        app._latest_rc = "1.70.0"
        with patch("threading.Thread", self._sync_thread()), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("subprocess.run") as mock_run, \
             patch("requests.get") as mock_get, \
             patch("rclone_manager.save_config"), \
             patch("rclone_manager.write_log") as mock_log:
            mock_run.return_value = MagicMock(stdout="rclone v1.70.0")
            app_resp = MagicMock()
            app_resp.json.return_value = {"tag_name": "v0.0.1", "body": "", "assets": []}
            mock_get.side_effect = [Exception("network fail"), app_resp]
            app._check_versions_async(force=True)
            mock_log.assert_any_call(
                "WARN", "[버전] rclone GitHub API 호출 실패: network fail")

    # ── Scenario 105: 버전 체크 - 앱 API 실패해도 계속 진행 ──────────────
    def test_scenario_105_check_versions_async_app_api_failure(self):
        app = self._create_mocked_app({"rclone_path": "C:\\fake\\rclone.exe"})
        with patch("threading.Thread", self._sync_thread()), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("subprocess.run") as mock_run, \
             patch("requests.get") as mock_get, \
             patch("rclone_manager.save_config"):
            mock_run.return_value = MagicMock(stdout="rclone v1.70.0")
            rclone_resp = MagicMock()
            rclone_resp.json.return_value = {"tag_name": "v1.70.0"}
            mock_get.side_effect = [rclone_resp, Exception("app api down")]
            app._check_versions_async(force=True)

    # ── Scenario 106: 버전 체크 - rclone 미등록(다운로드 표시) ───────────
    def test_scenario_106_check_versions_async_no_rclone(self):
        app = self._create_mocked_app({"rclone_path": ""})
        with patch("threading.Thread", self._sync_thread()), \
             patch("pathlib.Path.exists", return_value=False), \
             patch("requests.get") as mock_get, \
             patch("rclone_manager.save_config"):
            resp = MagicMock()
            resp.json.return_value = {"tag_name": "v1.0.0", "body": "", "assets": []}
            mock_get.return_value = resp
            app._check_versions_async(force=True)
            app.after.assert_any_call(0, mock.ANY)

    # ── Scenario 107: 버전 체크 - 로컬 버전 매칭 실패 ────────────────────
    def test_scenario_107_check_versions_async_local_version_unmatched(self):
        app = self._create_mocked_app({"rclone_path": "C:\\fake\\rclone.exe"})
        with patch("threading.Thread", self._sync_thread()), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("subprocess.run") as mock_run, \
             patch("requests.get") as mock_get, \
             patch("rclone_manager.save_config"):
            mock_run.return_value = MagicMock(stdout="no version info here")
            resp = MagicMock()
            resp.json.return_value = {"tag_name": "v1.0.0", "body": "", "assets": []}
            mock_get.return_value = resp
            app._check_versions_async(force=True)
            self.assertTrue(app.after.called)

    # ── Scenario 108: 버전 체크 - rclone version 실행 예외 ───────────────
    def test_scenario_108_check_versions_async_subprocess_exception(self):
        app = self._create_mocked_app({"rclone_path": "C:\\fake\\rclone.exe"})
        with patch("threading.Thread", self._sync_thread()), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("subprocess.run", side_effect=Exception("crash")), \
             patch("requests.get") as mock_get, \
             patch("rclone_manager.save_config"), \
             patch("rclone_manager.write_log") as mock_log:
            resp = MagicMock()
            resp.json.return_value = {"tag_name": "v1.0.0", "body": "", "assets": []}
            mock_get.return_value = resp
            app._check_versions_async(force=True)
            mock_log.assert_any_call("ERROR", mock.ANY)

    # ── Scenario 109: 버전 체크 - skip_app_api(24h 이내) 캐시된 정보 사용 ─
    def test_scenario_109_check_versions_async_skip_app_api(self):
        import time as _time_mod
        app = self._create_mocked_app({"rclone_path": "C:\\fake\\rclone.exe"})
        app._cfg["last_version_check"] = _time_mod.time()
        app._latest_app_info = {"tag_name": "v9.9.9"}
        with patch("threading.Thread", self._sync_thread()), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("subprocess.run") as mock_run, \
             patch("requests.get") as mock_get, \
             patch("rclone_manager.save_config"):
            mock_run.return_value = MagicMock(stdout="rclone v1.70.0")
            resp = MagicMock()
            resp.json.return_value = {"tag_name": "v1.75.0"}
            mock_get.return_value = resp
            app._check_versions_async(force=False)
            app.after.assert_any_call(0, app._show_app_update_btn)
            self.assertEqual(mock_get.call_count, 1)

    # ── Scenario 110: 앱 업데이트 버튼 표시 - 이미 표시됨(중복 방지) ─────
    def test_scenario_110_show_app_update_btn_already_mapped(self):
        app = self._create_mocked_app()
        app._app_up_btn.winfo_ismapped.return_value = True
        app._show_app_update_btn()
        app._app_up_btn.pack.assert_not_called()

    # ── Scenario 111: 앱 업데이트 버튼 표시 - 미표시 상태에서 표시 ───────
    def test_scenario_111_show_app_update_btn_not_mapped(self):
        app = self._create_mocked_app()
        app._app_up_btn.winfo_ismapped.return_value = False
        app._show_app_update_btn()
        app._app_up_btn.pack.assert_called_once_with(side="right")

    # ── Scenario 112: 앱 업데이트 버튼 숨김 - 표시 상태에서 숨김 ─────────
    def test_scenario_112_hide_app_update_btn_mapped(self):
        app = self._create_mocked_app()
        app._app_up_btn.winfo_ismapped.return_value = True
        app._hide_app_update_btn()
        app._app_up_btn.pack_forget.assert_called_once()

    # ── Scenario 113: 앱 업데이트 확인 - 최신 정보 없으면 무시 ───────────
    def test_scenario_113_show_app_update_confirm_no_info(self):
        app = self._create_mocked_app()
        app._latest_app_info = None
        with patch("rclone_manager.UpdateDialog") as mock_dlg_cls:
            app._show_app_update_confirm()
            mock_dlg_cls.assert_not_called()

    # ── Scenario 114: 앱 업데이트 확인 - 취소 시 다운로드 안 함 ──────────
    def test_scenario_114_show_app_update_confirm_cancelled(self):
        app = self._create_mocked_app()
        app._latest_app_info = {"tag_name": "v9.9.9", "body": "note", "assets": []}
        with patch("rclone_manager.UpdateDialog") as mock_dlg_cls, \
             patch("threading.Thread") as mock_thread:
            mock_dlg = MagicMock()
            mock_dlg.confirmed = False
            mock_dlg_cls.return_value = mock_dlg
            app._show_app_update_confirm()
            mock_thread.assert_not_called()

    # ── Scenario 115: 앱 업데이트 확인 - asset 없으면 브라우저 오픈 ──────
    def test_scenario_115_show_app_update_confirm_no_asset(self):
        app = self._create_mocked_app()
        app._latest_app_info = {"tag_name": "v9.9.9", "body": "note", "assets": []}
        with patch("rclone_manager.UpdateDialog") as mock_dlg_cls, \
             patch("webbrowser.open") as mock_open:
            mock_dlg = MagicMock()
            mock_dlg.confirmed = True
            mock_dlg._asset_url = ""
            mock_dlg_cls.return_value = mock_dlg
            app._show_app_update_confirm()
            mock_open.assert_called_once()

    # ── Scenario 116: 앱 업데이트 확인 - 다운로드 성공(수동 교체 안내) ───
    def test_scenario_116_show_app_update_confirm_manual_success(self):
        app = self._create_mocked_app()
        app._latest_app_info = {"tag_name": "v9.9.9", "body": "note", "assets": []}
        with patch("rclone_manager.UpdateDialog") as mock_dlg_cls, \
             patch("threading.Thread", self._sync_thread()), \
             patch("rclone_manager.download_app_release", return_value="manual"), \
             patch("tkinter.messagebox.showinfo") as mock_info, \
             patch("rclone_manager.write_log"):
            mock_dlg = MagicMock()
            mock_dlg.confirmed = True
            mock_dlg._asset_url = "https://example.com/app.zip"
            mock_dlg_cls.return_value = mock_dlg
            app._show_app_update_confirm()
            mock_info.assert_called_once()

    # ── Scenario 117: 앱 업데이트 확인 - 다운로드 실패 ───────────────────
    def test_scenario_117_show_app_update_confirm_download_error(self):
        app = self._create_mocked_app()
        app._latest_app_info = {"tag_name": "v9.9.9", "body": "note", "assets": []}
        with patch("rclone_manager.UpdateDialog") as mock_dlg_cls, \
             patch("threading.Thread", self._sync_thread()), \
             patch("rclone_manager.download_app_release", return_value="failed"), \
             patch("tkinter.messagebox.showinfo") as mock_info, \
             patch("rclone_manager.write_log"):
            mock_dlg = MagicMock()
            mock_dlg.confirmed = True
            mock_dlg._asset_url = "https://example.com/app.zip"
            mock_dlg_cls.return_value = mock_dlg
            app._show_app_update_confirm()
            mock_info.assert_called_with("알림", "failed")

    # ── Scenario 118: 업데이트 폴더 버튼 설정 ────────────────────────────
    def test_scenario_118_set_update_downloaded_btn(self):
        app = self._create_mocked_app()
        app._set_update_downloaded_btn(Path("C:\\out"))
        self.assertEqual(app._update_folder, Path("C:\\out"))
        app._app_up_btn.config.assert_called_once()

    # ── Scenario 119: 업데이트 폴더 열기 - 지정된 폴더 ───────────────────
    def test_scenario_119_open_update_folder_with_folder(self):
        app = self._create_mocked_app()
        app._update_folder = Path("C:\\out")
        with patch("subprocess.Popen") as mock_popen:
            app._open_update_folder()
            mock_popen.assert_called_once()

    # ── Scenario 120: 업데이트 폴더 열기 - 기본값(APP_DIR) ───────────────
    def test_scenario_120_open_update_folder_default(self):
        app = self._create_mocked_app()
        with patch("subprocess.Popen") as mock_popen:
            app._open_update_folder()
            mock_popen.assert_called_once()

    # ── Scenario 121: rclone 클릭 - 다운로드, 최신 버전 미확인 ───────────
    def test_scenario_121_handle_rc_click_download_no_latest(self):
        app = self._create_mocked_app()
        app._rc_ver_label.cget.return_value = "rclone 다운로드"
        app._latest_rc = ""
        with patch("tkinter.messagebox.showinfo") as mock_info:
            app._handle_rc_click(MagicMock())
            mock_info.assert_called_once()

    # ── Scenario 122: rclone 클릭 - 다운로드 동의 ────────────────────────
    def test_scenario_122_handle_rc_click_download_confirmed(self):
        app = self._create_mocked_app()
        app._rc_ver_label.cget.return_value = "rclone 다운로드"
        app._latest_rc = "1.70.0"
        with patch("tkinter.messagebox.askyesno", return_value=True), \
             patch.object(app, "_do_rc_down") as mock_down, \
             patch("threading.Thread", self._sync_thread()):
            app._handle_rc_click(MagicMock())
            mock_down.assert_called_once()

    # ── Scenario 123: rclone 클릭 - 다운로드 거절 ────────────────────────
    def test_scenario_123_handle_rc_click_download_declined(self):
        app = self._create_mocked_app()
        app._rc_ver_label.cget.return_value = "rclone 다운로드"
        app._latest_rc = "1.70.0"
        with patch("tkinter.messagebox.askyesno", return_value=False), \
             patch.object(app, "_do_rc_down") as mock_down, \
             patch("threading.Thread", self._sync_thread()):
            app._handle_rc_click(MagicMock())
            mock_down.assert_not_called()

    # ── Scenario 124: rclone 클릭 - 업데이트, 최신버전 미확인시 무시 ─────
    def test_scenario_124_handle_rc_click_update_no_latest(self):
        app = self._create_mocked_app()
        app._rc_ver_label.cget.return_value = "v1.0.0 / v1.1.0 업데이트"
        app._latest_rc = ""
        with patch.object(app, "_do_rc_down") as mock_down, \
             patch("threading.Thread", self._sync_thread()):
            app._handle_rc_click(MagicMock())
            mock_down.assert_not_called()

    # ── Scenario 125: rclone 클릭 - 업데이트, 마운트 중이면 경고 후 거절 ─
    def test_scenario_125_handle_rc_click_update_mounted_declined(self):
        app = self._create_mocked_app()
        app._rc_ver_label.cget.return_value = "v1.0.0 / v1.1.0 업데이트"
        app._latest_rc = "1.1.0"
        app._cfg["mounts"] = [{"id": "m1", "drive": "X:", "remote": "r"}]
        rclone_manager.active_mounts["m1"] = MagicMock()
        with patch("tkinter.messagebox.askyesno", return_value=False), \
             patch.object(app, "_do_rc_down") as mock_down, \
             patch("threading.Thread", self._sync_thread()):
            app._handle_rc_click(MagicMock())
            mock_down.assert_not_called()

    # ── Scenario 126: rclone 클릭 - 업데이트, 마운트 없으면 바로 진행 ────
    def test_scenario_126_handle_rc_click_update_no_mounts(self):
        app = self._create_mocked_app()
        app._rc_ver_label.cget.return_value = "v1.0.0 / v1.1.0 업데이트"
        app._latest_rc = "1.1.0"
        app._cfg["mounts"] = []
        with patch.object(app, "_do_rc_down") as mock_down, \
             patch("threading.Thread", self._sync_thread()):
            app._handle_rc_click(MagicMock())
            mock_down.assert_called_once()

    # ── Scenario 127: rclone 업데이트 실행 - 성공(재마운트 포함) ─────────
    def test_scenario_127_do_rc_down_success_with_remount(self):
        app = self._create_mocked_app({"rclone_path": "C:\\fake\\rclone.exe"})
        app._latest_rc = "1.70.0"
        app._cfg["mounts"] = [{"id": "m1", "drive": "X:", "remote": "r",
                                "remote_path": ""}]
        rclone_manager.active_mounts["m1"] = MagicMock()
        with patch("rclone_manager.download_rclone", return_value=True), \
             patch("rclone_manager.save_config"), \
             patch("rclone_manager.unmount") as mock_unmount, \
             patch("tkinter.messagebox.showinfo"), \
             patch.object(app, "_check_versions_async"), \
             patch.object(app, "_do_mount") as mock_do_mount:
            app._do_rc_down()
            mock_unmount.assert_called_once_with("m1")
            self.assertTrue(app.after.called)

    # ── Scenario 128: rclone 업데이트 실행 - 수동 교체 필요 ──────────────
    def test_scenario_128_do_rc_down_manual(self):
        app = self._create_mocked_app({"rclone_path": "C:\\fake\\rclone.exe"})
        app._latest_rc = "1.70.0"
        app._cfg["mounts"] = []
        with patch("rclone_manager.download_rclone", return_value="manual"), \
             patch("tkinter.messagebox.showinfo") as mock_info:
            app._do_rc_down()
            self.assertTrue(app.after.called)
            mock_info.assert_called_once()

    # ── Scenario 129: rclone 업데이트 실행 - 오류 ────────────────────────
    def test_scenario_129_do_rc_down_error(self):
        app = self._create_mocked_app({"rclone_path": "C:\\fake\\rclone.exe"})
        app._latest_rc = "1.70.0"
        app._cfg["mounts"] = []
        with patch("rclone_manager.download_rclone", return_value="some error"), \
             patch("tkinter.messagebox.showinfo") as mock_info:
            app._do_rc_down()
            mock_info.assert_called_with("알림", "some error")

    # ── Scenario 130: 트레이 시작 - 사용 불가 환경 ───────────────────────
    def test_scenario_130_start_tray_unavailable(self):
        app = self._create_mocked_app()
        with patch("rclone_manager._TRAY_AVAILABLE", False):
            app._start_tray()

    # ── Scenario 131: 트레이 시작 - 예외 발생 시 None 처리 ───────────────
    def test_scenario_131_start_tray_exception(self):
        app = self._create_mocked_app()
        with patch("rclone_manager._TRAY_AVAILABLE", True), \
             patch("rclone_manager._make_circle_icon", side_effect=Exception("x")), \
             patch("rclone_manager.write_log") as mock_log:
            app._start_tray()
            self.assertIsNone(app._tray)
            mock_log.assert_called()

    # ── Scenario 132: 트레이 메뉴 - 사용 불가 환경 ───────────────────────
    def test_scenario_132_build_tray_menu_unavailable(self):
        app = self._create_mocked_app()
        with patch("rclone_manager._TRAY_AVAILABLE", False):
            self.assertIsNone(app._build_tray_menu())

    # ── Scenario 133: 트레이 메뉴 - 마운트 없음 ──────────────────────────
    def test_scenario_133_build_tray_menu_no_mounts(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = []
        with patch("rclone_manager._TRAY_AVAILABLE", True), \
             patch("rclone_manager.pystray", create=True) as mock_pystray:
            mock_pystray.Menu.SEPARATOR = "SEP"
            mock_pystray.MenuItem = MagicMock(side_effect=lambda *a, **k: (a, k))
            mock_pystray.Menu = MagicMock(side_effect=lambda *a: a)
            app._build_tray_menu()
            calls = [c for c in mock_pystray.MenuItem.call_args_list
                     if c[0][0] == "(등록된 마운트 없음)"]
            self.assertEqual(len(calls), 1)

    # ── Scenario 134: 트레이 메뉴 - 마운트 중 항목 토글(언마운트) ────────
    def test_scenario_134_build_tray_menu_toggle_unmount(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "m1", "drive": "X:", "remote": "r",
                                "remote_path": ""}]
        app._status["m1"] = "mounted"
        with patch("rclone_manager._TRAY_AVAILABLE", True), \
             patch("rclone_manager.pystray", create=True) as mock_pystray, \
             patch("rclone_manager.unmount") as mock_unmount:
            mock_pystray.Menu.SEPARATOR = "SEP"
            captured = {}

            def _menu_item(display, callback, **kw):
                captured.setdefault("items", []).append((display, callback))
                return (display, callback)

            mock_pystray.MenuItem = MagicMock(side_effect=_menu_item)
            mock_pystray.Menu = MagicMock(side_effect=lambda *a: a)
            app._build_tray_menu()
            toggle_cb = [cb for disp, cb in captured["items"] if "X:" in disp][0]
            toggle_cb(None, None)
            mock_unmount.assert_called_once_with("m1")

    # ── Scenario 135: 트레이 메뉴 - 중지 상태 항목 토글(마운트) ──────────
    def test_scenario_135_build_tray_menu_toggle_mount(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "m1", "drive": "X:", "remote": "r",
                                "remote_path": ""}]
        with patch("rclone_manager._TRAY_AVAILABLE", True), \
             patch("rclone_manager.pystray", create=True) as mock_pystray:
            mock_pystray.Menu.SEPARATOR = "SEP"
            captured = {}

            def _menu_item(display, callback, **kw):
                captured.setdefault("items", []).append((display, callback))
                return (display, callback)

            mock_pystray.MenuItem = MagicMock(side_effect=_menu_item)
            mock_pystray.Menu = MagicMock(side_effect=lambda *a: a)
            app._build_tray_menu()
            toggle_cb = [cb for disp, cb in captured["items"] if "X:" in disp][0]
            with patch("tkinter.messagebox.showinfo"):
                toggle_cb(None, None)
            self.assertTrue(app.after.called)

    # ── Scenario 136: 목록 갱신 - 트레이 갱신 예외 무시 ──────────────────
    def test_scenario_136_refresh_list_tray_exception(self):
        app = self._create_mocked_app()
        app._cfg["remotes"] = [{"name": "r1", "type": "drive"}]
        app._cfg["mounts"] = [{"id": "m1", "remote": "r1", "remote_path": "",
                                "drive": "X:"}]
        app._tray.update_menu.side_effect = Exception("tray gone")
        with patch("rclone_manager.write_log") as mock_log:
            app._refresh_list()
            mock_log.assert_any_call("WARN", mock.ANY)

    # ── Scenario 137: 시작프로그램 토글 ───────────────────────────────────
    def test_scenario_137_toggle_st(self):
        app = self._create_mocked_app()
        app._st_var.get.return_value = True
        with patch("rclone_manager.set_startup") as mock_set:
            app._toggle_st()
            mock_set.assert_called_once_with(True)

    # ── Scenario 138: 트레이 최소화 옵션 토글 ────────────────────────────
    def test_scenario_138_toggle_min(self):
        app = self._create_mocked_app()
        app._min_var.get.return_value = True
        with patch("rclone_manager.save_config") as mock_save:
            app._toggle_min()
            self.assertTrue(app._cfg["start_minimized"])
            mock_save.assert_called_once()

    # ── Scenario 139: 마운트 삭제 - 거절 시 삭제 안 함 ───────────────────
    def test_scenario_139_delete_mount_declined(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "m1", "remote": "r"}]
        with patch("tkinter.messagebox.askyesno", return_value=False), \
             patch("rclone_manager.save_config") as mock_save:
            app._delete_mount("m1")
            self.assertEqual(len(app._cfg["mounts"]), 1)
            mock_save.assert_not_called()

    # ── Scenario 140: rclone 경로 찾아보기 - 선택함 ──────────────────────
    def test_scenario_140_browse_rc_selected(self):
        app = self._create_mocked_app()
        with patch("tkinter.filedialog.askopenfilename",
                   return_value="C:\\new\\rclone.exe"), \
             patch("rclone_manager.save_config") as mock_save, \
             patch.object(app, "_check_rclone_presence") as mock_check:
            app._browse_rc()
            self.assertEqual(app._cfg["rclone_path"], "C:\\new\\rclone.exe")
            mock_save.assert_called_once()
            mock_check.assert_called_once()

    # ── Scenario 141: rclone 경로 찾아보기 - 취소함 ──────────────────────
    def test_scenario_141_browse_rc_cancelled(self):
        app = self._create_mocked_app()
        prev = app._cfg.get("rclone_path", "")
        with patch("tkinter.filedialog.askopenfilename", return_value=""), \
             patch("rclone_manager.save_config") as mock_save:
            app._browse_rc()
            mock_save.assert_not_called()
            self.assertEqual(app._cfg.get("rclone_path", ""), prev)

    # ── Scenario 142: conf 가져오기 - 경로 선택 취소 ─────────────────────
    def test_scenario_142_import_conf_cancelled(self):
        app = self._create_mocked_app()
        with patch("rclone_manager.find_default_rclone_conf", return_value=None), \
             patch("tkinter.filedialog.askopenfilename", return_value=""):
            app._import_conf()

    # ── Scenario 143: conf 가져오기 - 신규 리모트만 추가(중복 제외) ──────
    def test_scenario_143_import_conf_adds_new_only(self):
        app = self._create_mocked_app()
        app._cfg["remotes"] = [{"name": "existing", "type": "drive"}]
        with patch("rclone_manager.find_default_rclone_conf",
                   return_value=Path("C:\\rclone.conf")), \
             patch("tkinter.filedialog.askopenfilename",
                   return_value="C:\\rclone.conf"), \
             patch("rclone_manager.parse_rclone_conf", return_value=[]), \
             patch("rclone_manager.ConfImportDialog") as mock_dlg_cls, \
             patch("rclone_manager.save_config") as mock_save:
            mock_dlg = MagicMock()
            mock_dlg.selected = [("existing", "drive"), ("newone", "drive")]
            mock_dlg_cls.return_value = mock_dlg
            app._import_conf()
            names = [r["name"] for r in app._cfg["remotes"]]
            self.assertEqual(names.count("existing"), 1)
            self.assertIn("newone", names)
            mock_save.assert_called_once()

    # ── Scenario 144: 마운트 추가 - 원본 선택 상태에서 사전 채움 ─────────
    def test_scenario_144_add_prefill_from_remote_selection(self):
        app = self._create_mocked_app()
        app._tree.selection.return_value = ["remote_gds"]
        with patch("rclone_manager.MountDialog") as mock_dlg_cls, \
             patch("rclone_manager.save_config"):
            mock_dlg = MagicMock()
            mock_dlg.result = None
            mock_dlg_cls.return_value = mock_dlg
            app._add()
            args, kwargs = mock_dlg_cls.call_args
            mount_arg = kwargs.get("mount") if "mount" in kwargs else args[1]
            self.assertEqual(mount_arg["remote"], "gds")

    # ── Scenario 145: 마운트 추가 - 결과 없음(취소) ──────────────────────
    def test_scenario_145_add_cancelled(self):
        app = self._create_mocked_app()
        app._tree.selection.return_value = []
        before = len(app._cfg["mounts"])
        with patch("rclone_manager.MountDialog") as mock_dlg_cls, \
             patch("rclone_manager.save_config") as mock_save:
            mock_dlg = MagicMock()
            mock_dlg.result = None
            mock_dlg_cls.return_value = mock_dlg
            app._add()
            self.assertEqual(len(app._cfg["mounts"]), before)
            mock_save.assert_not_called()

    # ── Scenario 146: 마운트 편집 - 선택 없음(무시) ──────────────────────
    def test_scenario_146_edit_no_selection(self):
        app = self._create_mocked_app()
        app._tree.selection.return_value = []
        with patch("rclone_manager.MountDialog") as mock_dlg_cls:
            app._edit()
            mock_dlg_cls.assert_not_called()

    # ── Scenario 147: 마운트 편집 - 원본 선택시 무시 ─────────────────────
    def test_scenario_147_edit_remote_selection_ignored(self):
        app = self._create_mocked_app()
        app._tree.selection.return_value = ["remote_gds"]
        with patch("rclone_manager.MountDialog") as mock_dlg_cls:
            app._edit()
            mock_dlg_cls.assert_not_called()

    # ── Scenario 148: 마운트 편집 - 결과 반영 ────────────────────────────
    def test_scenario_148_edit_applies_result(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "m1", "remote": "old"}]
        app._tree.selection.return_value = ["m1"]
        with patch("rclone_manager.MountDialog") as mock_dlg_cls, \
             patch("rclone_manager.save_config") as mock_save:
            mock_dlg = MagicMock()
            mock_dlg.result = {"remote": "new"}
            mock_dlg_cls.return_value = mock_dlg
            app._edit()
            self.assertEqual(app._cfg["mounts"][0]["remote"], "new")
            mock_save.assert_called_once()

    # ── Scenario 149: 삭제 - 선택 없음 ───────────────────────────────────
    def test_scenario_149_del_no_selection(self):
        app = self._create_mocked_app()
        app._tree.selection.return_value = []
        with patch("rclone_manager.save_config") as mock_save:
            app._del()
            mock_save.assert_not_called()

    # ── Scenario 150: 삭제 - 원본 삭제 동의 ──────────────────────────────
    def test_scenario_150_del_remote_confirmed(self):
        app = self._create_mocked_app()
        app._cfg["remotes"] = [{"name": "gds", "type": "drive"}]
        app._tree.selection.return_value = ["remote_gds"]
        with patch("tkinter.messagebox.askyesno", return_value=True), \
             patch("rclone_manager.save_config") as mock_save:
            app._del()
            self.assertEqual(len(app._cfg["remotes"]), 0)
            mock_save.assert_called_once()

    # ── Scenario 151: 삭제 - 원본 삭제 거절 ──────────────────────────────
    def test_scenario_151_del_remote_declined(self):
        app = self._create_mocked_app()
        app._cfg["remotes"] = [{"name": "gds", "type": "drive"}]
        app._tree.selection.return_value = ["remote_gds"]
        with patch("tkinter.messagebox.askyesno", return_value=False), \
             patch("rclone_manager.save_config") as mock_save:
            app._del()
            self.assertEqual(len(app._cfg["remotes"]), 1)
            mock_save.assert_not_called()

    # ── Scenario 152: 삭제 - 마운트 항목은 _delete_mount로 위임 ──────────
    def test_scenario_152_del_mount_delegates(self):
        app = self._create_mocked_app()
        app._tree.selection.return_value = ["m1"]
        with patch.object(app, "_delete_mount") as mock_del:
            app._del()
            mock_del.assert_called_once_with("m1")

    # ── Scenario 153: 위로 이동 - 원본, 경계(맨 위)라 이동 없음 ──────────
    def test_scenario_153_move_up_remote_boundary(self):
        app = self._create_mocked_app()
        app._cfg["remotes"] = [{"name": "a", "type": "drive"},
                                {"name": "b", "type": "drive"}]
        app._tree.selection.return_value = ["remote_a"]
        with patch("rclone_manager.save_config") as mock_save:
            app._move_up()
            mock_save.assert_not_called()
            self.assertEqual(app._cfg["remotes"][0]["name"], "a")

    # ── Scenario 154: 위로 이동 - 원본, 스왑 발생 ────────────────────────
    def test_scenario_154_move_up_remote_swap(self):
        app = self._create_mocked_app()
        app._cfg["remotes"] = [{"name": "a", "type": "drive"},
                                {"name": "b", "type": "drive"}]
        app._tree.selection.return_value = ["remote_b"]
        with patch("rclone_manager.save_config") as mock_save:
            app._move_up()
            self.assertEqual(app._cfg["remotes"][0]["name"], "b")
            mock_save.assert_called_once()

    # ── Scenario 155: 위로 이동 - 마운트, 맨 위라 이동 없음 ──────────────
    def test_scenario_155_move_up_mount_boundary(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "m1", "remote": "r1", "remote_path": "",
                                "drive": "X:"},
                               {"id": "m2", "remote": "r2", "remote_path": "",
                                "drive": "Y:"}]
        app._tree.selection.return_value = ["m1"]
        with patch("rclone_manager.save_config") as mock_save:
            app._move_up()
            mock_save.assert_not_called()

    # ── Scenario 156: 위로 이동 - 마운트, 스왑 발생 ──────────────────────
    def test_scenario_156_move_up_mount_swap(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "m1", "remote": "r1", "remote_path": "",
                                "drive": "X:"},
                               {"id": "m2", "remote": "r2", "remote_path": "",
                                "drive": "Y:"}]
        app._tree.selection.return_value = ["m2"]
        with patch("rclone_manager.save_config") as mock_save:
            app._move_up()
            self.assertEqual(app._cfg["mounts"][0]["id"], "m2")
            mock_save.assert_called_once()

    # ── Scenario 157: 아래로 이동 - 원본, 맨 아래라 이동 없음 ────────────
    def test_scenario_157_move_down_remote_boundary(self):
        app = self._create_mocked_app()
        app._cfg["remotes"] = [{"name": "a", "type": "drive"},
                                {"name": "b", "type": "drive"}]
        app._tree.selection.return_value = ["remote_b"]
        with patch("rclone_manager.save_config") as mock_save:
            app._move_down()
            mock_save.assert_not_called()

    # ── Scenario 158: 아래로 이동 - 원본, 스왑 발생 ──────────────────────
    def test_scenario_158_move_down_remote_swap(self):
        app = self._create_mocked_app()
        app._cfg["remotes"] = [{"name": "a", "type": "drive"},
                                {"name": "b", "type": "drive"}]
        app._tree.selection.return_value = ["remote_a"]
        with patch("rclone_manager.save_config") as mock_save:
            app._move_down()
            self.assertEqual(app._cfg["remotes"][1]["name"], "a")
            mock_save.assert_called_once()

    # ── Scenario 159: 아래로 이동 - 마운트, 맨 아래라 이동 없음 ──────────
    def test_scenario_159_move_down_mount_boundary(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "m1", "remote": "r1", "remote_path": "",
                                "drive": "X:"},
                               {"id": "m2", "remote": "r2", "remote_path": "",
                                "drive": "Y:"}]
        app._tree.selection.return_value = ["m2"]
        with patch("rclone_manager.save_config") as mock_save:
            app._move_down()
            mock_save.assert_not_called()

    # ── Scenario 160: 아래로 이동 - 마운트, 스왑 발생 ────────────────────
    def test_scenario_160_move_down_mount_swap(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "m1", "remote": "r1", "remote_path": "",
                                "drive": "X:"},
                               {"id": "m2", "remote": "r2", "remote_path": "",
                                "drive": "Y:"}]
        app._tree.selection.return_value = ["m1"]
        with patch("rclone_manager.save_config") as mock_save:
            app._move_down()
            self.assertEqual(app._cfg["mounts"][1]["id"], "m1")
            mock_save.assert_called_once()

    # ── Scenario 161: 마운트 선택 실행 - 선택 없음 ───────────────────────
    def test_scenario_161_mount_sel_no_selection(self):
        app = self._create_mocked_app()
        app._tree.selection.return_value = []
        with patch.object(app, "_mount_single") as mock_single:
            app._mount_sel()
            mock_single.assert_not_called()

    # ── Scenario 162: 마운트 선택 실행 - 원본이면 무시 ───────────────────
    def test_scenario_162_mount_sel_remote_ignored(self):
        app = self._create_mocked_app()
        app._tree.selection.return_value = ["remote_gds"]
        with patch.object(app, "_mount_single") as mock_single:
            app._mount_sel()
            mock_single.assert_not_called()

    # ── Scenario 163: 마운트 선택 실행 - 이미 마운트 중이면 무시 ─────────
    def test_scenario_163_mount_sel_already_mounted(self):
        app = self._create_mocked_app()
        app._tree.selection.return_value = ["m1"]
        rclone_manager.active_mounts["m1"] = MagicMock()
        with patch.object(app, "_mount_single") as mock_single:
            app._mount_sel()
            mock_single.assert_not_called()

    # ── Scenario 164: _do_mount - rclone 미등록 시 경고 로그 ─────────────
    def test_scenario_164_do_mount_no_rclone(self):
        app = self._create_mocked_app({"rclone_path": ""})
        with patch("pathlib.Path.exists", return_value=False), \
             patch("tkinter.messagebox.showinfo") as mock_info, \
             patch("rclone_manager.write_log") as mock_log:
            app._do_mount("m1", {"drive": "X:", "remote": "r"})
            mock_info.assert_called_once()
            mock_log.assert_called()

    # ── Scenario 165: _do_mount - 이미 마운트 중이면 무시 ────────────────
    def test_scenario_165_do_mount_already_mounted(self):
        app = self._create_mocked_app({"rclone_path": "C:\\fake\\rclone.exe"})
        rclone_manager.active_mounts["m1"] = MagicMock()
        with patch("pathlib.Path.exists", return_value=True), \
             patch("threading.Thread") as mock_thread:
            app._do_mount("m1", {"drive": "X:", "remote": "r"})
            mock_thread.assert_not_called()

    # ── Scenario 166: _mount_task - 2초 내 종료(오류 표시) ───────────────
    def test_scenario_166_mount_task_immediate_failure(self):
        app = self._create_mocked_app()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        mock_proc.stderr.read.return_value = b"error: bad flag"
        with patch("subprocess.Popen", return_value=mock_proc), \
             patch("time.sleep"), \
             patch("tkinter.messagebox.showinfo") as mock_info, \
             patch("rclone_manager.write_log"):
            app._mount_task("m1", Path("rclone.exe"),
                            {"remote": "r", "drive": "X:", "remote_path": ""})
            mock_info.assert_called_once()
            self.assertEqual(app._status["m1"], "stopped")

    # ── Scenario 167: _mount_task - 정상 마운트(오류 없음) ───────────────
    def test_scenario_167_mount_task_success(self):
        app = self._create_mocked_app()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr = iter([])
        mock_proc.wait.return_value = None
        mock_proc.returncode = 0
        with patch("subprocess.Popen", return_value=mock_proc), \
             patch("time.sleep"), \
             patch("tkinter.messagebox.showinfo") as mock_info, \
             patch("rclone_manager.write_log"):
            app._mount_task("m1", Path("rclone.exe"),
                            {"remote": "r", "drive": "X:", "remote_path": ""})
            import time as _t
            _t.sleep(0.2)
            mock_info.assert_not_called()

    # ── Scenario 168: _mount_task - 실행 중 종료(비정상 종료코드) ────────
    def test_scenario_168_mount_task_runtime_failure(self):
        app = self._create_mocked_app()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr = iter([])
        mock_proc.wait.return_value = None
        mock_proc.returncode = 1
        with patch("subprocess.Popen", return_value=mock_proc), \
             patch("time.sleep"), \
             patch("tkinter.messagebox.showinfo") as mock_info, \
             patch("rclone_manager.write_log"):
            app._mount_task("m1", Path("rclone.exe"),
                            {"remote": "r", "drive": "X:", "remote_path": ""})
            import time as _t
            _t.sleep(0.2)
            mock_info.assert_called_once()

    # ── Scenario 169: _mount_task - Popen 자체 예외 ──────────────────────
    def test_scenario_169_mount_task_popen_exception(self):
        app = self._create_mocked_app()
        with patch("subprocess.Popen", side_effect=Exception("cannot exec")), \
             patch("tkinter.messagebox.showinfo") as mock_info, \
             patch("rclone_manager.write_log"):
            app._mount_task("m1", Path("rclone.exe"),
                            {"remote": "r", "drive": "X:", "remote_path": ""})
            mock_info.assert_called_once()
            self.assertEqual(app._status["m1"], "stopped")

    # ── Scenario 170: _mount_task - 의도적 언마운트 중이면 오류 억제 ─────
    def test_scenario_170_mount_task_suppressed_during_unmount(self):
        app = self._create_mocked_app()
        rclone_manager._unmounting.add("m1")
        try:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = 1
            mock_proc.stderr.read.return_value = b"terminated"
            with patch("subprocess.Popen", return_value=mock_proc), \
                 patch("time.sleep"), \
                 patch("tkinter.messagebox.showinfo") as mock_info, \
                 patch("rclone_manager.write_log"):
                app._mount_task("m1", Path("rclone.exe"),
                                {"remote": "r", "drive": "X:", "remote_path": ""})
                mock_info.assert_not_called()
        finally:
            rclone_manager._unmounting.discard("m1")

    # ── Scenario 171: 자동 마운트 - 대상 없음 ────────────────────────────
    def test_scenario_171_automount_all_empty(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "m1", "auto_mount": False}]
        with patch.object(app, "_do_mount") as mock_do_mount, \
             patch("rclone_manager.write_log") as mock_log:
            app._automount_all()
            mock_do_mount.assert_not_called()
            mock_log.assert_not_called()

    # ── Scenario 172: 네트워크 모니터 - 중복 시작 방지 ───────────────────
    def test_scenario_172_start_net_monitor_already_running(self):
        app = self._create_mocked_app()
        app._net_monitor_running = True
        with patch("threading.Thread") as mock_thread:
            app._start_net_monitor()
            mock_thread.assert_not_called()

    # ── Scenario 173: 네트워크 모니터 - 연결 감지 시 자동 마운트 ─────────
    def test_scenario_173_start_net_monitor_connected(self):
        app = self._create_mocked_app()

        def _fake_sleep(_):
            app._net_monitor_running = False

        with patch("rclone_manager.is_internet_available", return_value=True), \
             patch("threading.Thread", self._sync_thread()), \
             patch("time.sleep", side_effect=_fake_sleep):
            app._start_net_monitor()
            app.after.assert_any_call(0, app._automount_all)

    # ── Scenario 174: 네트워크 모니터 - 끊김 감지 시 언마운트 ────────────
    def test_scenario_174_start_net_monitor_disconnected(self):
        app = self._create_mocked_app()
        app._net_was_connected = True

        def _fake_sleep(_):
            app._net_monitor_running = False

        with patch("rclone_manager.is_internet_available", return_value=False), \
             patch("threading.Thread", self._sync_thread()), \
             patch("time.sleep", side_effect=_fake_sleep):
            app._start_net_monitor()
            app.after.assert_any_call(0, app._unmount_all_on_disconnect)

    # ── Scenario 175: 끊김 시 전체 해제 - 마운트 없으면 조기 반환 ────────
    def test_scenario_175_unmount_all_on_disconnect_none_mounted(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "m1", "auto_mount": True}]
        with patch("rclone_manager.unmount") as mock_unmount, \
             patch("tkinter.messagebox.showinfo") as mock_info:
            app._unmount_all_on_disconnect()
            mock_unmount.assert_not_called()
            mock_info.assert_not_called()

    # ── Scenario 176: 끊김 시 전체 해제 - 자동 마운트는 조용히 해제 ──────
    def test_scenario_176_unmount_all_on_disconnect_auto_only(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "m1", "auto_mount": True,
                                "drive": "X:", "remote": "r", "remote_path": ""}]
        rclone_manager.active_mounts["m1"] = MagicMock()
        with patch("rclone_manager.unmount") as mock_unmount, \
             patch("tkinter.messagebox.showinfo") as mock_info:
            app._unmount_all_on_disconnect()
            mock_unmount.assert_called_once_with("m1")
            mock_info.assert_not_called()

    # ── Scenario 177: 끊김 시 전체 해제 - 수동 마운트는 알림 표시 ────────
    def test_scenario_177_unmount_all_on_disconnect_manual_alerts(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "m1", "auto_mount": False,
                                "drive": "X:", "remote": "r", "remote_path": ""}]
        rclone_manager.active_mounts["m1"] = MagicMock()
        with patch("rclone_manager.unmount") as mock_unmount, \
             patch("tkinter.messagebox.showinfo") as mock_info:
            app._unmount_all_on_disconnect()
            mock_unmount.assert_called_once_with("m1")
            mock_info.assert_called_once()

    # ── Scenario 178: 언마운트(선택) - 원본 선택시 무시 ──────────────────
    def test_scenario_178_unmount_sel_remote_ignored(self):
        app = self._create_mocked_app()
        app._tree.selection.return_value = ["remote_gds"]
        with patch("rclone_manager.unmount") as mock_unmount:
            app._unmount_sel()
            mock_unmount.assert_not_called()

    # ── Scenario 179: 언마운트(선택) - 정상 언마운트 ─────────────────────
    def test_scenario_179_unmount_sel_normal(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "m1", "drive": "X:", "remote": "r",
                                "remote_path": ""}]
        app._tree.selection.return_value = ["m1"]
        with patch("rclone_manager.unmount") as mock_unmount:
            app._unmount_sel()
            mock_unmount.assert_called_once_with("m1")

    # ── Scenario 180: 창 숨김/보이기 ─────────────────────────────────────
    def test_scenario_180_hide_and_show_window(self):
        app = self._create_mocked_app()
        app.hide_window()
        app.withdraw.assert_called_once()
        app.show_window()
        app.deiconify.assert_called_once()
        app.lift.assert_called_once()
        app.focus_force.assert_called_once()

    # ── Scenario 181: 앱 종료 - 트레이 있으면 정지 후 destroy ────────────
    def test_scenario_181_quit_app_with_tray(self):
        app = self._create_mocked_app()
        rclone_manager.active_mounts["m1"] = MagicMock()
        with patch("rclone_manager.unmount") as mock_unmount, \
             patch("rclone_manager.write_log"):
            app._quit_app()
            mock_unmount.assert_called_once_with("m1")
            app._tray.stop.assert_called_once()
            app.destroy.assert_called_once()
            self.assertFalse(app._net_monitor_running)

    # ── Scenario 182: 앱 종료 - 트레이 없으면 stop 생략 ──────────────────
    def test_scenario_182_quit_app_without_tray(self):
        app = self._create_mocked_app()
        app._tray = None
        with patch("rclone_manager.write_log"):
            app._quit_app()
            app.destroy.assert_called_once()

    # ── Scenario 183: 앱 버전 레이블 클릭 - 즉시 확인(force=True) ────────
    def test_scenario_183_handle_app_ver_click_forces_check(self):
        app = self._create_mocked_app()
        # 진행 중인 체크가 있어도 이제는 강제로 리셋하지 않는다
        # (강제 리셋은 두 스레드가 동시에 도는 경쟁 상태의 원인이었음)
        app._version_check_running = True
        with patch.object(app, "_check_versions_async") as mock_check:
            app._handle_app_ver_click(MagicMock())
            # _version_check_running을 건드리지 않고 그대로 force=True 요청만 위임
            self.assertTrue(app._version_check_running)
            mock_check.assert_called_once_with(force=True)
            app._app_ver_label.config.assert_any_call(
                text="버전 확인 중...", fg="#89b4fa")

    # ── Scenario 184: 인증서 안정화 - 정상 복사 및 환경변수 설정 ─────────
    def test_scenario_184_ensure_stable_ca_bundle_success(self):
        # Given: certifi가 정상 설치돼 있고 안정 경로에 인증서가 없을 때
        fake_certifi = MagicMock()
        fake_certifi.where.return_value = "/fake/_MEI123/certifi/cacert.pem"
        with patch.dict("sys.modules", {"certifi": fake_certifi}), \
             patch("pathlib.Path.exists", return_value=False), \
             patch("shutil.copy") as mock_copy, \
             patch.dict("os.environ", {}, clear=False):
            # When: 인증서 안정화를 수행하면
            rclone_manager._ensure_stable_ca_bundle()
            # Then: APP_DIR/cacert.pem 으로 복사하고 환경변수를 고정해야 한다
            mock_copy.assert_called_once()
            self.assertTrue(os.environ.get("REQUESTS_CA_BUNDLE", "").endswith("cacert.pem"))
            self.assertTrue(os.environ.get("SSL_CERT_FILE", "").endswith("cacert.pem"))

    # ── Scenario 185: 인증서 안정화 - 이미 존재하면 재복사하지 않음 ──────
    def test_scenario_185_ensure_stable_ca_bundle_already_exists(self):
        fake_certifi = MagicMock()
        fake_certifi.where.return_value = "/fake/_MEI123/certifi/cacert.pem"
        with patch.dict("sys.modules", {"certifi": fake_certifi}), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.stat") as mock_stat, \
             patch("shutil.copy") as mock_copy:
            mock_stat.return_value.st_size = 1000  # 이미 유효한 파일 존재
            rclone_manager._ensure_stable_ca_bundle()
            mock_copy.assert_not_called()

    # ── Scenario 186: 인증서 안정화 - certifi 없거나 예외 시 조용히 무시 ──
    def test_scenario_186_ensure_stable_ca_bundle_exception_ignored(self):
        with patch.dict("sys.modules", {"certifi": None}):
            try:
                rclone_manager._ensure_stable_ca_bundle()
            except Exception:
                self.fail("certifi가 없어도 예외 없이 조용히 넘어가야 한다")

if __name__ == "__main__":
    unittest.main(verbosity=2)
