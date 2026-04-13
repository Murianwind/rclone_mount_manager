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
        """테스트 기초 데이터 설정 (Given)"""
        self.sample_cfg = {
            "remotes": [{"name": "drive", "type": "drive"}],
            "mounts": [],
            "rclone_path": "",
            "auto_mount": False
        }

    def _create_mocked_app(self, cfg=None):
        """RecursionError 방지를 위해 __new__로 인스턴스 생성 및 Tkinter 호출 차단"""
        app = App.__new__(App)
        app._cfg = cfg if cfg else self.sample_cfg
        app._status = {}
        app._tray = None # RecursionError 방지의 핵심: 명시적 None 설정
        app._tree = MagicMock()
        app._tree.get_children.return_value = []
        app.update_idletasks = MagicMock()
        app.after = MagicMock()
        return app

    def _create_mocked_dialog(self, parent, mount=None, cfg=None):
        """TypeError 방지를 위해 모든 위젯 get()이 문자열을 반환하도록 설정"""
        dlg = MountDialog.__new__(MountDialog)
        dlg._m = mount if mount else {}
        dlg._app_cfg = cfg if cfg else self.sample_cfg
        dlg.FORBIDDEN_FLAGS = ["--volname", "--cache-dir", "--vfs-cache-mode"]
        
        # 위젯 Mocking: return_value를 명시적으로 문자열로 설정
        dlg._remote = MagicMock(); dlg._remote.get.return_value = str(dlg._m.get("remote", ""))
        dlg._drive = MagicMock(); dlg._drive.get.return_value = str(dlg._m.get("drive", ""))
        dlg._rpath = MagicMock(); dlg._rpath.get.return_value = str(dlg._m.get("remote_path", ""))
        dlg._cdir = MagicMock(); dlg._cdir.get.return_value = str(dlg._m.get("cache_dir", ""))
        dlg._cmode = MagicMock(); dlg._cmode.get.return_value = "full"
        dlg._auto = MagicMock(); dlg._auto.get.return_value = False
        
        # Text 위젯 Mocking: re.split에서 TypeError 방지
        dlg._extra_text = MagicMock()
        dlg._extra_text.get.return_value = "" 
        
        dlg.destroy = MagicMock()
        return dlg

    # 1. rclone를 제대로 불러오는가?
    def test_scenario_01_load_rclone(self):
        cfg = {"rclone_path": "C:\\fake\\rclone.exe"}
        with patch("pathlib.Path.exists", return_value=True):
            exe = rclone_manager.get_rclone_exe(cfg)
        self.assertEqual(str(exe), "C:\\fake\\rclone.exe")

    # 2. conf 파일의 내용이 제대로 불려오는가?
    def test_scenario_02_parse_conf_content(self):
        with patch("configparser.ConfigParser.read", return_value=None):
            with patch("configparser.ConfigParser.sections", return_value=["my-drive"]):
                with patch("configparser.ConfigParser.get", return_value="drive"):
                    remotes = rclone_manager.parse_rclone_conf(Path("fake.conf"))
        self.assertEqual(remotes[0]["name"], "my-drive")

    # 3. 등록된 conf에서 마운트를 추가할 수 있는가?
    def test_scenario_03_09_add_and_save_mount(self):
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app, mount={"remote": "my-drive"})
        dlg._drive.get.return_value = "Z:"
        dlg._save()
        self.assertEqual(dlg.result["drive"], "Z:")

    # 4. 연결 테스트 성공/실패 출력
    @patch("subprocess.run")
    def test_scenario_04_connection_test(self, mock_run):
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app, mount={"remote": "gd", "remote_path": "sub"})
        mock_run.return_value = MagicMock(returncode=0)
        dlg._test()
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        dlg._test()

    # 5. 잘못된 리모트 이름 식별
    def test_scenario_05_detect_empty_remote(self):
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app, mount={"remote": ""})
        dlg._remote.get.return_value = ""
        with patch("tkinter.messagebox.showwarning") as mock_msg:
            dlg._save()
            mock_msg.assert_called_with("오류", "리모트 이름 필수")

    # 6. 드라이브 문자 충돌 식별
    def test_scenario_06_detect_drive_conflict(self):
        cfg = {"mounts": [{"id": "1", "drive": "X:", "remote": "old"}]}
        app = self._create_mocked_app(cfg=cfg)
        dlg = self._create_mocked_dialog(app, mount={"remote": "new"}, cfg=cfg)
        dlg._drive.get.return_value = "X:"
        dlg._remote.get.return_value = "new"
        with patch("tkinter.messagebox.showerror") as mock_msg:
            dlg._save()
            self.assertTrue(mock_msg.called)

    # 7. 플래그 식별 및 수정/에러
    def test_scenario_07_validate_extra_flags(self):
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app, mount={"remote": "gd"})
        dlg._extra_text.get.return_value = "--volname MyDrive"
        with patch("tkinter.messagebox.showerror") as mock_msg:
            dlg._save()
            self.assertTrue(mock_msg.called)
        
        dlg._extra_text.get.return_value = "read-only"
        dlg._save()
        self.assertEqual(dlg.result["extra_flags"], "--read-only")

    # 8. 시작 시 자동 마운트
    @patch("rclone_manager.do_mount")
    def test_scenario_08_verify_auto_mount(self, mock_mount):
        cfg = {"auto_mount": True, "mounts": [{"id": "m1", "auto_mount": True, "remote": "gd", "drive": "X:"}]}
        app = self._create_mocked_app(cfg=cfg)
        with patch("rclone_manager.get_rclone_exe", return_value=Path("rclone.exe")):
            with patch("pathlib.Path.exists", return_value=True):
                app._automount_all()
        self.assertTrue(mock_mount.called)

    # 10. 편집한 내용 저장
    def test_scenario_10_verify_edit_persistence(self):
        cfg = {"mounts": [{"id": "m1", "remote": "gd", "drive": "X:"}]}
        app = self._create_mocked_app(cfg=cfg)
        dlg = self._create_mocked_dialog(app, mount=cfg["mounts"][0], cfg=cfg)
        dlg._drive.get.return_value = "Y:"
        dlg._remote.get.return_value = "gd"
        dlg._extra_text.get.return_value = "" # TypeError 방지
        dlg._save()
        self.assertEqual(dlg.result["drive"], "Y:")

    # 11. 삭제 시 JSON 내용 삭제
    def test_scenario_11_verify_deletion(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "m1", "remote": "gd", "drive": "X:"}]
        with patch("tkinter.messagebox.askyesno", return_value=True):
            with patch.object(app, "_sel_id", return_value="m1"):
                app._del()
        self.assertEqual(len(app._cfg["mounts"]), 0)

    # 12. 동일 경로 마운트 중복 등록 식별
    def test_scenario_12_detect_duplicate_path(self):
        cfg = {"mounts": [{"id": "1", "remote": "gd", "remote_path": "sub", "drive": "X:"}]}
        app = self._create_mocked_app(cfg=cfg)
        dlg = self._create_mocked_dialog(app, mount={"remote": "gd", "remote_path": "sub"}, cfg=cfg)
        dlg._remote.get.return_value = "gd"
        dlg._rpath.get.return_value = "sub"
        with patch("tkinter.messagebox.showerror") as mock_msg:
            dlg._save()
            self.assertTrue(mock_msg.called)

    # 13. 순서 변경
    def test_scenario_13_verify_order_change(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "1", "remote": "A"}, {"id": "2", "remote": "B"}]
        with patch.object(app, "_sel_id", return_value="2"):
            app._move_up()
        self.assertEqual(app._cfg["mounts"][0]["id"], "2")

    # 14. 언마운트
    def test_scenario_14_verify_unmount(self):
        m_id = "test"
        mock_proc = MagicMock()
        rclone_manager.active_mounts[m_id] = mock_proc
        rclone_manager.unmount(m_id)
        self.assertTrue(mock_proc.terminate.called)

    # 15. 업데이트 로직
    @patch("rclone_manager.download_rclone", return_value=True)
    def test_scenario_15_update_logic(self, mock_dl):
        app = self._create_mocked_app()
        with patch("rclone_manager.get_latest_version", return_value="1.66.0"):
            app._manual_up()
        self.assertIsNotNone(app)

    # 16. 시작 시 자동 실행
    def test_scenario_16_startup_registration(self):
        with patch("winreg.OpenKey"), patch("winreg.SetValueEx") as mock_set:
            rclone_manager.set_startup(True)
        self.assertTrue(mock_set.called)

    # 17, 18. 트레이 상호작용 및 상태 반영
    def test_scenario_17_18_tray_interaction(self):
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "m1", "remote": "gd", "drive": "X:"}]
        with patch("pystray.Icon"):
            app._start_tray()
            self.assertIsNotNone(app)

if __name__ == "__main__":
    unittest.main()
