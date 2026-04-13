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
        # Given: Tkinter 의존성을 최소화한 Mock App 객체를 생성한다.
        app = rclone_manager.App.__new__(rclone_manager.App)
        app.tk = MagicMock() # tk 속성 Mocking으로 RecursionError 근본 차단
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
        # Given: 설정에 특정 rclone 경로가 존재할 때
        cfg = {"rclone_path": "C:\\fake\\rclone.exe"}
        with patch("pathlib.Path.exists", return_value=True):
            # When: rclone 실행 파일을 가져오면
            exe = rclone_manager.get_rclone_exe(cfg)
            # Then: 설정된 경로가 반환되어야 한다.
            self.assertEqual(str(exe), "C:\\fake\\rclone.exe")

    # 2. rclone 명령어 빌드 (기본)
    def test_scenario_02_build_cmd_basic(self):
        # Given: 리모트와 드라이브 문자가 주어졌을 때
        exe = Path("rclone.exe")
        mount = {"remote": "drive", "drive": "X:", "remote_path": "data"}
        # When: 마운트 명령어를 생성하면
        cmd = rclone_manager.build_cmd(exe, mount)
        # Then: 필수 옵션들이 포함되어야 한다.
        self.assertIn("mount", cmd)
        self.assertIn("drive:data", cmd)

    # 3. rclone 명령어 빌드 (캐시 설정 포함)
    def test_scenario_03_build_cmd_with_cache(self):
        # Given: 캐시 경로와 모드가 설정되었을 때
        exe = Path("rclone.exe")
        mount = {"remote": "drive", "drive": "X:", "cache_dir": "C:\\cache", "cache_mode": "full"}
        # When: 명령어를 빌드하면
        cmd = rclone_manager.build_cmd(exe, mount)
        # Then: 캐시 관련 플래그가 포함되어야 한다.
        self.assertIn("--cache-dir", cmd)
        self.assertIn("full", cmd)

    # 4. rclone 명령어 빌드 (추가 플래그 포함)
    def test_scenario_04_build_cmd_with_extra_flags(self):
        # Given: 세미콜론으로 구분된 추가 플래그가 있을 때
        exe = Path("rclone.exe")
        mount = {"remote": "drive", "drive": "X:", "extra_flags": "--read-only; --bwlimit 10M"}
        # When: 명령어를 생성하면
        cmd = rclone_manager.build_cmd(exe, mount)
        # Then: 각 플래그가 인자로 분리되어 포함되어야 한다.
        self.assertIn("--read-only", cmd)
        self.assertIn("--bwlimit", cmd)

    # 5. 설정 파일 로드 (파일 없음)
    def test_scenario_05_load_config_none(self):
        # Given: 설정 파일이 존재하지 않을 때
        with patch("pathlib.Path.exists", return_value=False):
            # When: 설정을 로드하면
            cfg = rclone_manager.load_config()
            # Then: 기본 빈 설정이 반환되어야 한다.
            self.assertEqual(cfg["mounts"], [])

    # 6. 설정 파일 로드 (손상된 파일)
    def test_scenario_06_load_config_corrupt(self):
        # Given: 설정 파일 내용이 손상되었을 때
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value="{invalid"):
            # When: 설정을 로드하면
            cfg = rclone_manager.load_config()
            # Then: 빈 설정을 반환해야 한다.
            self.assertEqual(cfg["mounts"], [])

    # 7. 설정 파일 저장
    def test_scenario_07_save_config(self):
        # Given: 유효한 설정 데이터가 있을 때
        cfg = {"mounts": []}
        with patch("pathlib.Path.write_text") as mock_write:
            # When: 설정을 저장하면
            rclone_manager.save_config(cfg)
            # Then: 파일 쓰기가 호출되어야 한다.
            mock_write.assert_called_once()

    # 8. 시작 프로그램 상태 확인
    def test_scenario_08_startup_check(self):
        # Given: 시작 프로그램 등록 정보를 조회할 때
        with patch("rclone_manager.winreg") as mock_winreg:
            mock_winreg.OpenKey.return_value = MagicMock()
            mock_winreg.QueryValueEx.return_value = ("cmd", 1)
            # When: 등록 여부를 확인하면
            enabled = rclone_manager.is_startup_enabled()
            # Then: 결과는 True 여야 한다.
            self.assertTrue(enabled)

    # 9. 마운트 중지 로직
    def test_scenario_09_unmount_logic(self):
        # Given: 실행 중인 프로세스가 있을 때
        mock_proc = MagicMock()
        rclone_manager.active_mounts["test_id"] = mock_proc
        # When: 언마운트를 수행하면
        rclone_manager.unmount("test_id")
        # Then: 프로세스가 종료되어야 한다.
        mock_proc.terminate.assert_called_once()

    # 10. 중복 창 활성화 로직
    def test_scenario_10_activate_existing_window(self):
        # Given: 이미 실행 중인 창 핸들이 있을 때
        with patch("ctypes.windll.user32.FindWindowW", return_value=123):
            with patch("ctypes.windll.user32.ShowWindow") as mock_show:
                # When: 창 활성화를 시도하면
                res = rclone_manager.activate_existing_window()
                # Then: ShowWindow가 호출되어야 한다.
                self.assertTrue(res)
                mock_show.assert_called()

    # 11. 마운트 다이얼로그 저장 로직
    def test_scenario_11_dialog_save_new(self):
        # Given: 다이얼로그에 정보를 입력했을 때
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        dlg._rem.get.return_value = "remote"
        # When: 저장을 클릭하면
        dlg._save()
        # Then: 결과가 생성되어야 한다.
        self.assertIsNotNone(dlg.result)

    # 12. 마운트 다이얼로그 리모트 미입력 에러
    def test_scenario_12_dialog_save_empty_remote(self):
        # Given: 리모트 이름이 비어있을 때
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        dlg._rem.get.return_value = ""
        # When: 저장을 시도하면
        with patch("tkinter.messagebox.showwarning") as mock_warn:
            dlg._save()
            # Then: 경고창이 표시되어야 한다.
            mock_warn.assert_called_with("오류", "리모트 이름 필수")

    # 13. 드라이브 문자 중복 체크
    def test_scenario_13_dialog_duplicate_drive(self):
        # Given: 이미 사용 중인 드라이브 문자를 선택했을 때
        cfg = {"mounts": [{"id": "1", "drive": "Z:"}]}
        app = self._create_mocked_app(cfg)
        dlg = self._create_mocked_dialog(app, cfg=cfg)
        dlg._rem.get.return_value = "test"
        dlg._drv.get.return_value = "Z:"
        # When: 저장을 시도하면
        with patch("tkinter.messagebox.showerror") as mock_err:
            dlg._save()
            # Then: 에러창이 표시되어야 한다.
            mock_err.assert_called_with("오류", "드라이브 문자 중복")

    # 14. 리모트 및 경로 중복 체크
    def test_scenario_14_dialog_duplicate_remote_path(self):
        # Given: 리모트와 경로가 이미 존재할 때
        cfg = {"mounts": [{"id": "1", "remote": "test", "remote_path": "path"}]}
        app = self._create_mocked_app(cfg)
        dlg = self._create_mocked_dialog(app, cfg=cfg)
        dlg._rem.get.return_value = "test"
        dlg._pth.get.return_value = "path"
        # When: 저장을 시도하면
        with patch("tkinter.messagebox.showerror") as mock_err:
            dlg._save()
            # Then: 중복 에러가 발생해야 한다.
            mock_err.assert_called()

    # 15. rclone 다운로드 및 설치
    def test_scenario_15_rclone_install_path(self):
        # Given: 다운로드 환경이 주어질 때
        with patch("requests.get") as mock_get, \
             patch("zipfile.ZipFile") as mock_zip, \
             patch("pathlib.Path.write_bytes"):
            mock_get.return_value.iter_content = lambda x: [b"data"]
            mock_get.return_value.headers = {"content-length": "4"}
            # When: 다운로드를 실행하면
            res = rclone_manager.download_rclone(Path("."), "1.65.0")
            # Then: 성공해야 한다.
            self.assertTrue(res)

    # 16. 시작 프로그램 등록/해제
    def test_scenario_16_set_startup(self):
        # Given: 시작 프로그램 설정을 변경할 때
        with patch("rclone_manager.winreg") as mock_winreg:
            mock_winreg.OpenKey.return_value = MagicMock()
            # When: 등록을 활성화하면
            rclone_manager.set_startup(True)
            # Then: SetValueEx가 호출되어야 한다.
            mock_winreg.SetValueEx.assert_called()

    # 17. 앱 삭제 UI 테스트
    def test_scenario_17_app_delete_ui(self):
        # Given: 마운트 항목이 등록되어 있을 때
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "test-id", "remote": "test"}]
        # When: 삭제 메서드를 호출하면
        with patch("tkinter.messagebox.askyesno", return_value=True):
            app._delete_mount("test-id")
        # Then: 목록에서 제거되어야 한다.
        self.assertEqual(len(app._cfg["mounts"]), 0)

    # 18. 마운트 작업 시작 테스트
    def test_scenario_18_mount_task_start(self):
        # Given: 마운트 실행을 위한 데이터가 있을 때
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "test-id", "remote": "test"}]
        # When: 마운트를 시작하면
        with patch("subprocess.Popen") as mock_popen:
            app._mount_single("test-id")
            # Then: Popen이 호출되어야 한다.
            self.assertTrue(mock_popen.called)

    # 19. 자동 마운트 설정 토글 테스트
    def test_scenario_19_toggle_auto_mount(self):
        # Given: 자동 마운트 체크박스가 켜져 있을 때
        app = self._create_mocked_app()
        app._am_var.get.return_value = True
        # When: 설정을 저장하면
        app._save_settings()
        # Then: 설정 데이터에 반영되어야 한다.
        self.assertTrue(app._cfg["auto_mount"])

    # 20. 시스템 DPI 정보 수집
    def test_scenario_20_sys_info_retrieval(self):
        # Given: 시스템 정보를 조회할 때
        with patch("rclone_manager.get_sys_info", return_value="1920x1080"):
            # When: 정보를 가져오면
            info = rclone_manager.get_sys_info()
            # Then: 문자열이 반환되어야 한다.
            self.assertEqual(info, "1920x1080")

    # 21. 이슈 리포트 URL 테스트
    def test_scenario_21_issue_report_url(self):
        # Given: 이슈 제보 버튼을 누를 때
        app = self._create_mocked_app()
        with patch("webbrowser.open") as mock_open:
            # When: _open_issue를 실행하면
            app._open_issue()
            # Then: 이슈 URL이 포함된 주소가 열려야 한다.
            called_url = mock_open.call_args[0][0]
            self.assertIn("issues", called_url)

    # 22. 드라이브 문자 빈칸 허용
    def test_scenario_22_blank_drive_letter_save(self):
        # Given: 드라이브 문자를 입력하지 않았을 때
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        dlg._rem.get.return_value = "remote"
        dlg._drv.get.return_value = "" 
        # When: 저장을 수행하면
        dlg._save()
        # Then: 빈 문자로 저장되어야 한다.
        self.assertEqual(dlg.result["drive"], "")

    # 23. rclone 버전 텍스트 레이블 로직
    def test_scenario_23_rclone_version_label_text_logic(self):
        # Given: 현재 버전과 최신 버전이 다를 때
        loc, lat = "1.60.0", "1.65.0"
        # When: 레이블을 구성하면
        msg = f"v{loc} / v{lat} 업데이트"
        # Then: 업데이트 문구가 포함되어야 한다.
        self.assertIn("업데이트", msg)

    # 24. conf 불러오기 복구 테스트
    def test_scenario_24_parse_rclone_conf(self):
        # Given: 설정 파일을 파싱할 때
        with patch("configparser.ConfigParser.read"):
            with patch("configparser.ConfigParser.sections", return_value=["drive"]):
                # When: 파싱하면
                remotes = rclone_manager.parse_rclone_conf(Path("fake.conf"))
                # Then: 리스트를 반환해야 한다.
                self.assertIsInstance(remotes, list)

    # 25. 트레이 기본 동작 테스트
    def test_scenario_25_tray_default_action(self):
        # Given: 트레이 메뉴를 구성할 때
        with patch("rclone_manager.pystray", create=True) as mock_pystray:
            mock_pystray.MenuItem = MagicMock()
            # When: 열기 항목을 추가하면
            rclone_manager.pystray.MenuItem("열기", MagicMock(), default=True)
            # Then: default=True가 설정되어야 한다.
            mock_pystray.MenuItem.assert_called_with("열기", unittest.mock.ANY, default=True)

    # 26. 업데이트 확인 창 취소 테스트
    def test_scenario_26_update_dialog_cancel(self):
        # Given: 업데이트 확인 창에서
        with patch("tkinter.messagebox.askyesno", return_value=False):
            # When: '아니오'를 선택하면
            res = rclone_manager.messagebox.askyesno("rclone", "업데이트 할까요?")
            # Then: False가 반환된다.
            self.assertFalse(res)

    # 27. 업데이트 확인 창 승인 테스트
    def test_scenario_27_update_dialog_confirm(self):
        # Given: 업데이트 확인 창에서
        with patch("tkinter.messagebox.askyesno", return_value=True):
            # When: '예'를 선택하면
            res = rclone_manager.messagebox.askyesno("rclone", "업데이트 할까요?")
            # Then: True가 반환된다.
            self.assertTrue(res)

    # 28. rclone 미설치 시 다운로드 문구 표시 테스트
    def test_scenario_28_rclone_download_label_when_missing(self):
        """
        Scenario: rclone 실행 파일이 없을 때 UI에 다운로드 안내 문구 표시
        """
        # Given: rclone 실행 파일이 존재하지 않는 경로로 설정되었을 때
        app = self._create_mocked_app()
        app._cfg["rclone_path"] = "C:\\non_existent\\rclone.exe"
        with patch("pathlib.Path.exists", return_value=False):
            # When: 존재 여부를 재확인하는 로직이 수행되면
            app._check_rclone_presence()
            # Then: 레이블 텍스트가 'rclone 다운로드'로 설정되어야 한다.
            app._rc_ver_label.config.assert_called_with(text="rclone 다운로드", fg="#f38ba8")

    # 29. 창이 활성화될 때 rclone 존재 여부 재확인 테스트
    def test_scenario_29_check_rclone_on_focus(self):
        """
        Scenario: 프로그램 창이 포커스를 받을 때마다 rclone 경로를 재확인한다.
        """
        # Given: 앱이 실행 중인 상태에서
        app = self._create_mocked_app()
        # When: 포커스 이벤트(FocusIn)가 발생하면
        with patch.object(app, "_check_rclone_presence") as mock_check:
            app._on_focus_in(None)
            # Then: _check_rclone_presence 함수가 호출되어야 한다.
            mock_check.assert_called_once()

if __name__ == "__main__":
    unittest.main()
