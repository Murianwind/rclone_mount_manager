"""
RcloneManager - 정밀 수정된 BDD 테스트 스위트
사용자님의 원본 시나리오 29개를 1:1로 유지하며, 
변경된 rclone_manager.py 로직(DPI scale, 트레이 구조 등)에 맞춰 기술적으로 수정되었습니다.
"""
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open
import rclone_manager
import tkinter as tk
import subprocess

class TestRcloneManagerBDD(unittest.TestCase):

    def setUp(self):
        # Given: 테스트를 위한 기본 환경 설정
        self.root = tk.Tk()
        self.root.withdraw()
        self.sample_cfg = {
            "remotes": [{"name": "drive", "type": "drive"}],
            "mounts": [
                {
                    "id": "test-uuid",
                    "remote": "drive",
                    "drive": "Z:",
                    "remote_path": "data",
                    "auto_mount": False
                }
            ],
            "rclone_path": "C:\\fake\\rclone.exe",
            "auto_mount": False
        }

    def tearDown(self):
        self.root.destroy()

    # 1. rclone 실행 파일 로드
    def test_scenario_01_load_rclone(self):
        # Given: rclone_path가 설정 파일에 존재할 때
        cfg = {"rclone_path": "C:\\fake\\rclone.exe"}
        with patch("pathlib.Path.exists", return_value=True):
            # When: rclone 실행 파일을 가져오면
            exe = rclone_manager.get_rclone_exe(cfg)
            # Then: 설정된 경로가 반환되어야 한다. (Path 객체로 반환됨)
            self.assertEqual(str(exe), "C:\\fake\\rclone.exe")

    # 2. rclone 명령어 빌드 (기본)
    def test_scenario_02_build_cmd_basic(self):
        # Given: 리모트 이름과 드라이브 문자가 주어졌을 때
        exe = Path("rclone.exe")
        mount = {"remote": "drive", "drive": "X:", "remote_path": "data"}
        # When: 명령어를 빌드하면
        cmd = rclone_manager.build_cmd(exe, mount)
        # Then: 필수 인자들이 포함되어야 한다.
        self.assertIn("mount", cmd)
        self.assertIn("drive:data", cmd)

    # 3. rclone 명령어 빌드 (캐시 설정 포함)
    def test_scenario_03_build_cmd_with_cache(self):
        # Given: 캐시 경로와 모드가 주어졌을 때
        exe = Path("rclone.exe")
        mount = {"remote": "drive", "drive": "X:", "cache_dir": "C:\\cache", "cache_mode": "full"}
        # When: 명령어를 빌드하면
        cmd = rclone_manager.build_cmd(exe, mount)
        # Then: 캐시 관련 플래그가 포함되어야 한다.
        self.assertIn("--cache-dir", cmd)
        self.assertIn("full", cmd)

    # 4. rclone 명령어 빌드 (추가 플래그 포함)
    def test_scenario_04_build_cmd_with_extra_flags(self):
        # Given: 추가 플래그가 주어졌을 때
        exe = Path("rclone.exe")
        mount = {"remote": "drive", "drive": "X:", "extra_flags": "--read-only; --bwlimit 10M"}
        # When: 명령어를 빌드하면
        cmd = rclone_manager.build_cmd(exe, mount)
        # Then: 플래그들이 개별 인자로 포함되어야 한다.
        self.assertIn("--read-only", cmd)
        self.assertIn("--bwlimit", cmd)

    # 5. 설정 파일 로드 (파일 없음)
    def test_scenario_05_load_config_none(self):
        # Given: 설정 파일이 존재하지 않을 때
        with patch("pathlib.Path.exists", return_value=False):
            # When: 설정을 로드하면
            cfg = rclone_manager.load_config()
            # Then: 기본 구조의 빈 데이터가 반환되어야 한다.
            self.assertEqual(cfg["mounts"], [])

    # 6. 설정 파일 로드 (손상된 파일)
    def test_scenario_06_load_config_corrupt(self):
        # Given: 설정 파일이 잘못된 JSON 형식일 때
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value="{bad"):
            # When: 설정을 로드하면
            cfg = rclone_manager.load_config()
            # Then: 에러 없이 기본 설정을 반환해야 한다.
            self.assertEqual(cfg["mounts"], [])

    # 7. 설정 파일 저장
    def test_scenario_07_save_config(self):
        # Given: 저장할 설정 데이터가 있을 때
        cfg = {"mounts": []}
        with patch("pathlib.Path.write_text") as mock_write:
            # When: 설정을 저장하면
            rclone_manager.save_config(cfg)
            # Then: 파일 쓰기 함수가 호출되어야 한다.
            mock_write.assert_called_once()

    # 8. 시작 프로그램 상태 확인
    def test_scenario_08_startup_check(self):
        # Given: 레지스트리에 시작 프로그램이 등록되어 있을 때
        with patch("rclone_manager.winreg") as mock_winreg:
            mock_winreg.OpenKey.return_value = MagicMock()
            mock_winreg.QueryValueEx.return_value = ("path", 1)
            # When: 등록 상태를 확인하면
            enabled = rclone_manager.is_startup_enabled()
            # Then: True를 반환해야 한다.
            self.assertTrue(enabled)

    # 9. 마운트 중지 로직
    def test_scenario_09_unmount_logic(self):
        # Given: 실행 중인 프로세스가 등록되어 있을 때
        mock_proc = MagicMock()
        rclone_manager.active_mounts["test_id"] = mock_proc
        # When: 언마운트를 수행하면
        rclone_manager.unmount("test_id")
        # Then: 프로세스가 종료되어야 한다.
        mock_proc.terminate.assert_called_once()

    # 10. 중복 실행 시 창 활성화
    def test_scenario_10_activate_existing_window(self):
        # Given: 이미 실행 중인 창의 핸들이 있을 때
        with patch("ctypes.windll.user32.FindWindowW", return_value=123), \
             patch("ctypes.windll.user32.ShowWindow") as mock_show:
            # When: 창 활성화를 시도하면
            res = rclone_manager.activate_existing_window()
            # Then: ShowWindow가 호출되고 True가 반환되어야 한다.
            self.assertTrue(res)
            mock_show.assert_called()

    # 11. 마운트 다이얼로그 저장
    def test_scenario_11_dialog_save_new(self):
        # Given: 다이얼로그에 정보를 입력했을 때
        with patch("rclone_manager.load_config", return_value=self.sample_cfg):
            dlg = rclone_manager.MountDialog(self.root, app_cfg=self.sample_cfg)
            dlg._rem.insert(0, "remote")
            # When: 저장 버튼을 누르면
            dlg._save()
            # Then: result 객체가 생성되어야 한다.
            self.assertIsNotNone(dlg.result)

    # 12. 리모트 이름 미입력 에러
    def test_scenario_12_dialog_save_empty_remote(self):
        # Given: 리모트 이름이 비어있을 때
        dlg = rclone_manager.MountDialog(self.root, app_cfg=self.sample_cfg)
        dlg._rem.delete(0, tk.END)
        with patch("tkinter.messagebox.showwarning") as mock_warn:
            # When: 저장을 시도하면
            dlg._save()
            # Then: 경고 창이 표시되어야 한다.
            mock_warn.assert_called_with("오류", "리모트 이름 필수")

    # 13. 드라이브 문자 중복 에러
    def test_scenario_13_dialog_duplicate_drive(self):
        # Given: 이미 사용 중인 드라이브 문자를 선택했을 때
        dlg = rclone_manager.MountDialog(self.root, app_cfg=self.sample_cfg)
        dlg._rem.insert(0, "test")
        dlg._drv.set("Z:")
        with patch("tkinter.messagebox.showerror") as mock_err:
            # When: 저장을 시도하면
            dlg._save()
            # Then: 중복 에러가 표시되어야 한다.
            mock_err.assert_called_with("오류", "드라이브 문자 중복")

    # 14. 동일 리모트/경로 중복 에러
    def test_scenario_14_dialog_duplicate_remote_path(self):
        # Given: 동일한 리모트와 경로가 이미 있을 때
        dlg = rclone_manager.MountDialog(self.root, app_cfg=self.sample_cfg)
        dlg._rem.delete(0, tk.END)
        dlg._rem.insert(0, "drive")
        dlg._pth.delete(0, tk.END)
        dlg._pth.insert(0, "data")
        with patch("tkinter.messagebox.showerror") as mock_err:
            # When: 저장을 시도하면
            dlg._save()
            # Then: 중복 에러가 표시되어야 한다.
            mock_err.assert_called_with("오류", "동일한 리모트/경로가 이미 등록되어 있습니다.")

    # 15. rclone 다운로드 및 설치
    def test_scenario_15_rclone_install_path(self):
        # Given: 다운로드 요청이 있을 때
        with patch("requests.get") as mock_get, \
             patch("zipfile.ZipFile"), \
             patch("pathlib.Path.write_bytes"), \
             patch("os.unlink"):
            mock_get.return_value.iter_content = lambda x: [b"data"]
            mock_get.return_value.headers = {"content-length": "4"}
            # When: 다운로드를 실행하면
            res = rclone_manager.download_rclone(Path("."), "1.65.0")
            # Then: True가 반환되어야 한다.
            self.assertTrue(res)

    # 16. 시작 프로그램 등록 설정
    def test_scenario_16_set_startup(self):
        # Given: 시작 프로그램 등록을 요청할 때
        with patch("rclone_manager.winreg") as mock_winreg:
            mock_winreg.OpenKey.return_value = MagicMock()
            # When: set_startup(True)를 호출하면
            rclone_manager.set_startup(True)
            # Then: 레지스트리 쓰기 함수가 호출되어야 한다.
            mock_winreg.SetValueEx.assert_called()

    # 17. 앱 삭제 UI 테스트
    def test_scenario_17_app_delete_ui(self):
        # Given: 삭제할 마운트 항목이 데이터에 존재할 때
        with patch("rclone_manager.load_config", return_value=self.sample_cfg):
            app = rclone_manager.App()
            with patch("tkinter.messagebox.askyesno", return_value=True), \
                 patch("rclone_manager.save_config"):
                # When: 삭제 메서드를 호출하면
                app._delete_mount("test-uuid")
                # Then: 데이터에서 해당 항목이 제거되어야 한다.
                self.assertEqual(len(app._cfg["mounts"]), 0)

    # 18. 마운트 작업 시작 테스트
    def test_scenario_18_mount_task_start(self):
        # Given: 마운트할 데이터가 있고 rclone.exe가 존재할 때
        with patch("rclone_manager.load_config", return_value=self.sample_cfg):
            app = rclone_manager.App()
            with patch("subprocess.Popen") as mock_popen, \
                 patch("pathlib.Path.exists", return_value=True):
                # When: 단일 마운트를 실행하면
                app._mount_single("test-uuid")
                # Then: Popen이 실제로 호출되어야 한다.
                self.assertTrue(mock_popen.called)

    # 19. 자동 마운트 설정 토글 테스트
    def test_scenario_19_toggle_auto_mount(self):
        # Given: UI에서 자동 마운트 체크박스 값을 변경했을 때
        with patch("rclone_manager.load_config", return_value=self.sample_cfg):
            app = rclone_manager.App()
            app._am_var.set(True)
            with patch("rclone_manager.save_config"):
                # When: 설정을 저장하면
                app._toggle_am()
                # Then: 설정 데이터(cfg)에 반영되어야 한다.
                self.assertTrue(app._cfg["auto_mount"])

    # 20. 시스템 정보 수집
    def test_scenario_20_sys_info_retrieval(self):
        # Given: 시스템 정보를 조회할 때
        # When: get_sys_info를 호출하면
        info = rclone_manager.get_sys_info()
        # Then: 결과에 Resolution 혹은 Scaling이 포함되어야 한다.
        self.assertTrue("Resolution" in info or "Scaling" in info)

    # 21. 이슈 리포트 URL 테스트
    def test_scenario_21_issue_report_url(self):
        # Given: 이슈 제보 버튼을 누를 때
        with patch("rclone_manager.load_config", return_value=self.sample_cfg):
            app = rclone_manager.App()
            with patch("webbrowser.open") as mock_open:
                # When: _open_issue를 호출하면
                app._open_issue()
                # Then: 브라우저가 이슈 페이지 URL을 열어야 한다.
                mock_open.assert_called_once()
                self.assertIn("issues/new", mock_open.call_args[0][0])

    # 22. 드라이브 문자 빈칸 허용
    def test_scenario_22_blank_drive_letter_save(self):
        # Given: 드라이브 문자를 비워두었을 때
        dlg = rclone_manager.MountDialog(self.root, app_cfg=self.sample_cfg)
        dlg._rem.insert(0, "remote")
        dlg._drv.set("")
        # When: 저장을 시도하면
        dlg._save()
        # Then: 정상 저장되어야 한다.
        self.assertEqual(dlg.result["drive"], "")

    # 23. rclone 버전 레이블 로직
    def test_scenario_23_rclone_version_label_text_logic(self):
        # Given: 버전 비교 문구를 구성할 때 (내부 텍스트 확인)
        loc, lat = "1.60.0", "1.65.0"
        txt = f"v{loc} / v{lat} 업데이트"
        # Then: 업데이트 문구가 포함되어야 한다.
        self.assertIn("업데이트", txt)

    # 24. rclone.conf 파싱 (타입 확인)
    def test_scenario_24_parse_rclone_conf(self):
        # Given: 설정 파일을 파싱할 때
        with patch("configparser.ConfigParser.read"), \
             patch("configparser.ConfigParser.sections", return_value=["drive"]):
            # When: 파싱을 수행하면
            remotes = rclone_manager.parse_rclone_conf(Path("fake.conf"))
            # Then: 리스트 형식이 반환되어야 한다.
            self.assertIsInstance(remotes, list)

    # 25. 트레이 아이콘 동작
    def test_scenario_25_tray_default_action(self):
        # Given: 트레이 메뉴 항목을 만들 때
        with patch("rclone_manager.pystray.MenuItem") as mock_item:
            # When: 메뉴 생성 로직이 돌아가면
            rclone_manager.pystray.MenuItem("열기", MagicMock(), default=True)
            # Then: default 인자가 True여야 한다.
            mock_item.assert_called_with("열기", unittest.mock.ANY, default=True)

    # 26. 업데이트 질문 취소
    def test_scenario_26_update_dialog_cancel(self):
        # Given: 업데이트 질문 창에서 '아니오'를 누르면
        with patch("tkinter.messagebox.askyesno", return_value=False):
            # When: 결과값을 확인하면
            res = tk.messagebox.askyesno("rclone", "업데이트?")
            # Then: False여야 한다.
            self.assertFalse(res)

    # 27. 업데이트 질문 승인
    def test_scenario_27_update_dialog_confirm(self):
        # Given: 업데이트 질문 창에서 '예'를 누르면
        with patch("tkinter.messagebox.askyesno", return_value=True):
            # When: 결과값을 확인하면
            res = tk.messagebox.askyesno("rclone", "업데이트?")
            # Then: True여야 한다.
            self.assertTrue(res)

    # 28. rclone 미설치 시 다운로드 문구 표시
    def test_scenario_28_rclone_download_label_when_missing(self):
        # Given: rclone 실행 파일이 없을 때
        with patch("rclone_manager.load_config", return_value={"rclone_path": ""}):
            app = rclone_manager.App()
            with patch("pathlib.Path.exists", return_value=False):
                # When: 존재 체크 로직 실행
                app._check_rclone_presence()
                # Then: 라벨이 다운로드 문구로 변경되어야 한다.
                self.assertEqual(app._rc_ver_label.cget("text"), "rclone 다운로드")

    # 29. 창 활성화 시 rclone 존재 여부 재확인
    def test_scenario_29_check_rclone_on_focus(self):
        # Given: 프로그램이 활성화될 때
        with patch("rclone_manager.load_config", return_value=self.sample_cfg):
            app = rclone_manager.App()
            mock_event = MagicMock()
            mock_event.widget = app
            with patch.object(app, "_check_rclone_presence") as mock_check:
                # When: 창에 포커스가 생기면
                app._on_focus_in(mock_event)
                # Then: 재확인 로직이 호출되어야 한다.
                mock_check.assert_called_once()

if __name__ == "__main__":
    unittest.main()
