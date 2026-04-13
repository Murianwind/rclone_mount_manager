import unittest
import os
import sys
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import tkinter as tk

# 테스트 대상 모듈 임포트
import rclone_manager
from rclone_manager import App, MountDialog

class TestRcloneManagerBDD(unittest.TestCase):

    def setUp(self):
        """테스트 기초 데이터 설정 (Given)"""
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

    # ── Scenario 1: 설정 로드 및 자동 복구 (Config) ──
    def test_scenario_config_load_and_repair(self):
        """
        Given: 불완전한 설정 JSON 데이터가 존재함
        When: load_config()를 호출하여 로드할 때
        Then: 누락된 필드가 기본값으로 복구되어야 함
        """
        partial_json = json.dumps({"mounts": [{"remote": "test"}]})
        with patch("pathlib.Path.read_text", return_value=partial_json):
            with patch("pathlib.Path.exists", return_value=True):
                cfg = rclone_manager.load_config()
                self.assertIn("remotes", cfg)
                self.assertIn("id", cfg["mounts"][0])

    # ── Scenario 2: 실행 파일 경로 결정 (Path) ──
    def test_scenario_determine_rclone_path(self):
        """
        Given: 커스텀 경로가 비어있는 설정
        When: get_rclone_exe()를 호출하면
        Then: 앱 기본 경로의 rclone.exe를 반환해야 함
        """
        cfg = {"rclone_path": ""}
        with patch("pathlib.Path.exists", return_value=False):
            exe = rclone_manager.get_rclone_exe(cfg)
            self.assertTrue(str(exe).endswith("rclone.exe"))

    # ── Scenario 3: 명령어 빌드 (CLI) ──
    def test_scenario_build_correct_cli_command(self):
        """
        Given: 유효한 마운트 설정값
        When: build_cmd()로 리스트 생성 시
        Then: 모든 필수 플래그가 포함되어야 함
        """
        mount = self.sample_cfg["mounts"][0]
        rclone_exe = Path("C:/rclone.exe")
        cmd = rclone_manager.build_cmd(rclone_exe, mount)
        self.assertIn("drive:backup", cmd)
        self.assertIn("X:", cmd)
        self.assertIn("full", cmd)

    # ── Scenario 4: 설정 파일 파싱 (Parsing) ──
    def test_scenario_parse_external_rclone_conf(self):
        """
        Given: 외부 rclone.conf 파일 내용
        When: parse_rclone_conf()로 파싱 시
        Then: 리모트 이름과 타입을 정확히 추출해야 함
        """
        with patch("configparser.ConfigParser.read", return_value=None):
            with patch("configparser.ConfigParser.sections", return_value=["my-gd"]):
                with patch("configparser.ConfigParser.get", return_value="drive"):
                    remotes = rclone_manager.parse_rclone_conf(Path("fake.conf"))
                    self.assertEqual(remotes[0]["name"], "my-gd")

    # ── Scenario 5: 버전 체크 (Update) ──
    @patch("requests.get")
    def test_scenario_check_latest_rclone_version(self, mock_get):
        """
        Given: GitHub API 응답 데이터
        When: get_latest_version() 호출 시
        Then: 버전 문자열이 정확히 반환되어야 함
        """
        mock_get.return_value.json.return_value = {"tag_name": "v1.66.0"}
        version = rclone_manager.get_latest_version()
        self.assertEqual(version, "1.66.0")

    # ── Scenario 6: 시작 프로그램 등록 (System) ──
    def test_scenario_toggle_windows_startup(self):
        """
        Given: 시작 프로그램 등록 요청
        When: set_startup(True) 실행 시
        Then: 레지스트리 작업이 성공하거나 에러 메시지를 반환해야 함
        """
        with patch("winreg.OpenKey"), patch("winreg.SetValueEx"), patch("winreg.CloseKey"):
            result = rclone_manager.set_startup(True)
            self.assertTrue(result is True or isinstance(result, str))

    # ── Scenario 7: 고해상도 대응 및 UI 제어 (Resolution) ──
    def test_scenario_ui_resolution_and_resizable_control(self):
        """
        Given: 고해상도 환경의 사용자 (Surface Pro 등)
        When: 앱의 메인창과 서브창(MountDialog)을 생성했을 때
        Then: 메인창은 최소 너비 750px를 확보해야 하며
        And: 서브창은 세로 리사이즈가 가능하고 기본 크기가 충분히 커야 함
        """
        # Given
        root = App()
        root.withdraw()
        
        # When & Then (Main Window)
        root.update()
        # AttributeError 방지: winfo_get_minsize 대신 minsize() 사용
        min_w, min_h = root.minsize()
        self.assertGreaterEqual(min_w, 750, "메인 창 최소 너비 기준 미달")

        # When & Then (Sub Window)
        dlg = MountDialog(root, mount={}, app_cfg=self.sample_cfg)
        dlg.update()
        
        # 리사이즈 속성 검증
        resizing = dlg.resizable()
        self.assertTrue(resizing[1], "서브창 세로 리사이즈가 설정에서 막혀 있습니다.")
        
        # 가시성 크기 검증 (수정된 코드의 geometry/minsize 반영 여부 확인)
        width = dlg.winfo_width()
        height = dlg.winfo_height()
        self.assertGreaterEqual(width, 600, f"서브창 너비({width})가 고해상도에서 너무 작습니다.")
        self.assertGreaterEqual(height, 700, f"서브창 높이({height})가 고해상도에서 너무 작습니다.")
        
        dlg.destroy()
        root.destroy()

if __name__ == "__main__":
    unittest.main()
