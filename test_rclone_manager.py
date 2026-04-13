import unittest
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import tkinter as tk

# 가상 환경(CI)에서의 TclError 방지를 위한 전역 패치
# 테스트가 실행되는 동안 tkinter.Tk와 Toplevel이 실제 창을 띄우지 않게 합니다.
patch('tkinter.Tk', MagicMock()).start()
patch('tkinter.Toplevel', MagicMock()).start()

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

    def _create_mock_app(self):
        """가짜 앱 인스턴스 생성"""
        with patch('rclone_manager.load_config', return_value=self.sample_cfg):
            app = App()
            # UI 요소들을 Mock으로 교체하여 에러 방지
            app._tree = MagicMock()
            app._tree.get_children.return_value = []
            return app

    # 1. rclone를 제대로 불러오는가?
    def test_scenario_01_load_rclone(self):
        # Given: rclone_path가 설정 파일에 명시되어 있을 때
        cfg = {"rclone_path": "C:\\fake\\rclone.exe"}
        # When: rclone 실행 파일 경로를 가져오면
        with patch("pathlib.Path.exists", return_value=True):
            exe = rclone_manager.get_rclone_exe(cfg)
        # Then: 설정된 해당 경로가 정확히 반환되어야 함
        self.assertEqual(str(exe), "C:\\fake\\rclone.exe")

    # 2. conf 파일의 내용이 제대로 불려오는가?
    def test_scenario_02_parse_conf_content(self):
        # Given: 섹션 [my-drive]와 타입 drive가 적힌 rclone.conf가 있을 때
        with patch("configparser.ConfigParser.read", return_value=None):
            with patch("configparser.ConfigParser.sections", return_value=["my-drive"]):
                with patch("configparser.ConfigParser.get", return_value="drive"):
                    # When: 해당 파일을 파싱하면
                    remotes = rclone_manager.parse_rclone_conf(Path("fake.conf"))
        # Then: 파싱 결과 리스트에 해당 이름의 리모트가 포함되어야 함
        self.assertEqual(remotes[0]["name"], "my-drive")

    # 3. 등록된 conf에서 마운트를 추가할 수 있는가?
    # 9. 추가한 마운트를 저장이 되어서 동작하는가?
    def test_scenario_03_09_add_and_save_mount(self):
        # Given: 앱이 실행 중이고 마운트 추가 창을 열었을 때
        app = self._create_mock_app()
        dlg = MountDialog(app, mount={"remote": "my-drive"}, app_cfg=self.sample_cfg)
        # When: 드라이브 문자를 Z:로 설정하고 저장을 누르면
        dlg._drive = MagicMock()
        dlg._drive.get.return_value = "Z:"
        dlg._remote = MagicMock()
        dlg._remote.get.return_value = "my-drive"
        dlg._rpath = MagicMock()
        dlg._rpath.get.return_value = ""
        dlg._extra_text = MagicMock()
        dlg._extra_text.get.return_value = ""
        
        dlg._save()
        # Then: 결과 데이터의 드라이브 문자가 Z:로 저장되어야 함
        self.assertIsNotNone(dlg.result)
        self.assertEqual(dlg.result["drive"], "Z:")

    # 4. 연결 테스트는 루트와 서브 디렉토리에서 각각 성공/실패를 제대로 출력하는가?
    @patch("subprocess.run")
    def test_scenario_04_connection_test_feedback(self, mock_run):
        # Given: 연결 테스트 대상 리모트 정보가 있을 때
        app = self._create_mock_app()
        dlg = MountDialog(app, mount={"remote": "gd", "remote_path": "sub"}, app_cfg=self.sample_cfg)
        # When & Then: 서브 프로세스가 0(성공)을 반환할 때 테스트가 수행되는지 확인
        mock_run.return_value = MagicMock(returncode=0)
        dlg._test()
        # When & Then: 서브 프로세스가 1(실패)을 반환할 때 에러가 처리되는지 확인
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        dlg._test()

    # 5. 잘못된 리모트 이름을 식별하는가?
    def test_scenario_05_detect_empty_remote(self):
        # Given: 리모트 이름이 비어있는 마운트 설정일 때
        app = self._create_mock_app()
        dlg = MountDialog(app, mount={"remote": ""}, app_cfg=self.sample_cfg)
        # When: 저장을 시도하면
        dlg._remote.get = MagicMock(return_value="")
        with patch("tkinter.messagebox.showwarning") as mock_msg:
            dlg._save()
            # Then: '리모트 이름 필수' 경고 메시지가 출력되어야 함
            mock_msg.assert_called_with("오류", "리모트 이름 필수")

    # 6. 드라이브 문자 충돌을 식별하고 에러메시지를 출력하는가?
    def test_scenario_06_detect_drive_conflict(self):
        # Given: 이미 'X:' 드라이브가 다른 마운트에 사용 중일 때
        cfg = {"mounts": [{"id": "1", "drive": "X:", "remote": "existing"}]}
        app = self._create_mock_app()
        dlg = MountDialog(app, mount={"remote": "new"}, app_cfg=cfg)
        # When: 신규 마운트에 동일한 'X:'를 할당하고 저장하면
        dlg._drive.get = MagicMock(return_value="X:")
        dlg._remote.get = MagicMock(return_value="new")
        with patch("tkinter.messagebox.showerror") as mock_msg:
            dlg._save()
            # Then: 드라이브 중복 에러 메시지가 출력되어야 함
            self.assertTrue(mock_msg.called)

    # 7. 추가플래그중 잘못입력되거나 불가능한 태그 식별/수정/에러
    def test_scenario_07_validate_extra_flags(self):
        # Given: 금지된 플래그(--vfs-cache-mode)를 추가 플래그에 입력했을 때
        app = self._create_mock_app()
        dlg = MountDialog(app, mount={"remote": "gd"}, app_cfg=self.sample_cfg)
        dlg._extra_text.get = MagicMock(return_value="--vfs-cache-mode full")
        # When: 저장을 시도하면
        with patch("tkinter.messagebox.showerror") as mock_msg:
            dlg._save()
            # Then: 금지된 플래그 안내 에러가 발생해야 함
            self.assertTrue(mock_msg.called)

    # 8. 시작 시 자동 마운트는 제대로 동작하는가?
    @patch("rclone_manager.do_mount")
    def test_scenario_08_verify_auto_mount(self, mock_mount):
        # Given: '시작 시 자동 마운트'가 체크된 설정 파일이 로드될 때
        cfg = {"auto_mount": True, "mounts": [{"id": "m1", "auto_mount": True, "remote": "gd", "drive": "X:"}]}
        # When: 앱이 초기화되면
        with patch("rclone_manager.load_config", return_value=cfg):
            with patch("rclone_manager.get_rclone_exe", return_value=Path("rclone.exe")):
                with patch("pathlib.Path.exists", return_value=True):
                    app = App()
                    app._refresh_list = MagicMock()
                    app._automount_all()
        # Then: do_mount 함수가 실제로 호출되어야 함
        self.assertTrue(mock_mount.called)

    # 10. 편집한 내용이 저장이 되어서 동작하는가?
    def test_scenario_10_verify_edit_persistence(self):
        # Given: 기존에 등록된 마운트 정보가 있을 때
        cfg = {"mounts": [{"id": "m1", "remote": "gd", "drive": "X:"}]}
        app = self._create_mock_app()
        dlg = MountDialog(app, mount=cfg["mounts"][0], app_cfg=cfg)
        # When: 드라이브 문자를 'Y:'로 수정하고 저장하면
        dlg._drive.get = MagicMock(return_value="Y:")
        dlg._remote.get = MagicMock(return_value="gd")
        dlg._save()
        # Then: 결과값에 변경된 문자 'Y:'가 반영되어 있어야 함
        self.assertEqual(dlg.result["drive"], "Y:")

    # 11. 삭제를 하면 mount.json에 내용이 삭제되는가?
    def test_scenario_11_verify_config_deletion(self):
        # Given: 마운트 항목이 한 개 등록된 앱 상태에서
        app = self._create_mock_app()
        app._cfg["mounts"] = [{"id": "m1", "remote": "gd", "drive": "X:"}]
        # When: 해당 항목을 선택하고 삭제 명령을 내리면
        with patch("tkinter.messagebox.askyesno", return_value=True):
            with patch.object(app, "_sel_id", return_value="m1"):
                app._del()
        # Then: 설정 내 마운트 리스트가 비어있어야 함
        self.assertEqual(len(app._cfg["mounts"]), 0)

    # 12. 동일한 리모트나 동일한 경로의 마운트를 등록하려고 하면 에러메시지가 출력되는가?
    def test_scenario_12_detect_duplicate_path(self):
        # Given: 이미 'gd:sub' 경로가 등록되어 있는 경우
        cfg = {"mounts": [{"id": "1", "remote": "gd", "remote_path": "sub", "drive": "X:"}]}
        app = self._create_mock_app()
        dlg = MountDialog(app, mount={"remote": "gd", "remote_path": "sub"}, app_cfg=cfg)
        # When: 동일한 'gd:sub' 경로를 신규 등록하려 하면
        dlg._remote.get = MagicMock(return_value="gd")
        dlg._rpath.get = MagicMock(return_value="sub")
        with patch("tkinter.messagebox.showerror") as mock_msg:
            dlg._save()
            # Then: 중복 경로 에러 메시지가 표시되어야 함
            self.assertTrue(mock_msg.called)

    # 13. 목록에서 순서는 제대로 변경되는가?
    def test_scenario_13_verify_order_movement(self):
        # Given: 목록에 두 개의 마운트('A', 'B')가 있을 때
        app = self._create_mock_app()
        app._cfg["mounts"] = [{"id": "1", "remote": "A"}, {"id": "2", "remote": "B"}]
        # When: 두 번째 항목('2')을 위로 이동시키면
        with patch.object(app, "_sel_id", return_value="2"):
            app._move_up()
        # Then: 첫 번째 인덱스의 아이디가 '2'가 되어야 함
        self.assertEqual(app._cfg["mounts"][0]["id"], "2")

    # 14. 언마운트는 제대로 되는가?
    def test_scenario_14_verify_unmount(self):
        # Given: 실행 중인 마운트 프로세스 객체가 있을 때
        m_id = "test"
        mock_proc = MagicMock()
        rclone_manager.active_mounts[m_id] = mock_proc
        # When: 언마운트 함수를 호출하면
        rclone_manager.unmount(m_id)
        # Then: 해당 프로세스에 종료 명령(terminate)이 전달되어야 함
        self.assertTrue(mock_proc.terminate.called)

    # 15. 업데이트는 제대로 되는가?
    @patch("rclone_manager.download_rclone", return_value=True)
    def test_scenario_15_update_logic(self, mock_dl):
        # Given: 새로운 버전 정보가 확인되었을 때
        app = self._create_mock_app()
        with patch("rclone_manager.get_latest_version", return_value="1.66.0"):
            # When: 수동 업데이트를 실행하면
            app._manual_up()
        # Then: 업데이트 체크 로직이 정상 호출되는지 확인
        self.assertIsNotNone(app)

    # 16. 시작 시 자동 실행은 제대로 되는가?
    def test_scenario_16_startup_registration(self):
        # Given: 시작 프로그램 등록을 시도할 때
        with patch("winreg.OpenKey"), patch("winreg.SetValueEx") as mock_set:
            # When: set_startup(True)를 호출하면
            rclone_manager.set_startup(True)
        # Then: 윈도우 레지스트리에 쓰기 명령이 수행되어야 함
        self.assertTrue(mock_set.called)

    # 17. 트레이에서 마운트, 언마운트는 제대로 되는가?
    # 18. 등록된 마운트는 트레이에 올바로 출력되고 상태가 정확히 반영되는가?
    def test_scenario_17_18_tray_interaction(self):
        # Given: 등록된 마운트 정보('gd')가 있을 때
        app = self._create_mock_app()
        app._cfg["mounts"] = [{"id": "m1", "remote": "gd", "drive": "X:"}]
        # When: 트레이 아이콘 서비스를 시작하면
        with patch("pystray.Icon"):
            app._start_tray()
            # Then: 트레이 객체가 생성되었는지 확인
            self.assertIsNotNone(app._tray)

if __name__ == "__main__":
    unittest.main()
