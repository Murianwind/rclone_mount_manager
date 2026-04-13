import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import rclone_manager
import os
import configparser
import tkinter as tk
import sys

class TestRcloneManagerBDD(unittest.TestCase):

    def setUp(self):
        """테스트 데이터 초기 설정 (Given)"""
        self.sample_cfg = {"remotes": [], "mounts": [], "rclone_path": "", "auto_mount": False}

    def _create_mocked_app(self, cfg=None):
        """Mock 앱 인스턴스 생성 유틸리티 (RecursionError 방지)"""
        # Given: Tkinter 의존성을 Mocking한 App 객체를 생성한다.
        app = rclone_manager.App.__new__(rclone_manager.App)
        app.tk = MagicMock() 
        app._cfg = cfg if cfg else self.sample_cfg
        app._status = {}
        app._tray = MagicMock() 
        app._tree = MagicMock()
        app._rc_ver_label = MagicMock()
        app._app_up_btn = MagicMock()
        app._rc_var = MagicMock()
        app._am_var = MagicMock(); app._am_var.get = MagicMock()
        app._st_var = MagicMock(); app._st_var.get = MagicMock()
        app.after = MagicMock()
        app.withdraw = MagicMock()
        app.deiconify = MagicMock()
        app.lift = MagicMock()
        app.focus_force = MagicMock()
        app.bind = MagicMock()
        return app

    def _create_mocked_dialog(self, parent, mount=None, cfg=None):
        """Mock 다이얼로그 생성 유틸리티"""
        dlg = rclone_manager.MountDialog.__new__(rclone_manager.MountDialog)
        dlg._m = mount if mount else {}
        dlg._app_cfg = cfg if cfg else self.sample_cfg
        dlg._rem = MagicMock(); dlg._drv = MagicMock(); dlg._pth = MagicMock()
        dlg._cdir = MagicMock(); dlg._cmode = MagicMock(); dlg._ext = MagicMock()
        dlg._auto = MagicMock(); dlg.destroy = MagicMock()
        return dlg

    # 1. rclone 실행 파일 로드
    def test_scenario_01_load_rclone(self):
        # Given: rclone_path가 설정 파일에 존재할 때
        cfg = {"rclone_path": "C:\\fake\\rclone.exe"}
        with patch("pathlib.Path.exists", return_value=True):
            # When: rclone 실행 파일을 가져오면
            exe = rclone_manager.get_rclone_exe(cfg)
            # Then: 설정된 경로가 반환되어야 한다.
            self.assertEqual(str(exe), "C:\\fake\\rclone.exe")

    # 2. rclone 명령어 빌드 (기본)
    def test_scenario_02_build_cmd_basic(self):
        # Given: 리모트 이름과 드라이브 문자가 주어졌을 때
        exe = Path("rclone.exe")
        mount = {"remote": "drive", "drive": "X:", "remote_path": "data"}
        # When: 명령어를 빌드하면
        cmd = rclone_manager.build_cmd(exe, mount)
        # Then: 필수 인자들이 포함되어야 한다.
        self.assertIn("mount", cmd)
        self.assertIn("drive:data", cmd)

    # 17. 앱 삭제 UI 테스트
    def test_scenario_17_app_delete_ui(self):
        # Given: 삭제할 마운트 항목이 데이터에 존재할 때
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "test-id", "remote": "test"}]
        with patch("tkinter.messagebox.askyesno", return_value=True):
            # When: 삭제 메서드를 호출하면
            app._delete_mount("test-id")
            # Then: 데이터에서 해당 항목이 제거되어야 한다.
            self.assertEqual(len(app._cfg["mounts"]), 0)

    # 18. 마운트 작업 시작 테스트 (Scenario 18 실패 해결)
    def test_scenario_18_mount_task_start(self):
        # Given: 마운트할 데이터가 있고 rclone.exe가 존재한다고 패치할 때
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "test-id", "remote": "test"}]
        # When: 단일 마운트를 실행하면
        with patch("subprocess.Popen") as mock_popen, \
             patch("pathlib.Path.exists", return_value=True):
            app._mount_single("test-id")
            # Then: Popen이 실제로 호출되어야 한다.
            self.assertTrue(mock_popen.called)

    # 19. 자동 마운트 설정 토글 테스트
    def test_scenario_19_toggle_auto_mount(self):
        # Given: UI에서 자동 마운트 체크박스 값을 변경했을 때
        app = self._create_mocked_app()
        app._am_var.get.return_value = True
        # When: 설정을 저장하면
        app._save_settings()
        # Then: 설정 데이터(cfg)에 반영되어야 한다.
        self.assertTrue(app._cfg["auto_mount"])

    # 21. 이슈 리포트 URL 테스트
    def test_scenario_21_issue_report_url(self):
        # Given: 이슈 제보 버튼을 누를 때
        app = self._create_mocked_app()
        with patch("webbrowser.open") as mock_open:
            # When: _open_issue를 호출하면
            app._open_issue()
            # Then: 브라우저가 이슈 페이지 URL로 열려야 한다.
            called_url = mock_open.call_args[0][0]
            self.assertIn("issues", called_url)

    # 28. rclone 미설치 시 다운로드 문구 표시 테스트 (Scenario 28 복구)
    def test_scenario_28_rclone_download_label_when_missing(self):
        # Given: rclone 실행 파일이 시스템에 없을 때
        app = self._create_mocked_app()
        app._cfg["rclone_path"] = "C:\\non_existent\\rclone.exe"
        with patch("pathlib.Path.exists", return_value=False):
            # When: 존재 여부 체크 로직이 실행되면
            app._check_rclone_presence()
            # Then: UI 레이블이 'rclone 다운로드'로 변경되어야 한다.
            app._rc_ver_label.config.assert_called_with(text="rclone 다운로드", fg="#f38ba8")

    # 29. 창이 활성화될 때 rclone 존재 여부 재확인 테스트
    def test_scenario_29_check_rclone_on_focus(self):
        # Given: 프로그램이 실행 중일 때
        app = self._create_mocked_app()
        # When: 사용자가 프로그램 창을 활성화(FocusIn)하면
        with patch.object(app, "_check_rclone_presence") as mock_check:
            app._on_focus_in(None)
            # Then: rclone 존재 여부를 확인하는 함수가 호출되어야 한다.
            mock_check.assert_called_once()

if __name__ == "__main__":
    unittest.main()
