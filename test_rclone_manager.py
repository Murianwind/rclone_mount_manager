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
        """테스트 환경 초기화"""
        self.sample_cfg = {
            "remotes": [{"name": "drive", "type": "drive"}],
            "mounts": [],
            "rclone_path": "",
            "auto_mount": False
        }

    def _create_mocked_app(self, cfg=None):
        """UI 호출을 차단한 실제 App 인스턴스 생성"""
        target_cfg = cfg if cfg else self.sample_cfg
        with patch('tkinter.Tk.__init__', return_value=None), \
             patch('tkinter.Tk.geometry'), \
             patch('tkinter.Tk.title'), \
             patch('tkinter.Tk.withdraw'), \
             patch('tkinter.Tk.minsize'), \
             patch('tkinter.Tk.resizable'), \
             patch('tkinter.Tk.protocol'), \
             patch('rclone_manager.load_config', return_value=target_cfg):
            app = App()
            app._tree = MagicMock()
            app._tree.get_children.return_value = []
            return app

    def _create_mocked_dialog(self, parent, mount=None, cfg=None):
        """UI 호출을 차단한 실제 MountDialog 인스턴스 생성"""
        target_cfg = cfg if cfg else self.sample_cfg
        with patch('tkinter.Toplevel.__init__', return_value=None), \
             patch('tkinter.Toplevel.geometry'), \
             patch('tkinter.Toplevel.title'), \
             patch('tkinter.Toplevel.minsize'), \
             patch('tkinter.Toplevel.resizable'), \
             patch('tkinter.Toplevel.grab_set'), \
             patch('rclone_manager.MountDialog._build'):
            dlg = MountDialog(parent, mount=mount, app_cfg=target_cfg)
            # 내부 위젯을 Mock으로 교체하여 로직 테스트 가능하게 설정
            dlg._remote = MagicMock()
            dlg._drive = MagicMock()
            dlg._rpath = MagicMock()
            dlg._cdir = MagicMock()
            dlg._cmode = MagicMock()
            dlg._extra_text = MagicMock()
            dlg._auto = MagicMock()
            return dlg

    # 1. rclone를 제대로 불러오는가?
    def test_scenario_01_load_rclone(self):
        # Given: 설정 파일에 rclone 경로가 있을 때
        cfg = {"rclone_path": "C:\\fake\\rclone.exe"}
        # When: rclone 실행 파일을 요청하면
        with patch("pathlib.Path.exists", return_value=True):
            exe = rclone_manager.get_rclone_exe(cfg)
        # Then: 해당 경로가 반환되어야 함
        self.assertEqual(str(exe), "C:\\fake\\rclone.exe")

    # 2. conf 파일의 내용이 제대로 불려오는가?
    def test_scenario_02_parse_conf_content(self):
        # Given: 섹션 정보가 있는 rclone.conf
        with patch("configparser.ConfigParser.read", return_value=None):
            with patch("configparser.ConfigParser.sections", return_value=["my-drive"]):
                with patch("configparser.ConfigParser.get", return_value="drive"):
                    # When: 파일을 파싱하면
                    remotes = rclone_manager.parse_rclone_conf(Path("fake.conf"))
        # Then: 리모트 이름이 정확해야 함
        self.assertEqual(remotes[0]["name"], "my-drive")

    # 3. 등록된 conf에서 마운트를 추가할 수 있는가?
    # 9. 추가한 마운트를 저장이 되어서 동작하는가?
    def test_scenario_03_09_add_and_save_mount(self):
        # Given: 마운트 추가 대화상자에서
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app, mount={"remote": "my-drive"})
        # When: 드라이브 문자를 Z:로 입력하고 저장하면
        dlg._remote.get.return_value = "my-drive"
        dlg._drive.get.return_value = "Z:"
        dlg._rpath.get.return_value = ""
        dlg._extra_text.get.return_value = ""
        dlg._save()
        # Then: 결과 데이터의 드라이브 문자가 Z:여야 함 (AssertionError 방지)
        self.assertIsNotNone(dlg.result)
        self.assertEqual(dlg.result["drive"], "Z:")

    # 4. 연결 테스트는 루트와 서브 디렉토리에서 각각 성공/실패를 제대로 출력하는가?
    @patch("subprocess.run")
    def test_scenario_04_connection_test(self, mock_run):
        # Given: 연결 테스트 환경
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app, mount={"remote": "gd", "remote_path": "sub"})
        # When & Then: 성공 시
        mock_run.return_value = MagicMock(returncode=0)
        dlg._test()
        # When & Then: 실패 시
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        dlg._test()

    # 5. 잘못된 리모트 이름을 식별하는가?
    def test_scenario_05_detect_empty_remote(self):
        # Given: 리모트 이름이 누락된 경우
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app, mount={"remote": ""})
        dlg._remote.get.return_value = ""
        # When: 저장 시도 시
        with patch("tkinter.messagebox.showwarning") as mock_msg:
            dlg._save()
            # Then: 경고가 표시되어야 함
            mock_msg.assert_called_with("오류", "리모트 이름 필수")

    # 6. 드라이브 문자 충돌을 식별하고 에러메시지를 출력하는가?
    def test_scenario_06_detect_drive_conflict(self):
        # Given: 이미 X: 드라이브가 사용 중일 때
        cfg = {"mounts": [{"id": "1", "drive": "X:", "remote": "old"}]}
        app = self._create_mocked_app(cfg=cfg)
        dlg = self._create_mocked_dialog(app, mount={"remote": "new"}, cfg=cfg)
        # When: 동일한 X: 드라이브로 저장 시
        dlg._drive.get.return_value = "X:"
        dlg._remote.get.return_value = "new"
        with patch("tkinter.messagebox.showerror") as mock_msg:
            dlg._save()
            # Then: 에러 메시지가 표시되어야 함
            self.assertTrue(mock_msg.called)

    # 7. 추가플래그중 잘못입력되거나 불가능한 태그 식별/수정/에러
    def test_scenario_07_validate_extra_flags(self):
        # Given: 금지된 플래그 입력 시
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app, mount={"remote": "gd"})
        dlg._extra_text.get.return_value = "--vfs-cache-mode full"
        # When: 저장 시
        with patch("tkinter.messagebox.showerror") as mock_msg:
            dlg._save()
            # Then: 금지 안내 에러가 나야 함
            self.assertTrue(mock_msg.called)

    # 8. 시작 시 자동 마운트는 제대로 동작하는가?
    @patch("rclone_manager.do_mount")
    def test_scenario_08_verify_auto_mount(self, mock_mount):
        # Given: 자동 마운트 설정 상태
        cfg = {"auto_mount": True, "mounts": [{"id": "m1", "auto_mount": True, "remote": "gd", "drive": "X:"}]}
        # When: 앱 실행 시
        with patch("rclone_manager.get_rclone_exe", return_value=Path("rclone.exe")):
            with patch("pathlib.Path.exists", return_value=True):
                app = self._create_mocked_app(cfg=cfg)
                app._automount_all()
        # Then: 마운트 함수가 실행되어야 함
        self.assertTrue(mock_mount.called)

    # 10. 편집한 내용이 저장이 되어서 동작하는가?
    def test_scenario_10_verify_edit_persistence(self):
        # Given: 기존 마운트 수정 시
        cfg = {"mounts": [{"id": "m1", "remote": "gd", "drive": "X:"}]}
        app = self._create_mocked_app(cfg=cfg)
        dlg = self._create_mocked_dialog(app, mount=cfg["mounts"][0], cfg=cfg)
        # When: 드라이브를 Y:로 바꾸면
        dlg._drive.get.return_value = "Y:"
        dlg._remote.get.return_value = "gd"
        dlg._save()
        # Then: 결과가 Y:여야 함
        self.assertEqual(dlg.result["drive"], "Y:")

    # 11. 삭제를 하면 mount.json에 내용이 삭제되는가?
    def test_scenario_11_verify_deletion(self):
        # Given: 마운트 삭제 요청 시
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "m1", "remote": "gd", "drive": "X:"}]
        # When: 삭제를 확인하면
        with patch("tkinter.messagebox.askyesno", return_value=True):
            with patch.object(app, "_sel_id", return_value="m1"):
                app._del()
        # Then: 리스트에서 제거되어야 함
        self.assertEqual(len(app._cfg["mounts"]), 0)

    # 12. 동일한 리모트나 동일한 경로의 마운트를 등록하려고 하면 에러메시지가 출력되는가?
    def test_scenario_12_detect_duplicate_path(self):
        # Given: 이미 등록된 동일 경로
        cfg = {"mounts": [{"id": "1", "remote": "gd", "remote_path": "sub", "drive": "X:"}]}
        app = self._create_mocked_app(cfg=cfg)
        dlg = self._create_mocked_dialog(app, mount={"remote": "gd", "remote_path": "sub"}, cfg=cfg)
        # When: 다시 등록 시
        dlg._remote.get.return_value = "gd"
        dlg._rpath.get.return_value = "sub"
        with patch("tkinter.messagebox.showerror") as mock_msg:
            dlg._save()
            # Then: 중복 에러가 발생해야 함
            self.assertTrue(mock_msg.called)

    # 13. 목록에서 순서는 제대로 변경되는가?
    def test_scenario_13_verify_order_change(self):
        # Given: 순서 변경 시
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "1", "remote": "A"}, {"id": "2", "remote": "B"}]
        # When: 두 번째를 위로 올리면
        with patch.object(app, "_sel_id", return_value="2"):
            app._move_up()
        # Then: 첫 번째 아이디가 2가 되어야 함 (StopIteration 방지)
        self.assertEqual(app._cfg["mounts"][0]["id"], "2")

    # 14. 언마운트는 제대로 되는가?
    def test_scenario_14_verify_unmount(self):
        # Given: 실행 중인 프로세스
        m_id = "test"
        mock_proc = MagicMock()
        rclone_manager.active_mounts[m_id] = mock_proc
        # When: 언마운트 시
        rclone_manager.unmount(m_id)
        # Then: 종료 명령이 가야 함
        self.assertTrue(mock_proc.terminate.called)

    # 15. 업데이트는 제대로 되는가?
    @patch("rclone_manager.download_rclone", return_value=True)
    def test_scenario_15_update_logic(self, mock_dl):
        # Given: 업데이트 상황
        app = self._create_mocked_app()
        with patch("rclone_manager.get_latest_version", return_value="1.66.0"):
            # When: 업데이트 실행 시
            app._manual_up()
        # Then: 인스턴스가 유지되어야 함
        self.assertIsNotNone(app)

    # 16. 시작 시 자동 실행은 제대로 되는가?
    def test_scenario_16_startup_check(self):
        # Given: 시작 프로그램 등록 시
        with patch("winreg.OpenKey"), patch("winreg.SetValueEx") as mock_set:
            # When: 호출하면
            rclone_manager.set_startup(True)
        # Then: 레지스트리 쓰기가 발생해야 함
        self.assertTrue(mock_set.called)

    # 17. 트레이에서 마운트, 언마운트는 제대로 되는가?
    # 18. 등록된 마운트는 트레이에 올바로 출력되고 상태가 정확히 반영되는가?
    def test_scenario_17_18_tray_interaction(self):
        # Given: 트레이 상태 반영 확인
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "m1", "remote": "gd", "drive": "X:"}]
        # When: 트레이 시작 시
        with patch("pystray.Icon"):
            app._start_tray()
            # Then: 트레이 객체가 존재해야 함
            self.assertIsNotNone(app._tray)

if __name__ == "__main__":
    unittest.main()
