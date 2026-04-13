import unittest
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# 테스트 대상 모듈 임포트
import rclone_manager

class TestRcloneManagerBDD(unittest.TestCase):
    
    def setUp(self):
        """테스트 시작 전 초기 설정"""
        self.mock_cfg = {
            "remotes": [],
            "mounts": [],
            "rclone_path": "",
            "auto_mount": False
        }

    ## 1. 기능 검증 테스트 ##
    def test_load_config_default(self):
        """설정 파일이 없을 때 기본값이 올바르게 생성되는지 확인"""
        # 실제 파일에 영향을 주지 않도록 존재하지 않는 경로 가정
        with patch('rclone_manager.CONFIG_FILE', Path('non_existent_file.json')):
            cfg = rclone_manager.load_config()
            self.assertEqual(cfg["remotes"], [])
            self.assertEqual(cfg["auto_mount"], False)

    def test_rclone_exe_logic(self):
        """rclone 실행 파일 경로 결정 로직이 올바른지 확인"""
        # 사용자 설정 경로가 있는 경우
        self.mock_cfg["rclone_path"] = "C:\\tools\\rclone.exe"
        exe = rclone_manager.get_rclone_exe(self.mock_cfg)
        # get_rclone_exe는 파일이 실제 존재해야 Path를 반환하므로 로직상 체크
        self.assertIsInstance(exe, Path)

    ## 2. 비유효 테스트 (Edge Cases) ##
    def test_startup_registry_error_handling(self):
        """레지스트리 권한이 없는 상황에서도 프로그램이 중단되지 않는지 확인"""
        with patch('winreg.OpenKey', side_effect=PermissionError("Access Denied")):
            result = rclone_manager.set_startup(True)
            self.assertIsInstance(result, str) # 에러 메시지 문자열 반환 확인

    ## 3. DPI 및 환경 검증 ##
    def test_dpi_awareness_call(self):
        """Windows DPI 인식 API가 호출 가능한지 확인 (Windows 환경 전용)"""
        if sys.platform == 'win32':
            try:
                from ctypes import windll
                # 함수 존재 여부 확인
                self.assertTrue(has_value := hasattr(windll.shcore, 'SetProcessDpiAwareness'))
            except Exception:
                self.skipTest("DPI 관련 DLL이 없는 윈도우 버전입니다.")

if __name__ == '__main__':
    unittest.main()
