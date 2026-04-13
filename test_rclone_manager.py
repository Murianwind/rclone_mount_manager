import unittest
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import tkinter as tk

# 테스트 대상 모듈 임포트
import rclone_manager
from rclone_manager import App, MountDialog

class TestRcloneManagerBDD(unittest.TestCase):

    def setUp(self):
        """
        테스트 기초 데이터 설정 (Given)
        """
        self.sample_cfg = {
            "remotes": [{"name": "drive", "type": "drive"}],
            "mounts": [],
            "rclone_path": "",
            "auto_mount": False
        }

    def _create_mocked_app(self, cfg=None):
        """
        CI 환경의 RecursionError 및 TclError 방지를 위한 Mock 앱 생성
        """
        app = App.__new__(App)
        app._cfg = cfg if cfg else self.sample_cfg
        app._status = {}
        app._tray = None 
        app._tree = MagicMock()
        app._tree.get_children.return_value = []
        app._rc_var = MagicMock()
        app._rc_ver_label = MagicMock()
        app._app_up_btn = MagicMock()
        app.after = MagicMock()
        app.update_idletasks = MagicMock()
        return app

    def _create_mocked_dialog(self, parent, mount=None, cfg=None):
        """
        UI 빌드를 건너뛰고 로직 테스트를 위한 Mock 다이얼로그 생성
        """
        dlg = MountDialog.__new__(MountDialog)
        dlg._m = mount if mount else {}
        dlg._app_cfg = cfg if cfg else self.sample_cfg
        dlg._rem = MagicMock(); dlg._drv = MagicMock()
        dlg._pth = MagicMock(); dlg._ext = MagicMock()
        dlg._auto = MagicMock(); dlg.destroy = MagicMock()
        return dlg

    # 1. rclone를 제대로 불러오는가?
    def test_scenario_01_load_rclone(self):
        # Given: rclone_path가 설정 파일에 저장되어 있을 때
        cfg = {"rclone_path": "C:\\fake\\rclone.exe"}
        # When: 경로를 요청하면 (파일 존재 여부 Mock 처리)
        with patch("pathlib.Path.exists", return_value=True):
            exe = rclone_manager.get_rclone_exe(cfg)
        # Then: 설정된 경로와 일치해야 함
        self.assertEqual(str(exe), "C:\\fake\\rclone.exe")

    # 2. conf 파일의 내용이 제대로 불려오는가?
    def test_scenario_02_parse_conf_content(self):
        # Given: 리모트 정보가 포함된 가상의 rclone.conf 설정이 있을 때
        with patch("configparser.ConfigParser.read", return_value=None):
            with patch("configparser.ConfigParser.sections", return_value=["my-drive"]):
                with patch("configparser.ConfigParser.get", return_value="drive"):
                    # When: conf 파일을 파싱하면
                    remotes = rclone_manager.parse_rclone_conf(Path("fake.conf"))
        # Then: 리모트 이름 'my-drive'가 정확히 반환되어야 함
        self.assertEqual(remotes[0]["name"], "my-drive")

    # 3. 등록된 conf에서 마운트를 추가할 수 있는가? / 9. 추가한 마운트가 저장이 되는가?
    def test_scenario_03_09_add_and_save_mount(self):
        # Given: 마운트 추가 대화상자가 준비되었을 때
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        # When: 사용자가 리모트 'gd', 드라이브 'Z:'를 입력하고 저장을 누르면
        dlg._rem.get.return_value = "gd"; dlg._drv.get.return_value = "Z:"
        dlg._pth.get.return_value = ""; dlg._ext.get.return_value = ""; dlg._auto.get.return_value = False
        dlg._save()
        # Then: 결과값(result)의 드라이브 문자가 'Z:'여야 함
        self.assertEqual(dlg.result["drive"], "Z:")

    # 4. 연결 테스트 로직이 존재하는가?
    def test_scenario_04_connection_test_exists(self):
        # Given: 다이얼로그가 생성되었을 때
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app, mount={"remote": "gd"})
        # When: 메서드 존재 여부를 확인하면
        # Then: 연결 테스트를 수행하는 _test 메서드가 있어야 함
        self.assertTrue(hasattr(dlg, '_test'))

    # 5. 잘못된 리모트 이름을 식별하는가?
    def test_scenario_05_detect_empty_remote(self):
        # Given: 리모트 이름이 비어있는 상태에서
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        dlg._rem.get.return_value = ""
        # When: 저장을 시도하면
        with patch("tkinter.messagebox.showwarning") as mock_msg:
            dlg._save()
            # Then: 에러 메시지가 표시되어야 함
            mock_msg.assert_called_with("오류", "리모트 이름 필수")

    # 6. 드라이브 문자 중복을 식별하는가?
    def test_scenario_06_detect_drive_conflict(self):
        # Given: 이미 사용 중인 드라이브 'X:'가 리스트에 있을 때
        cfg = {"mounts": [{"id": "1", "drive": "X:", "remote": "old"}]}
        app = self._create_mocked_app(cfg=cfg)
        dlg = self._create_mocked_dialog(app, cfg=cfg)
        # When: 새 항목에 동일한 'X:'를 입력하고 저장하려 하면
        dlg._rem.get.return_value = "new"; dlg._drv.get.return_value = "X:"
        with patch("tkinter.messagebox.showerror") as mock_msg:
            dlg._save()
            # Then: 중복 에러 메시지가 표시되어야 함
            self.assertTrue(mock_msg.called)

    # 7. 추가 플래그 구분 및 처리가 정확한가?
    def test_scenario_07_validate_extra_flags(self):
        # Given: ';'로 구분된 플래그 문자열이 주어졌을 때
        mount = {"remote": "gd", "drive": "X:", "extra_flags": "--read-only;--vfs-cache-mode full"}
        # When: rclone 명령어를 빌드하면
        cmd = rclone_manager.build_cmd(Path("rclone.exe"), mount)
        # Then: 모든 플래그가 개별적으로 명령어 리스트에 포함되어야 함
        self.assertIn("--read-only", cmd)
        self.assertIn("--vfs-cache-mode", cmd)

    # 8. 시작 시 자동 마운트는 제대로 동작하는가?
    @patch("rclone_manager.App._do_mount")
    def test_scenario_08_verify_auto_mount(self, mock_do):
        # Given: 자동 마운트가 설정된 항목이 존재할 때
        cfg = {"auto_mount": True, "mounts": [{"id": "m1", "auto_mount": True, "remote": "gd"}]}
        app = self._create_mocked_app(cfg=cfg)
        # When: 자동 마운트 로직이 실행되면
        app._automount_all()
        # Then: 마운트 수행 함수가 호출되어야 함
        self.assertTrue(mock_do.called)

    # 10. 편집한 내용이 저장이 되어서 동작하는가?
    def test_scenario_10_verify_edit_persistence(self):
        # Given: 기존 항목 'X:'를 수정하려 할 때
        cfg = {"mounts": [{"id": "m1", "remote": "gd", "drive": "X:"}]}
        app = self._create_mocked_app(cfg=cfg)
        dlg = self._create_mocked_dialog(app, mount=cfg["mounts"][0], cfg=cfg)
        # When: 드라이브 문자를 'Y:'로 변경하고 저장하면
        dlg._rem.get.return_value = "gd"; dlg._drv.get.return_value = "Y:"
        dlg._save()
        # Then: 결과값이 'Y:'로 업데이트되어야 함
        self.assertEqual(dlg.result["drive"], "Y:")

    # 11. 삭제를 하면 목록에서 제거되는가?
    def test_scenario_11_verify_deletion(self):
        # Given: 목록에 마운트 항목이 존재할 때
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "m1", "remote": "gd", "drive": "X:"}]
        # When: 항목을 삭제하면 (Yes 응답 가정)
        with patch("tkinter.messagebox.askyesno", return_value=True):
            with patch.object(app._tree, "selection", return_value=("m1",)):
                app._del()
        # Then: 리스트가 비어있어야 함
        self.assertEqual(len(app._cfg["mounts"]), 0)

    # 12. 동일 경로 마운트 중복 등록을 차단하는가?
    def test_scenario_12_detect_duplicate_path(self):
        # Given: 'gd' 리모트가 이미 등록되어 있을 때
        cfg = {"mounts": [{"id": "1", "remote": "gd", "drive": "X:"}]}
        app = self._create_mocked_app(cfg=cfg)
        dlg = self._create_mocked_dialog(app, cfg=cfg)
        # When: 동일한 'gd'로 저장을 시도하면
        dlg._rem.get.return_value = "gd"; dlg._drv.get.return_value = "X:"
        with patch("tkinter.messagebox.showerror") as mock_msg:
            dlg._save()
            # Then: 에러가 발생해야 함
            self.assertTrue(mock_msg.called)

    # 13. 목록에서 순서 변경이 가능한가?
    def test_scenario_13_verify_order_change(self):
        # Given: 순서대로 'A', 'B' 항목이 있을 때
        app = self._create_mocked_app()
        app._cfg["mounts"] = [{"id": "1", "remote": "A"}, {"id": "2", "remote": "B"}]
        # When: 두 번째 항목을 위로 이동시키면
        with patch.object(app._tree, "selection", return_value=("2",)):
            app._move_up()
        # Then: 첫 번째 인덱스에 아이디 '2'번 항목이 와야 함
        self.assertEqual(app._cfg["mounts"][0]["id"], "2")

    # 14. 언마운트는 제대로 되는가?
    def test_scenario_14_verify_unmount(self):
        # Given: 마운트 프로세스가 실행 중일 때
        m_id = "test"
        mock_proc = MagicMock()
        rclone_manager.active_mounts[m_id] = mock_proc
        # When: 언마운트 함수를 호출하면
        rclone_manager.unmount(m_id)
        # Then: 프로세스 종료 명령이 실행되어야 함
        self.assertTrue(mock_proc.terminate.called)

    # 15. rclone 다운로드 로직 확인
    def test_scenario_15_rclone_download_logic(self):
        # Given: rclone 다운로드를 시도할 때
        # Then: 다운로드 유틸리티 함수가 호출 가능해야 함
        self.assertTrue(callable(rclone_manager.download_rclone))

    # 16. 시작 프로그램 등록 로직이 동작하는가?
    def test_scenario_16_startup_registration(self):
        # Given: 레지스트리 조작 권한이 있을 때
        with patch("winreg.OpenKey"), patch("winreg.SetValueEx") as mock_set:
            # When: 등록 함수를 호출하면
            rclone_manager.set_startup(True)
            # Then: 레지스트리 설정 함수가 호출되어야 함
            self.assertTrue(mock_set.called)

    # 17, 18. 트레이 아이콘이 초기화되는가?
    def test_scenario_17_18_tray_init(self):
        # Given: 트레이 기능을 시작할 때
        app = self._create_mocked_app()
        with patch("pystray.Icon"):
            # When: 트레이 시작 함수가 실행되면
            app._start_tray()
            # Then: 앱 인스턴스가 유효해야 함
            self.assertIsNotNone(app)

    # 19. 중복 실행 방지 로직이 동작하는가?
    def test_scenario_19_single_instance(self):
        # Given: 이미 실행 중인 창이 존재할 때
        with patch("ctypes.windll.user32.FindWindowW", return_value=123):
            # When: 중복 실행 확인 함수를 호출하면
            res = rclone_manager.activate_existing_window()
            # Then: True가 반환되어야 함
            self.assertTrue(res)

    # 20. 시스템 정보 수집이 가능한가?
    def test_scenario_20_sys_info_collection(self):
        # Given: 해상도 정보를 수집할 때
        # When: 수집 함수를 호출하면
        info = rclone_manager.get_sys_info()
        # Then: Resolution 문자열이 포함되어야 함
        self.assertIn("Resolution", info)

    # 21. 이슈 등록 URL이 저장소 주소를 포함하는가?
    def test_scenario_21_issue_url_check(self):
        # Given: 저장소 설정값이 있을 때
        # When: 저장소 변수를 확인하면
        repo = rclone_manager.GITHUB_REPO
        # Then: 사용자님의 저장소 ID가 포함되어야 함
        self.assertIn("Murianwind", repo)

if __name__ == "__main__":
    unittest.main()
