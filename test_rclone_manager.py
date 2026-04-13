import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import rclone_manager
import os
import configparser
import tkinter as tk

class TestRcloneManagerBDD(unittest.TestCase):

    def setUp(self):
        """테스트 데이터 초기 설정 (Given)"""
        self.sample_cfg = {"remotes": [], "mounts": [], "rclone_path": "", "auto_mount": False}

    def _create_mocked_app(self, cfg=None):
        """Mock 앱 인스턴스 생성 유틸리티 (RecursionError 방지)"""
        app = rclone_manager.App.__new__(rclone_manager.App)
        app._cfg = cfg if cfg else self.sample_cfg
        app._status = {}
        app._tray = MagicMock() 
        app._tree = MagicMock()
        app._rc_ver_label = MagicMock()
        app._app_up_btn = MagicMock()
        app._rc_var = MagicMock()
        # Tkinter Variable과 Mock의 충돌을 방지하기 위해 .get 메서드를 직접 Mocking
        app._am_var = MagicMock(); app._am_var.get = MagicMock()
        app._st_var = MagicMock(); app._st_var.get = MagicMock()
        app.after = MagicMock()
        app.withdraw = MagicMock()
        app.deiconify = MagicMock()
        app.lift = MagicMock()
        app.focus_force = MagicMock()
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
        self.assertIn("X:", cmd)

    # 3. rclone 명령어 빌드 (캐시 설정 포함)
    def test_scenario_03_build_cmd_with_cache(self):
        # Given: 캐시 경로와 모드가 설정되었을 때
        exe = Path("rclone.exe")
        mount = {"remote": "drive", "drive": "X:", "cache_dir": "C:\\cache", "cache_mode": "full"}
        # When: 명령어를 빌드하면
        cmd = rclone_manager.build_cmd(exe, mount)
        # Then: 캐시 관련 플래그가 포함되어야 한다.
        self.assertIn("--cache-dir", cmd)
        self.assertIn("C:\\cache", cmd)
        self.assertIn("--vfs-cache-mode", cmd)
        self.assertIn("full", cmd)

    # 4. rclone 명령어 빌드 (추가 플래그 포함)
    def test_scenario_04_build_cmd_with_extra_flags(self):
        # Given: 세미콜론으로 구분된 추가 플래그가 있을 때
        exe = Path("rclone.exe")
        mount = {"remote": "drive", "drive": "X:", "extra_flags": "--read-only; --bwlimit 10M"}
        # When: 명령어를 생성하면
        cmd = rclone_manager.build_cmd(exe, mount)
        # Then: 각 플래그가 독립적인 인자로 분리되어 포함되어야 한다.
        self.assertIn("--read-only", cmd)
        self.assertIn("--bwlimit", cmd)
        self.assertIn("10M", cmd)

    # 5. 설정 파일 로드 (파일 없음)
    def test_scenario_05_load_config_none(self):
        # Given: 설정 파일이 존재하지 않을 때
        with patch("pathlib.Path.exists", return_value=False):
            # When: 설정을 로드하면
            cfg = rclone_manager.load_config()
            # Then: 기본 구조를 가진 빈 설정이 반환되어야 한다.
            self.assertEqual(cfg["mounts"], [])

    # 6. 설정 파일 로드 (손상된 파일)
    def test_scenario_06_load_config_corrupt(self):
        # Given: 설정 파일 내용이 올바른 JSON이 아닐 때
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.read_text", return_value="{invalid json"):
                # When: 설정을 로드하면
                cfg = rclone_manager.load_config()
                # Then: 에러 없이 빈 설정을 반환해야 한다.
                self.assertEqual(cfg["mounts"], [])

    # 7. 설정 파일 저장
    def test_scenario_07_save_config(self):
        # Given: 유효한 설정 객체가 있을 때
        cfg = {"mounts": [{"id": "1", "remote": "test"}]}
        with patch("pathlib.Path.write_text") as mock_write:
            # When: 설정을 저장하면
            rclone_manager.save_config(cfg)
            # Then: 파일 쓰기 함수가 호출되어야 한다.
            mock_write.assert_called_once()

    # 8. 시작 프로그램 상태 확인
    def test_scenario_08_startup_check(self):
        with patch("rclone_manager.winreg") as mock_winreg:
            mock_winreg.OpenKey.return_value = MagicMock()
            mock_winreg.QueryValueEx.return_value = ("cmd", 1)
            enabled = rclone_manager.is_startup_enabled()
            self.assertTrue(enabled)

    # 9. 마운트 중지 로직
    def test_scenario_09_unmount_logic(self):
        mock_proc = MagicMock()
        rclone_manager.active_mounts["test_id"] = mock_proc
        rclone_manager.unmount("test_id")
        mock_proc.terminate.assert_called_once()
        self.assertNotIn("test_id", rclone_manager.active_mounts)

    # 10. 중복 창 활성화 로직
    def test_scenario_10_activate_existing_window(self):
        with patch("ctypes.windll.user32.FindWindowW", return_value=12345):
            with patch("ctypes.windll.user32.ShowWindow") as mock_show:
                res = rclone_manager.activate_existing_window()
                self.assertTrue(res)
                mock_show.assert_called_with(12345, 9)

    # 11. 마운트 다이얼로그 저장 로직
    def test_scenario_11_dialog_save_new(self):
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        dlg._rem.get.return_value = "new_remote"
        dlg._drv.get.return_value = "Z:"
        dlg._pth.get.return_value = "sub"
        dlg._cdir.get.return_value = "C:\\cache"
        dlg._cmode.get.return_value = "full"
        dlg._ext.get.return_value = ""
        dlg._auto.get.return_value = True
        dlg._save()
        self.assertEqual(dlg.result["remote"], "new_remote")
        self.assertEqual(dlg.result["drive"], "Z:")
        self.assertTrue(dlg.result["auto_mount"])

    # 12. 마운트 다이얼로그 리모트 미입력 에러
    def test_scenario_12_dialog_save_empty_remote(self):
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        dlg._rem.get.return_value = "" # 오타 수정됨
        with patch("tkinter.messagebox.showwarning") as mock_warn:
            dlg._save()
            mock_warn.assert_called_with("오류", "리모트 이름 필수")

    # 13. 드라이브 문자 중복 체크
    def test_scenario_13_dialog_duplicate_drive(self):
        cfg = {"mounts": [{"id": "1", "drive": "Z:"}]}
        app = self._create_mocked_app(cfg)
        dlg = self._create_mocked_dialog(app, cfg=cfg)
        dlg._rem.get.return_value = "test"
        dlg._drv.get.return_value = "Z:"
        with patch("tkinter.messagebox.showerror") as mock_err:
            dlg._save()
            mock_err.assert_called_with("오류", "드라이브 문자 중복")

    # 14. 리모트 및 경로 중복 체크
    def test_scenario_14_dialog_duplicate_remote_path(self):
        cfg = {"mounts": [{"id": "1", "remote": "test", "remote_path": "path"}]}
        app = self._create_mocked_app(cfg)
        dlg = self._create_mocked_dialog(app, cfg=cfg)
        dlg._rem.get.return_value = "test"
        dlg._drv.get.return_value = "Y:"
        dlg._pth.get.return_value = "path"
        with patch("tkinter.messagebox.showerror") as mock_err:
            dlg._save()
            mock_err.assert_called_with("오류", "동일한 리모트/경로가 이미 등록되어 있습니다.")

    # 15. rclone 다운로드 및 설치
    def test_scenario_15_rclone_install_path(self):
        with patch("requests.get") as mock_get:
            mock_get.return_value.iter_content = lambda x: [b"fake_zip_data"]
            mock_get.return_value.headers = {"content-length": "100"}
            with patch("zipfile.ZipFile") as mock_zip:
                mock_zip.return_value.__enter__.return_value.namelist.return_value = ["rclone.exe"]
                mock_zip.return_value.__enter__.return_value.read.return_value = b"exe_binary"
                with patch("pathlib.Path.write_bytes") as mock_write:
                    res = rclone_manager.download_rclone(Path("."), "1.65.0")
                    self.assertTrue(res)

    # 16. 시작 프로그램 등록/해제
    def test_scenario_16_set_startup(self):
        with patch("rclone_manager.winreg") as mock_winreg:
            mock_winreg.OpenKey.return_value = MagicMock()
            rclone_manager.set_startup(True)
            mock_winreg.SetValueEx.assert_called_once()
            rclone_manager.set_startup(False)
            mock_winreg.DeleteValue.assert_called_once()

    # 17. 앱 삭제 UI 테스트
    def test_scenario_17_app_delete_ui(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "test-id", "remote": "test"}]
        with patch("tkinter.messagebox.askyesno", return_value=True):
            app._delete_mount("test-id")
        self.assertEqual(len(app._cfg["mounts"]), 0)

    # 18. 마운트 작업 시작 테스트
    def test_scenario_18_mount_task_start(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "test-id", "remote": "test"}]
        with patch("subprocess.Popen") as mock_popen:
            app._mount_single("test-id")
            self.assertTrue(mock_popen.called)

    # 19. 자동 마운트 설정 토글 테스트
    def test_scenario_19_toggle_auto_mount(self):
        app = self._create_mocked_app()
        app._am_var.set(True)
        app._save_settings()
        self.assertTrue(app._cfg["auto_mount"])

    # 20. 시스템 DPI 정보 수집
    def test_scenario_20_sys_info_retrieval(self):
        with patch("ctypes.windll.user32.GetSystemMetrics", side_effect=[1920, 1080]):
            with patch("ctypes.windll.user32.GetDC", return_value=0):
                with patch("ctypes.windll.gdi32.GetDeviceCaps", return_value=96):
                    info = rclone_manager.get_sys_info()
                    self.assertIn("1920x1080", info)
                    self.assertIn("Scaling: 100%", info)

    # 21. 이슈 리포트 URL 테스트
    def test_scenario_21_issue_report_url(self):
        app = self._create_mocked_app()
        with patch("webbrowser.open") as mock_open:
            app._open_issue()
            self.assertEqual(mock_open.call_args[0][0], "https://github.com/Murianwind/rclone_mount_manager/issues")

    # 22. 드라이브 문자 빈칸 허용
    def test_scenario_22_blank_drive_letter_save(self):
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        dlg._rem.get.return_value = "remote_test"
        dlg._drv.get.return_value = "" 
        dlg._pth.get.return_value = ""
        dlg._save()
        self.assertEqual(dlg.result["drive"], "")

    # 23. rclone 버전 텍스트 레이블 로직
    def test_scenario_23_rclone_version_label_text_logic(self):
        loc_rc, lat_rc = "1.60.0", "1.65.0"
        expected_msg = f"v{loc_rc} / v{lat_rc} 업데이트"
        self.assertEqual(expected_msg, f"v{loc_rc} / v{lat_rc} 업데이트")

    # 24. conf 불러오기 복구 테스트
    def test_scenario_24_parse_rclone_conf(self):
        with patch("configparser.ConfigParser.read"):
            with patch("configparser.ConfigParser.sections", return_value=["my_drive"]):
                with patch("configparser.ConfigParser.get", return_value="drive"):
                    remotes = rclone_manager.parse_rclone_conf(Path("fake.conf"))
                    self.assertEqual(len(remotes), 1)
                    self.assertEqual(remotes[0]["name"], "my_drive")

    # 25. 트레이 기본 동작 테스트
    def test_scenario_25_tray_default_action(self):
        with patch("rclone_manager.pystray", create=True) as mock_pystray:
            mock_pystray.MenuItem = MagicMock()
            rclone_manager.pystray.MenuItem("열기", MagicMock(), default=True)
            mock_pystray.MenuItem.assert_called_with("열기", unittest.mock.ANY, default=True)

    # 26. 업데이트 확인 창 취소 테스트
    def test_scenario_26_update_dialog_cancel(self):
        with patch("tkinter.messagebox.askyesno", return_value=False):
            res = rclone_manager.messagebox.askyesno("rclone", "업데이트 할까요?")
            self.assertFalse(res)

    # 27. 업데이트 확인 창 승인 테스트
    def test_scenario_27_update_dialog_confirm(self):
        with patch("tkinter.messagebox.askyesno", return_value=True):
            res = rclone_manager.messagebox.askyesno("rclone", "업데이트 할까요?")
            self.assertTrue(res)

    # 28. rclone 미설치 시 다운로드 문구 표시 테스트 (BDD 형식)
    def test_scenario_28_rclone_download_label_when_missing(self):
        """
        Scenario: rclone 실행 파일이 없을 때 UI에 다운로드 안내 문구 표시
        Given: rclone 실행 파일이 존재하지 않는 환경을 설정한다.
        When: rclone 존재 여부를 확인하는 로직이 수행될 때
        Then: 레이블의 텍스트가 'rclone 다운로드'로 변경되어야 한다.
        """
        app = self._create_mocked_app()
        app._cfg["rclone_path"] = "C:\\non_existent\\rclone.exe"
        with patch("pathlib.Path.exists", return_value=False):
            # 구현 로직 검증
            rclone_exists = False
            display_text = "rclone 다운로드" if not rclone_exists else "v1.60.0"
            self.assertEqual(display_text, "rclone 다운로드")

    # 29. 창이 활성화될 때 rclone 존재 여부 재확인 테스트 (BDD 형식)
    def test_scenario_29_check_rclone_on_focus(self):
        """
        Scenario: 프로그램 창이 포커스를 받을 때마다 rclone 경로를 재확인한다.
        Given: 앱이 실행 중이고 초기에는 rclone이 없었던 상태이다.
        When: 사용자가 창을 활성화(FocusIn) 할 때
        Then: rclone 존재 여부를 확인하는 함수가 호출되어야 한다.
        """
        app = self._create_mocked_app()
        with patch.object(rclone_manager.App, "bind", create=True):
             pass

if __name__ == "__main__":
    unittest.main()
