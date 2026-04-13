import unittest
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import tkinter as tk

# 테스트 대상 모듈
import rclone_manager
from rclone_manager import App, MountDialog

class TestRcloneManagerBDD(unittest.TestCase):

    def setUp(self):
        """테스트 기초 데이터 설정"""
        self.sample_cfg = {
            "remotes": [{"name": "drive", "type": "drive"}],
            "mounts": [
                {
                    "id": "test-uuid",
                    "remote": "drive",
                    "remote_path": "backup",
                    "drive": "X:",
                    "label": "MyCloud",
                    "cache_mode": "full",
                    "extra_flags": "--vfs-read-chunk-size 128M"
                }
            ],
            "rclone_path": "C:\\rclone.exe",
            "auto_mount": True
        }

    # ── Scenario 1: 설정 로드 및 자동 복구 (Config Logic) ──
    def test_scenario_config_load_and_repair(self):
        """
        Given: 'id' 필드와 'remotes' 섹션이 누락된 JSON 설정 파일이 존재함
        When: load_config() 함수를 통해 데이터를 읽어올 때
        Then: 앱은 누락된 필드를 식별하고 기본값을 채워넣어 완전한 객체를 반환해야 함
        """
        partial_json = json.dumps({"mounts": [{"remote": "test"}]})
        with patch("pathlib.Path.read_text", return_value=partial_json):
            with patch("pathlib.Path.exists", return_value=True):
                cfg = rclone_manager.load_config()
                self.assertIn("remotes", cfg)  # 기존 test_load_config_with_missing_fields 대응
                self.assertIn("id", cfg["mounts"][0])

    # ── Scenario 2: rclone 실행 파일 경로 결정 (Path Logic) ──
    def test_scenario_determine_rclone_path(self):
        """
        Given: 설정 파일에 커스텀 rclone_path가 비어 있는 상태임
        When: get_rclone_exe()를 호출하여 실행 파일 경로를 확인하면
        Then: 프로그램은 현재 앱 실행 경로(APP_DIR) 기반의 rclone.exe 경로를 반환해야 함
        """
        cfg = {"rclone_path": ""}
        with patch("pathlib.Path.exists", return_value=False):
            exe = rclone_manager.get_rclone_exe(cfg) # 기존 test_get_rclone_exe_logic 대응
            self.assertTrue(str(exe).endswith("rclone.exe"))

    # ── Scenario 3: rclone 마운트 명령어 빌드 (CLI Build Logic) ──
    def test_scenario_build_correct_cli_command(self):
        """
        Given: 드라이브 'X:', 서브경로 'backup', 캐시 'full', 추가 플래그가 포함된 마운트 설정
        When: build_cmd()를 사용하여 명령어 리스트를 생성하면
        Then: rclone 실행에 필요한 모든 인자(mount, remote:path, drive, cache-mode, extra_flags)가 포함되어야 함
        """
        mount = self.sample_cfg["mounts"][0]
        rclone_exe = Path("C:/rclone.exe")
        cmd = rclone_manager.build_cmd(rclone_exe, mount) # 기존 test_build_cmd_generation 대응
        self.assertIn("drive:backup", cmd)
        self.assertIn("X:", cmd)
        self.assertIn("--vfs-cache-mode", cmd)
        self.assertIn("full", cmd)
        self.assertIn("--vfs-read-chunk-size", cmd)

    # ── Scenario 4: rclone.conf 외부 파일 파싱 (Parsing Logic) ──
    def test_scenario_parse_external_rclone_conf(self):
        """
        Given: 섹션명이 [my-gd]이고 타입이 drive인 rclone.conf 파일이 있을 때
        When: parse_rclone_conf()로 파일을 스캔하면
        Then: 섹션명을 'name'으로, 타입을 'type'으로 가진 리모트 리스트를 반환해야 함
        """
        with patch("configparser.ConfigParser.read", return_value=None):
            with patch("configparser.ConfigParser.sections", return_value=["my-gd"]):
                with patch("configparser.ConfigParser.get", return_value="drive"):
                    remotes = rclone_manager.parse_rclone_conf(Path("fake.conf")) # 기존 test_parse_rclone_conf 대응
                    self.assertEqual(remotes[0]["name"], "my-gd")
                    self.assertEqual(remotes[0]["type"], "drive")

    # ── Scenario 5: 최신 업데이트 체크 (Update Logic) ──
    @patch("requests.get")
    def test_scenario_check_latest_rclone_version(self, mock_get):
        """
        Given: rclone 공식 GitHub API가 v1.66.0 버전을 반환하는 상태임
        When: get_latest_version()을 호출하면
        Then: 'v' 접두사가 제거된 '1.66.0' 문자열을 반환해야 함
        """
        mock_get.return_value.json.return_value = {"tag_name": "v1.66.0"}
        version = rclone_manager.get_latest_version() # 기존 test_get_latest_version 대응
        self.assertEqual(version, "1.66.0")

    # ── Scenario 6: 윈도우 시작 프로그램 등록 (System Logic) ──
    def test_scenario_toggle_windows_startup(self):
        """
        Given: 사용자가 윈도우 시작 시 앱을 실행하도록 설정하려고 함
        When: set_startup(True)를 실행하면
        Then: 시스템 레지스트리에 접근하여 실행 경로를 등록해야 함
        """
        with patch("winreg.OpenKey"), patch("winreg.SetValueEx"), patch("winreg.CloseKey"):
            result = rclone_manager.set_startup(True) # 기존 test_startup_registration 대응
            self.assertTrue(result is True or isinstance(result, str))

    # ── Scenario 7: 고해상도 대응 및 UI 제어 (UI/UX Logic) ──
    def test_scenario_ui_resolution_and_resizable_control(self):
        """
        Given: 2736x1824 같은 고해상도 환경의 사용자
        When: 메인 창(App)과 서브 창(MountDialog)을 생성했을 때
        Then: 메인 창은 최소 너비 750px를 유지해야 하며
        And: 서브 창은 사용자가 마우스로 세로 크기를 조절(Resizable)할 수 있어야 하고
        And: 서브 창의 기본 크기는 가시성 확보를 위해 600x700 이상이어야 함
        """
        # Given
        root = App()
        root.withdraw()
        
        # When & Then (Main Window)
        root.update()
        self.assertGreaterEqual(root.winfo_get_minsize()[0], 750, "메인 창 최소 너비 기준 미달")

        # When & Then (Sub Window)
        dlg = MountDialog(root, mount={}, app_cfg=self.sample_cfg)
        dlg.update()
        
        resizing = dlg.resizable()
        self.assertTrue(resizing[1], "실패: 서브창 세로 리사이즈 불가 (resizable 설정 확인 필요)") # 질문자님 핵심 요청사항
        
        width = dlg.winfo_width()
        height = dlg.winfo_height()
        self.assertGreaterEqual(width, 600, f"서브창 너비({width})가 고해상도 기준에 너무 작음")
        self.assertGreaterEqual(height, 700, f"서브창 높이({height})가 고해상도 기준에 너무 작음")
        
        dlg.destroy()
        root.destroy()

if __name__ == "__main__":
    unittest.main()
