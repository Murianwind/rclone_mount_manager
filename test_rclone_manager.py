import unittest
import json
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

    # 1. rclone를 제대로 불러오는가?
    def test_scenario_01_load_rclone_executable(self):
        # Given: rclone 경로가 설정된 경우
        cfg = {"rclone_path": "C:\\fake\\rclone.exe"}
        # When: rclone 실행 파일을 가져오려 할 때
        with patch("pathlib.Path.exists", return_value=True):
            exe = rclone_manager.get_rclone_exe(cfg)
        # Then: 설정된 경로가 반환되어야 함
        self.assertEqual(str(exe), "C:\\fake\\rclone.exe")

    # 2. conf 파일의 내용이 제대로 불려오는가?
    def test_scenario_02_parse_rclone_conf_correctly(self):
        # Given: 특정 섹션이 포함된 rclone.conf가 있을 때
        with patch("configparser.ConfigParser.read", return_value=None):
            with patch("configparser.ConfigParser.sections", return_value=["my-drive"]):
                with patch("configparser.ConfigParser.get", return_value="drive"):
                    # When: 파일을 파싱하면
                    remotes = rclone_manager.parse_rclone_conf(Path("fake.conf"))
        # Then: 리모트 정보가 리스트로 반환되어야 함
        self.assertEqual(remotes[0]["name"], "my-drive")

    # 3. 등록된 conf에서 마운트를 추가할 수 있는가?
    # 9. 추가한 마운트를 저장이 되어서 동작하는가?
    def test_scenario_03_09_add_and_save_new_mount(self):
        # Given: 앱이 실행 중인 상태에서
        root = App(); root.withdraw()
        dlg = MountDialog(root, mount={"remote": "my-drive"}, app_cfg=self.sample_cfg)
        # When: 드라이브 문자를 설정하고 저장을 누르면
        dlg._drive.set("X:")
        dlg._save()
        # Then: 결과 객체가 생성되어야 함
        self.assertIsNotNone(dlg.result)
        self.assertEqual(dlg.result["drive"], "X:")
        root.destroy()

    # 4. 연결 테스트는 루트와 서브 디렉토리에서 각각 성공/실패를 제대로 출력하는가?
    @patch("subprocess.run")
    def test_scenario_04_connection_test_feedback(self, mock_run):
        # Given: 연결 테스트를 수행하려 할 때
        root = App(); root.withdraw()
        dlg = MountDialog(root, mount={"remote": "gd", "remote_path": "sub"}, app_cfg=self.sample_cfg)
        # When & Then: 성공 응답 시
        mock_run.return_value = MagicMock(returncode=0)
        dlg._test()
        # When & Then: 실패 응답 시
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        dlg._test()
        root.destroy()

    # 5. 잘못된 리모트 이름을 식별하는가?
    def test_scenario_05_detect_empty_remote_name(self):
        # Given: 리모트 이름이 비어있는 상태
        root = App(); root.withdraw()
        dlg = MountDialog(root, mount={"remote": ""}, app_cfg=self.sample_cfg)
        # When: 저장을 시도하면
        with patch("tkinter.messagebox.showwarning") as mock_msg:
            dlg._save()
            # Then: 경고 창이 출력되어야 함
            mock_msg.assert_called_with("오류", "리모트 이름 필수")
        root.destroy()

    # 6. 드라이브 문자 충돌을 식별하고 에러메시지를 출력하는가?
    def test_scenario_06_detect_drive_letter_conflict(self):
        # Given: 이미 'Z:' 드라이브가 사용 중인 설정일 때
        cfg = {"mounts": [{"id": "1", "drive": "Z:", "remote": "old"}]}
        root = App(); root.withdraw()
        dlg = MountDialog(root, mount={"remote": "new"}, app_cfg=cfg)
        # When: 동일한 'Z:'로 저장을 시도하면
        dlg._drive.set("Z:")
        with patch("tkinter.messagebox.showerror") as mock_msg:
            dlg._save()
            # Then: 충돌 에러가 발생해야 함
            self.assertTrue(mock_msg.called)
        root.destroy()

    # 7. 추가플래그중 잘못입력되거나 불가능한 태그 식별/수정/에러
    def test_scenario_07_validate_extra_flags(self):
        # Given: 금지된 플래그(--volname)를 입력했을 때
        root = App(); root.withdraw()
        dlg = MountDialog(root, mount={"remote": "gd"}, app_cfg=self.sample_cfg)
        dlg._extra_text.insert("1.0", "--volname MyDrive")
        # When: 저장을 시도하면
        with patch("tkinter.messagebox.showerror") as mock_msg:
            dlg._save()
            # Then: 에러 창이 출력되어야 함
            self.assertTrue(mock_msg.called)
        root.destroy()

    # 8. 시작 시 자동 마운트는 제대로 동작하는가?
    @patch("rclone_manager.do_mount")
    def test_scenario_08_verify_auto_mount_on_init(self, mock_mount):
        # Given: 시작 시 자동 마운트가 설정된 마운트가 있을 때
        cfg = {"auto_mount": True, "mounts": [{"id": "m1", "auto_mount": True, "remote": "gd", "drive": "X:"}]}
        # When: 앱이 초기화되면
        with patch("rclone_manager.load_config", return_value=cfg):
            with patch("rclone_manager.get_rclone_exe", return_value=Path("rclone.exe")):
                with patch("pathlib.Path.exists", return_value=True):
                    app = App(); app.withdraw()
                    app.after(1600, lambda: app.destroy())
                    app.mainloop()
        # Then: do_mount 함수가 호출되어야 함
        self.assertTrue(mock_mount.called)

    # 10. 편집한 내용이 저장이 되어서 동작하는가?
    def test_scenario_10_verify_edit_persistence(self):
        # Given: 기존 마운트를 편집할 때
        cfg = {"mounts": [{"id": "m1", "remote": "gd", "drive": "X:"}]}
        root = App(); root.withdraw()
        dlg = MountDialog(root, mount=cfg["mounts"][0], app_cfg=cfg)
        # When: 드라이브 문자를 변경하고 저장하면
        dlg._drive.set("Y:")
        dlg._save()
        # Then: 변경된 값이 결과에 반영되어야 함
        self.assertEqual(dlg.result["drive"], "Y:")
        root.destroy()

    # 11. 삭제를 하면 mount.json에 내용이 삭제되는가?
    def test_scenario_11_verify_deletion_from_config(self):
        # Given: 특정 마운트가 등록되어 있을 때
        app = App(); app.withdraw()
        app._cfg["mounts"] = [{"id": "m1", "remote": "gd"}]
        # When: 삭제를 실행하면 (확인 창 예 클릭)
        with patch("tkinter.messagebox.askyesno", return_value=True):
            with patch.object(app._tree, "selection", return_value=("m1",)):
                app._del()
        # Then: 설정 리스트에서 제거되어야 함
        self.assertEqual(len(app._cfg["mounts"]), 0)
        app.destroy()

    # 12. 동일한 리모트나 동일한 경로의 마운트를 등록하려고 하면 에러메시지가 출력되는가?
    def test_scenario_12_detect_duplicate_mount_path(self):
        # Given: 이미 'gd:sub' 경로가 등록되어 있을 때
        cfg = {"mounts": [{"id": "1", "remote": "gd", "remote_path": "sub"}]}
        root = App(); root.withdraw()
        dlg = MountDialog(root, mount={"remote": "gd", "remote_path": "sub"}, app_cfg=cfg)
        # When: 동일한 경로로 저장을 시도하면
        with patch("tkinter.messagebox.showerror") as mock_msg:
            dlg._save()
            # Then: 중복 에러가 발생해야 함
            self.assertTrue(mock_msg.called)
        root.destroy()

    # 13. 목록에서 순서는 제대로 변경되는가?
    def test_scenario_13_verify_order_movement(self):
        # Given: 마운트 목록이 여러 개 있을 때
        app = App(); app.withdraw()
        app._cfg["mounts"] = [{"id": "1"}, {"id": "2"}]
        # When: 두 번째 항목을 위로 이동시키면
        with patch.object(app._tree, "selection", return_value=("2",)):
            app._move_up()
        # Then: 리스트 순서가 바뀌어야 함
        self.assertEqual(app._cfg["mounts"][0]["id"], "2")
        app.destroy()

    # 14. 언마운트는 제대로 되는가?
    def test_scenario_14_verify_unmount_termination(self):
        # Given: 활성화된 마운트 프로세스가 있을 때
        m_id = "test"
        mock_proc = MagicMock()
        rclone_manager.active_mounts[m_id] = mock_proc
        # When: 언마운트를 수행하면
        rclone_manager.unmount(m_id)
        # Then: 프로세스 종료 함수가 호출되어야 함
        self.assertTrue(mock_proc.terminate.called)

    # 15. 업데이트는 제대로 되는가?
    @patch("rclone_manager.download_rclone", return_value=True)
    def test_scenario_15_update_workflow(self, mock_dl):
        # Given: 새로운 버전 업데이트를 수행할 때
        app = App(); app.withdraw()
        with patch("rclone_manager.get_latest_version", return_value="1.66.0"):
            # When: 업데이트 명령을 실행하면
            app._manual_up()
            app.after(500, lambda: app.destroy())
            app.mainloop()
        # Then: 다운로드 함수가 실행되어야 함
        self.assertTrue(mock_dl.called)

    # 16. 시작 시 자동 실행은 제대로 되는가?
    def test_scenario_16_verify_startup_registry_set(self):
        # Given: 시작 프로그램 등록을 활성화하려 할 때
        with patch("winreg.OpenKey"), patch("winreg.SetValueEx") as mock_set:
            # When: 레지스트리 설정 함수를 실행하면
            rclone_manager.set_startup(True)
        # Then: 윈도우 레지스트리에 값이 기록되어야 함
        self.assertTrue(mock_set.called)

    # 17. 트레이에서 마운트, 언마운트는 제대로 되는가?
    # 18. 등록된 마운트는 트레이에 올바로 출력되고 상태가 정확히 반영되는가?
    def test_scenario_17_18_tray_status_and_menu(self):
        # Given: 등록된 마운트 정보가 있고 앱이 트레이를 시작할 때
        app = App(); app.withdraw()
        app._cfg["mounts"] = [{"id": "m1", "remote": "gd", "drive": "X:"}]
        # When: 트레이 아이콘을 생성하면
        with patch("pystray.Icon"):
            app._start_tray()
            # Then: 설정이 로드되어 메뉴 아이템이 준비되어야 함
            self.assertIsNotNone(app._tray)
        app.destroy()

if __name__ == "__main__":
    unittest.main()
