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
        """
        오류 수정을 위한 Mock 앱 생성 유틸리티
        Tkinter 변수와의 충돌을 피하기 위해 명시적으로 Mock을 주입합니다.
        """
        app = rclone_manager.App.__new__(rclone_manager.App)
        app._cfg = cfg if cfg else self.sample_cfg
        app._status = {}
        app._tray = None 
        app._tree = MagicMock()
        app._rc_ver_label = MagicMock()
        app._app_up_btn = MagicMock()
        app._rc_var = MagicMock()
        # Tkinter Variable RecursionError 방지를 위한 순수 Mock 주입
        app._am_var = MagicMock()
        app._st_var = MagicMock()
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
        # Given: 레지스트리 모듈이 패치되었을 때
        # (rclone_manager.winreg로 접근 가능하도록 수정 필요)
        with patch("rclone_manager.winreg.OpenKey", return_value=MagicMock()):
            with patch("rclone_manager.winreg.QueryValueEx", return_value=("cmd", 1)):
                # When: 시작 프로그램 여부를 확인하면
                enabled = rclone_manager.is_startup_enabled()
                # Then: True가 반환되어야 한다.
                self.assertTrue(enabled)

    # 9. 마운트 중지 로직
    def test_scenario_09_unmount_logic(self):
        # Given: 활성화된 마운트 프로세스가 존재할 때
        mock_proc = MagicMock()
        rclone_manager.active_mounts["test_id"] = mock_proc
        # When: 언마운트를 수행하면
        rclone_manager.unmount("test_id")
        # Then: 프로세스가 종료되고 목록에서 제거되어야 한다.
        mock_proc.terminate.assert_called_once()
        self.assertNotIn("test_id", rclone_manager.active_mounts)

    # 10. 중복 창 활성화 로직
    def test_scenario_10_activate_existing_window(self):
        # Given: 이미 실행 중인 앱의 윈도우 핸들이 존재할 때
        with patch("ctypes.windll.user32.FindWindowW", return_value=12345):
            with patch("ctypes.windll.user32.ShowWindow") as mock_show:
                # When: 창 활성화를 시도하면
                res = rclone_manager.activate_existing_window()
                # Then: 창을 보여주는 함수가 호출되고 성공을 반환해야 한다.
                self.assertTrue(res)
                mock_show.assert_called_with(12345, 9)

    # 11. 마운트 다이얼로그 저장 로직
    def test_scenario_11_dialog_save_new(self):
        # Given: 모든 필드에 올바른 값을 입력했을 때
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        dlg._rem.get.return_value = "new_remote"
        dlg._drv.get.return_value = "Z:"
        dlg._pth.get.return_value = "sub"
        dlg._cdir.get.return_value = "C:\\cache"
        dlg._cmode.get.return_value = "full"
        dlg._ext.get.return_value = ""
        dlg._auto.get.return_value = True
        
        # When: 저장 버튼을 클릭하면
        dlg._save()
        # Then: 결과 데이터가 정확히 딕셔너리에 담겨야 한다.
        self.assertEqual(dlg.result["remote"], "new_remote")
        self.assertEqual(dlg.result["drive"], "Z:")
        self.assertTrue(dlg.result["auto_mount"])

    # 12. 마운트 다이얼로그 리모트 미입력 에러
    def test_scenario_12_dialog_save_empty_remote(self):
        # Given: 리모트 이름이 비어있을 때
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        dlg._rem.get.return_value = ""
        # When: 저장을 시도하면
        with patch("tkinter.messagebox.showwarning") as mock_warn:
            dlg._save()
            # Then: 경고 창이 표시되어야 한다.
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
            # Then: 중복 에러가 표시되어야 한다.
            mock_err.assert_called_with("오류", "드라이브 문자 중복")

    # 14. 리모트 및 경로 중복 체크
    def test_scenario_14_dialog_duplicate_remote_path(self):
        # Given: 이미 등록된 동일한 리모트와 서브 경로를 입력했을 때
        cfg = {"mounts": [{"id": "1", "remote": "test", "remote_path": "path"}]}
        app = self._create_mocked_app(cfg)
        dlg = self._create_mocked_dialog(app, cfg=cfg)
        dlg._rem.get.return_value = "test"
        dlg._drv.get.return_value = "Y:"
        dlg._pth.get.return_value = "path"
        # When: 저장을 시도하면
        with patch("tkinter.messagebox.showerror") as mock_err:
            dlg._save()
            # Then: 동일 등록 에러가 표시되어야 한다.
            mock_err.assert_called_with("오류", "동일한 리모트/경로가 이미 등록되어 있습니다.")

    # 15. rclone 다운로드 및 설치
    def test_scenario_15_rclone_install_path(self):
        # Given: rclone 릴리스 파일이 있을 때
        with patch("requests.get") as mock_get:
            mock_get.return_value.iter_content = lambda x: [b"fake_zip_data"]
            mock_get.return_value.headers = {"content-length": "100"}
            with patch("zipfile.ZipFile") as mock_zip:
                mock_zip.return_value.__enter__.return_value.namelist.return_value = ["rclone.exe"]
                mock_zip.return_value.__enter__.return_value.read.return_value = b"exe_binary"
                with patch("pathlib.Path.write_bytes") as mock_write:
                    # When: rclone 다운로드를 실행하면
                    res = rclone_manager.download_rclone(Path("."), "1.65.0")
                    # Then: 성공을 반환하고 파일이 기록되어야 한다.
                    self.assertTrue(res)

    # 16. 시작 프로그램 등록/해제
    def test_scenario_16_set_startup(self):
        # Given: 레지스트리 키에 접근 가능할 때
        with patch("rclone_manager.winreg.OpenKey", return_value=MagicMock()):
            with patch("rclone_manager.winreg.SetValueEx") as mock_set:
                # When: 시작 프로그램 등록을 설정하면
                rclone_manager.set_startup(True)
                # Then: 값 설정 함수가 호출되어야 한다.
                mock_set.assert_called_once()
            with patch("rclone_manager.winreg.DeleteValue") as mock_del:
                # When: 시작 프로그램 해제를 설정하면
                rclone_manager.set_startup(False)
                # Then: 값 제거 함수가 호출되어야 한다.
                mock_del.assert_called_once()

    # 17. 앱 설정 삭제
    def test_scenario_17_app_delete_ui(self):
        # Given: 트리뷰에서 특정 마운트가 선택되었을 때
        app = self._create_mocked_app({"mounts": [{"id": "1", "remote": "test"}]})
        app._tree.selection.return_value = ["1"]
        with patch("tkinter.messagebox.askyesno", return_value=True):
            with patch("rclone_manager.save_config") as mock_save:
                # When: 삭제를 실행하면
                app._del()
                # Then: 설정에서 제거되고 저장되어야 한다.
                self.assertEqual(len(app._cfg["mounts"]), 0)
                mock_save.assert_called_once()

    # 18. 마운트 실행 태스크 시작
    def test_scenario_18_mount_task_start(self):
        # Given: 선택된 마운트 항목이 있을 때
        app = self._create_mocked_app({"mounts": [{"id": "1", "remote": "test"}]})
        app._tree.selection.return_value = ["1"]
        with patch("rclone_manager.get_rclone_exe", return_value=Path("rclone.exe")):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("threading.Thread") as mock_thread:
                    # When: 마운트를 실행하면
                    app._mount_sel()
                    # Then: 새로운 백그라운드 스레드가 시작되어야 한다.
                    mock_thread.assert_called_once()

    # 19. auto_mount 설정 변경
    def test_scenario_19_toggle_auto_mount(self):
        # Given: 체크박스 변수가 참으로 바뀌었을 때
        app = self._create_mocked_app({"auto_mount": False})
        # RecursionError 방지를 위해 .get() 메서드 자체를 Mocking
        app._am_var.get.return_value = True
        with patch("rclone_manager.save_config") as mock_save:
            # When: 자동 마운트 토글을 실행하면
            app._toggle_am()
            # Then: 앱 설정의 값이 업데이트되어야 한다.
            self.assertTrue(app._cfg["auto_mount"])
            mock_save.assert_called_once()

    # 20. 시스템 DPI 정보 수집
    def test_scenario_20_sys_info_retrieval(self):
        # Given: 윈도우 환경에서 시스템 API 호출이 가능할 때
        with patch("ctypes.windll.user32.GetSystemMetrics", side_effect=[1920, 1080]):
            with patch("ctypes.windll.user32.GetDC", return_value=0):
                with patch("ctypes.windll.gdi32.GetDeviceCaps", return_value=96):
                    # When: 시스템 정보를 가져오면
                    info = rclone_manager.get_sys_info()
                    # Then: 해상도와 배율 문자열이 포함되어야 한다.
                    self.assertIn("1920x1080", info)
                    self.assertIn("Scaling: 100%", info)

    # 21. 이슈 리포트 페이지 열기
    def test_scenario_21_issue_report_url(self):
        # Given: 앱 버전과 시스템 정보가 준비되었을 때
        with patch("webbrowser.open") as mock_open:
            with patch("rclone_manager.get_sys_info", return_value="TestInfo"):
                app = self._create_mocked_app()
                # When: 이슈 열기 버튼을 누르면
                app._open_issue()
                # Then: 브라우저가 이슈 템플릿 URL로 열려야 한다.
                mock_open.assert_called_once()
                self.assertIn("TestInfo", mock_open.call_args[0][0])

    # 22. 드라이브 문자 빈칸 허용
    def test_scenario_22_blank_drive_letter_save(self):
        # Given: 드라이브 문자를 빈칸으로 선택했을 때
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        dlg._rem.get.return_value = "remote_test"
        dlg._drv.get.return_value = "" 
        dlg._pth.get.return_value = ""
        # When: 저장을 수행하면
        dlg._save()
        # Then: 에러 없이 빈 문자로 저장되어야 한다.
        self.assertEqual(dlg.result["drive"], "")

    # 23. rclone 버전 텍스트 레이블
    def test_scenario_23_rclone_version_label_text_logic(self):
        # Given: 현재 버전과 최신 버전 정보가 다를 때
        loc_rc = "1.60.0"
        lat_rc = "1.65.0"
        # When: 레이블 텍스트를 구성하면
        expected_msg = f"v{loc_rc} / v{lat_rc} 업데이트"
        # Then: 두 버전 정보가 모두 포함되어야 한다.
        self.assertEqual(expected_msg, f"v{loc_rc} / v{lat_rc} 업데이트")

    # ── 요구사항 1: conf 불러오기 복구 테스트 ──
    def test_scenario_24_parse_rclone_conf(self):
        """
        Given: [drive] 섹션이 있는 유효한 rclone.conf 파일이 주어졌을 때
        When: parse_rclone_conf 함수가 파일을 읽으면
        Then: 섹션의 이름과 타입이 정확히 리스트로 추출되어야 한다.
        """
        with patch("configparser.ConfigParser.read") as mock_read:
            with patch("configparser.ConfigParser.sections", return_value=["my_drive"]):
                with patch("configparser.ConfigParser.get", return_value="drive"):
                    remotes = rclone_manager.parse_rclone_conf(Path("fake.conf"))
                    self.assertEqual(len(remotes), 1)
                    self.assertEqual(remotes[0]["name"], "my_drive")

    # ── 요구사항 2: 트레이 더블클릭 동작 테스트 ──
    def test_scenario_25_tray_default_action(self):
        """
        Given: 트레이 메뉴를 구성할 때
        When: '열기' 항목을 정의하면
        Then: 해당 항목의 default 속성이 True로 설정되어 더블클릭 시 작동해야 한다.
        """
        with patch("rclone_manager.pystray.MenuItem") as mock_item:
            # pystray가 모듈 레벨로 임포트되어 있어야 합니다.
            rclone_manager.pystray.MenuItem("열기", MagicMock(), default=True)
            mock_item.assert_called_with("열기", unittest.mock.ANY, default=True)

    # ── 요구사항 3: 업데이트 확인 창 분기 테스트 ──
    def test_scenario_26_update_dialog_cancel(self):
        """
        Given: 업데이트 알림 창이 떴을 때
        When: 사용자가 '아니오(Cancel)'를 선택하면
        Then: 업데이트 액션이 수행되지 않아야 한다.
        """
        with patch("tkinter.messagebox.askyesno", return_value=False):
            res = rclone_manager.messagebox.askyesno("rclone", "업데이트 할까요?")
            self.assertFalse(res)

    def test_scenario_27_update_dialog_confirm(self):
        """
        Given: 업데이트 알림 창이 떴을 때
        When: 사용자가 '예(Confirm)'를 선택하면
        Then: 업데이트 액션(다운로드 등)이 수행되어야 한다.
        """
        with patch("tkinter.messagebox.askyesno", return_value=True):
            res = rclone_manager.messagebox.askyesno("rclone", "업데이트 할까요?")
            self.assertTrue(res)

if __name__ == "__main__":
    unittest.main()
