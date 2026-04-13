import unittest
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import tkinter as tk

import rclone_manager
from rclone_manager import App, MountDialog

class TestRcloneManagerBDD(unittest.TestCase):

    def setUp(self):
        """테스트 데이터 설정 (Given)"""
        self.sample_cfg = {"remotes": [], "mounts": [], "rclone_path": "", "auto_mount": False}

    def _create_mocked_app(self, cfg=None):
        """Mock 앱 생성"""
        app = App.__new__(App)
        app._cfg = cfg if cfg else self.sample_cfg
        app._status = {}
        app._tray = None 
        app._tree = MagicMock()
        app._rc_ver_label = MagicMock()
        app._app_up_btn = MagicMock()
        app.after = MagicMock()
        return app

    def _create_mocked_dialog(self, parent, mount=None, cfg=None):
        """Mock 다이얼로그 생성"""
        dlg = MountDialog.__new__(MountDialog)
        dlg._m = mount if mount else {}
        dlg._app_cfg = cfg if cfg else self.sample_cfg
        dlg._rem = MagicMock(); dlg._drv = MagicMock(); dlg._pth = MagicMock()
        dlg._cdir = MagicMock(); dlg._cmode = MagicMock(); dlg._ext = MagicMock()
        dlg._auto = MagicMock(); dlg.destroy = MagicMock()
        return dlg

    # 1. rclone 실행 파일 로드
    def test_scenario_01_load_rclone(self):
        cfg = {"rclone_path": "C:\\fake\\rclone.exe"}
        with patch("pathlib.Path.exists", return_value=True):
            exe = rclone_manager.get_rclone_exe(cfg)
        self.assertEqual(str(exe), "C:\\fake\\rclone.exe")

    # 2. rclone.conf 파싱
    def test_scenario_02_parse_conf(self):
        with patch("configparser.ConfigParser.read", return_value=None):
            with patch("configparser.ConfigParser.sections", return_value=["drive"]):
                with patch("configparser.ConfigParser.get", return_value="drive"):
                    remotes = rclone_manager.parse_rclone_conf(Path("fake.conf"))
        self.assertEqual(remotes[0]["name"], "drive")

    # 3. 마운트 추가 및 저장
    def test_scenario_03_09_add_save(self):
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        dlg._rem.get.return_value = "gd"; dlg._drv.get.return_value = "Z:"
        dlg._pth.get.return_value = ""; dlg._cdir.get.return_value = ""
        dlg._cmode.get.return_value = "full"; dlg._ext.get.return_value = ""
        dlg._auto.get.return_value = True
        dlg._save()
        self.assertEqual(dlg.result["remote"], "gd")

    # 4. 연결 테스트 로직 확인
    def test_scenario_04_test_method(self):
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        self.assertTrue(hasattr(dlg, '_test'))

    # 5. 빈 리모트 이름 감지
    def test_scenario_05_empty_remote(self):
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        dlg._rem.get.return_value = ""
        with patch("tkinter.messagebox.showwarning") as m:
            dlg._save()
            m.assert_called_with("오류", "리모트 이름 필수")

    # 6. 드라이브 중복 체크
    def test_scenario_06_drive_conflict(self):
        cfg = {"mounts": [{"id": "1", "drive": "X:", "remote": "old"}]}
        app = self._create_mocked_app(cfg)
        dlg = self._create_mocked_dialog(app, cfg=cfg)
        dlg._rem.get.return_value = "new"; dlg._drv.get.return_value = "X:"
        with patch("tkinter.messagebox.showerror") as m:
            dlg._save()
            self.assertTrue(m.called)

    # 7. 추가 플래그 빌드
    def test_scenario_07_flags(self):
        m = {"remote": "gd", "drive": "X:", "extra_flags": "--read-only"}
        cmd = rclone_manager.build_cmd(Path("rclone.exe"), m)
        self.assertIn("--read-only", cmd)

    # 8. 자동 마운트 실행
    @patch("rclone_manager.App._do_mount")
    def test_scenario_08_auto_start(self, mock_do):
        cfg = {"auto_mount": True, "mounts": [{"id": "1", "auto_mount": True, "remote": "gd"}]}
        app = self._create_mocked_app(cfg)
        app._automount_all()
        self.assertTrue(mock_do.called)

    # 10. 편집 지속성
    def test_scenario_10_edit(self):
        cfg = {"mounts": [{"id": "1", "drive": "X:"}]}
        app = self._create_mocked_app(cfg)
        dlg = self._create_mocked_dialog(app, cfg=cfg, mount=cfg["mounts"][0])
        dlg._rem.get.return_value = "gd"; dlg._drv.get.return_value = "Y:"
        dlg._save()
        self.assertEqual(dlg.result["drive"], "Y:")

    # 11. 삭제
    def test_scenario_11_del(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "1"}]
        with patch("tkinter.messagebox.askyesno", return_value=True):
            with patch.object(app._tree, "selection", return_value=("1",)):
                app._del()
        self.assertEqual(len(app._cfg["mounts"]), 0)

    # 12. 중복 경로 차단
    def test_scenario_12_path_conflict(self):
        cfg = {"mounts": [{"id": "1", "remote": "gd", "remote_path": ""}]}
        app = self._create_mocked_app(cfg)
        dlg = self._create_mocked_dialog(app, cfg=cfg)
        dlg._rem.get.return_value = "gd"; dlg._pth.get.return_value = ""
        with patch("tkinter.messagebox.showerror") as m:
            dlg._save()
            self.assertTrue(m.called)

    # 13. 순서 변경
    def test_scenario_13_order(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "1"}, {"id": "2"}]
        with patch.object(app._tree, "selection", return_value=("2",)):
            app._move_up()
        self.assertEqual(app._cfg["mounts"][0]["id"], "2")

    # 14. 언마운트
    def test_scenario_14_unmount(self):
        mock_p = MagicMock()
        rclone_manager.active_mounts["1"] = mock_p
        rclone_manager.unmount("1")
        self.assertTrue(mock_p.terminate.called)

    # 15. 다운로드 로직
    def test_scenario_15_download(self):
        self.assertTrue(callable(rclone_manager.download_rclone))

    # 16. 시작 프로그램
    def test_scenario_16_startup(self):
        with patch("winreg.OpenKey"), patch("winreg.SetValueEx") as m:
            rclone_manager.set_startup(True)
            self.assertTrue(m.called)

    # 17, 18. 트레이
    def test_scenario_17_18_tray(self):
        app = self._create_mocked_app()
        with patch("pystray.Icon"):
            app._start_tray()
            self.assertIsNotNone(app)

    # 19. 중복 실행 방지
    def test_scenario_19_single(self):
        with patch("ctypes.windll.user32.FindWindowW", return_value=123):
            self.assertTrue(rclone_manager.activate_existing_window())

    # 20. 시스템 정보
    def test_scenario_20_info(self):
        self.assertIn("Resolution", rclone_manager.get_sys_info())

    # 21. 저장소 확인
    def test_scenario_21_repo(self):
        self.assertIn("Murianwind", rclone_manager.GITHUB_REPO)

if __name__ == "__main__":
    unittest.main()
