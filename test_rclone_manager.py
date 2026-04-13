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
        """Mock 앱 인스턴스 생성 유틸리티 (테스트 멈춤 방지 및 RecursionError 방지)"""
        app = rclone_manager.App.__new__(rclone_manager.App)
        app._cfg = cfg if cfg else self.sample_cfg
        app._status = {}
        app._tray = MagicMock() 
        app._tree = MagicMock()
        app._rc_ver_label = MagicMock()
        app._app_up_btn = MagicMock()
        app._rc_var = MagicMock()
        app._am_var = MagicMock(); app._am_var.get = MagicMock()
        app._st_var = MagicMock(); app._st_var.get = MagicMock()
        
        # 테스트 중 블로킹 방지를 위한 Mocking
        app.after = MagicMock()
        app.wait_window = MagicMock() 
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

    # 1~27번 기존 시나리오 (사용자 제공 코드와 100% 동일하게 유지)
    def test_scenario_01_load_rclone(self):
        cfg = {"rclone_path": "C:\\fake\\rclone.exe"}
        with patch("pathlib.Path.exists", return_value=True):
            exe = rclone_manager.get_rclone_exe(cfg)
            self.assertEqual(str(exe), "C:\\fake\\rclone.exe")

    def test_scenario_02_build_cmd_basic(self):
        exe = Path("rclone.exe")
        mount = {"remote": "drive", "drive": "X:", "remote_path": "data"}
        cmd = rclone_manager.build_cmd(exe, mount)
        self.assertIn("mount", cmd)
        self.assertIn("drive:data", cmd)
        self.assertIn("X:", cmd)

    def test_scenario_03_build_cmd_with_cache(self):
        exe = Path("rclone.exe")
        mount = {"remote": "drive", "drive": "X:", "cache_dir": "C:\\cache", "cache_mode": "full"}
        cmd = rclone_manager.build_cmd(exe, mount)
        self.assertIn("--cache-dir", cmd)
        self.assertIn("C:\\cache", cmd)
        self.assertIn("--vfs-cache-mode", cmd)
        self.assertIn("full", cmd)

    def test_scenario_04_build_cmd_with_extra_flags(self):
        exe = Path("rclone.exe")
        mount = {"remote": "drive", "drive": "X:", "extra_flags": "--read-only; --bwlimit 10M"}
        cmd = rclone_manager.build_cmd(exe, mount)
        self.assertIn("--read-only", cmd)
        self.assertIn("--bwlimit", cmd)
        self.assertIn("10M", cmd)

    def test_scenario_05_load_config_none(self):
        with patch("pathlib.Path.exists", return_value=False):
            cfg = rclone_manager.load_config()
            self.assertEqual(cfg["mounts"], [])

    def test_scenario_06_load_config_corrupt(self):
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value="{invalid json"):
            cfg = rclone_manager.load_config()
            self.assertEqual(cfg["mounts"], [])

    def test_scenario_07_save_config(self):
        cfg = {"mounts": [{"id": "1", "remote": "test"}]}
        with patch("pathlib.Path.write_text") as mock_write:
            rclone_manager.save_config(cfg)
            mock_write.assert_called_once()

    def test_scenario_08_startup_check(self):
        with patch("rclone_manager.winreg") as mock_winreg:
            mock_winreg.OpenKey.return_value = MagicMock()
            mock_winreg.QueryValueEx.return_value = ("cmd", 1)
            enabled = rclone_manager.is_startup_enabled()
            self.assertTrue(enabled)

    def test_scenario_09_unmount_logic(self):
        mock_proc = MagicMock()
        rclone_manager.active_mounts["test_id"] = mock_proc
        rclone_manager.unmount("test_id")
        mock_proc.terminate.assert_called_once()
        self.assertNotIn("test_id", rclone_manager.active_mounts)

    def test_scenario_10_activate_existing_window(self):
        with patch("ctypes.windll.user32.FindWindowW", return_value=12345), \
             patch("ctypes.windll.user32.ShowWindow") as mock_show:
            res = rclone_manager.activate_existing_window()
            self.assertTrue(res)
            mock_show.assert_called_with(12345, 9)

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

    def test_scenario_12_dialog_save_empty_remote(self):
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        dlg._rem.get.get.return_value = ""
        with patch("tkinter.messagebox.showwarning") as mock_warn:
            dlg._save()
            mock_warn.assert_called_with("오류", "리모트 이름 필수")

    def test_scenario_13_dialog_duplicate_drive(self):
        cfg = {"mounts": [{"id": "1", "drive": "Z:"}]}
        app = self._create_mocked_app(cfg)
        dlg = self._create_mocked_dialog(app, cfg=cfg)
        dlg._rem.get.return_value = "test"
        dlg._drv.get.return_value = "Z:"
        with patch("tkinter.messagebox.showerror") as mock_err:
            dlg._save()
            mock_err.assert_called_with("오류", "드라이브 문자 중복")

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

    def test_scenario_15_rclone_install_path(self):
        with patch("requests.get") as mock_get, \
             patch("zipfile.ZipFile") as mock_zip, \
             patch("pathlib.Path.write_bytes") as mock_write:
            mock_get.return_value.iter_content = lambda x: [b"fake_zip_data"]
            mock_get.return_value.headers = {"content-length": "100"}
            mock_zip.return_value.__enter__.return_value.namelist.return_value = ["rclone.exe"]
            mock_zip.return_value.__enter__.return_value.read.return_value = b"exe_binary"
            res = rclone_manager.download_rclone(Path("."), "1.65.0")
            self.assertTrue(res)

    def test_scenario_16_set_startup(self):
        with patch("rclone_manager.winreg") as mock_winreg:
            mock_winreg.OpenKey.return_value = MagicMock()
            rclone_manager.set_startup(True)
            mock_winreg.SetValueEx.assert_called_once()
            rclone_manager.set_startup(False)
            mock_winreg.DeleteValue.assert_called_once()

    def test_scenario_17_app_delete_ui(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "test-id", "remote": "test"}]
        with patch("tkinter.messagebox.askyesno", return_value=True):
            app._delete_mount("test-id")
        self.assertEqual(len(app._cfg["mounts"]), 0)

    def test_scenario_18_mount_task_start(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "test-id", "remote": "test"}]
        with patch("subprocess.Popen") as mock_popen:
            app._mount_single("test-id")
            self.assertTrue(mock_popen.called)

    def test_scenario_19_toggle_auto_mount(self):
        app = self._create_mocked_app()
        app._am_var.set(True)
        app._save_settings()
        self.assertTrue(app._cfg["auto_mount"])

    def test_scenario_20_sys_info_retrieval(self):
        with patch("ctypes.windll.user32.GetSystemMetrics", side_effect=[1920, 1080]), \
             patch("ctypes.windll.user32.GetDC", return_value=0), \
             patch("ctypes.windll.gdi32.GetDeviceCaps", return_value=96):
            info = rclone_manager.get_sys_info()
            self.assertIn("1920x1080", info)
            self.assertIn("Scaling: 100%", info)

    def test_scenario_21_issue_report_url(self):
        app = self._create_mocked_app()
        with patch("webbrowser.open") as mock_open:
            app._open_issue()
            self.assertEqual(mock_open.call_args[0][0], f"https://github.com/{rclone_manager.GITHUB_REPO}/issues")

    def test_scenario_22_blank_drive_letter_save(self):
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        dlg._rem.get.return_value = "remote_test"
        dlg._drv.get.return_value = "" 
        dlg._pth.get.return_value = ""
        dlg._save()
        self.assertEqual(dlg.result["drive"], "")

    def test_scenario_23_rclone_version_label_text_logic(self):
        loc_rc, lat_rc = "1.60.0", "1.65.0"
        expected_msg = f"v{loc_rc} / v{lat_rc} 업데이트"
        self.assertEqual(expected_msg, f"v{loc_rc} / v{lat_rc} 업데이트")

    def test_scenario_24_parse_rclone_conf(self):
        with patch("configparser.ConfigParser.read"), \
             patch("configparser.ConfigParser.sections", return_value=["my_drive"]), \
             patch("configparser.ConfigParser.get", return_value="drive"):
            remotes = rclone_manager.parse_rclone_conf(Path("fake.conf"))
            self.assertEqual(len(remotes), 1)
            self.assertEqual(remotes[0]["name"], "my_drive")

    def test_scenario_25_tray_default_action(self):
        with patch("rclone_manager.pystray", create=True) as mock_pystray:
            mock_pystray.MenuItem = MagicMock()
            rclone_manager.pystray.MenuItem("열기", MagicMock(), default=True)
            mock_pystray.MenuItem.assert_called_with("열기", unittest.mock.ANY, default=True)

    def test_scenario_26_update_dialog_cancel(self):
        with patch("tkinter.messagebox.askyesno", return_value=False):
            res = rclone_manager.messagebox.askyesno("rclone", "업데이트 할까요?")
            self.assertFalse(res)

    def test_scenario_27_update_dialog_confirm(self):
        with patch("tkinter.messagebox.askyesno", return_value=True):
            res = rclone_manager.messagebox.askyesno("rclone", "업데이트 할까요?")
            self.assertTrue(res)

    # ── [신규 요구사항 반영 테스트] ──

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
            # rclone이 없는 경우의 UI 텍스트 업데이트 로직 검증을 위해 함수 호출
            # 실제 구현된 _check_rclone_presence 메서드를 테스트
            app._check_rclone_presence()
            app._rc_ver_label.config.assert_called_with(text="rclone 다운로드", fg="#f38ba8")

    def test_scenario_29_check_rclone_on_focus(self):
        """
        Scenario: 프로그램 창이 포커스를 받을 때마다 rclone 경로를 재확인한다.
        Given: 앱이 실행 중이고 초기에는 rclone이 없었던 상태이다.
        When: 사용자가 창을 클릭하거나 창이 활성화(FocusIn)될 때
        Then: rclone 존재 여부를 확인하는 함수가 호출되어야 한다.
        """
        # App 클래스 초기화 시 바인딩이 일어나는지 확인
        with patch("rclone_manager.App.bind") as mock_bind:
            # __init__이 호출되도록 인스턴스 정상 생성 (Mocking 환경 내에서)
            with patch("rclone_manager.load_config"), \
                 patch("rclone_manager.App._build_ui"), \
                 patch("rclone_manager.App._refresh_list"), \
                 patch("rclone_manager.App._start_tray"), \
                 patch("rclone_manager.App._check_versions_async"):
                app = rclone_manager.App()
                # Then: <FocusIn> 이벤트가 _check_rclone_presence에 바인딩되었는지 확인
                # 호출 인자 중 하나가 "<FocusIn>"인지 검증
                bind_calls = [call[0][0] for call in mock_bind.call_args_list]
                self.assertIn("<FocusIn>", bind_calls)

if __name__ == "__main__":
    unittest.main()
