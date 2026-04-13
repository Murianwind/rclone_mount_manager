import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import tkinter as tk

# 수정한 rclone_manager 모듈 임포트
import rclone_manager

class TestRcloneManagerBDD(unittest.TestCase):

    def setUp(self):
        """테스트 데이터 설정 (Given)"""
        self.sample_cfg = {"remotes": [], "mounts": [], "rclone_path": "", "auto_mount": False}

    def _create_mocked_app(self, cfg=None):
        """Mock 앱 생성"""
        app = rclone_manager.App.__new__(rclone_manager.App)
        app._cfg = cfg if cfg else self.sample_cfg
        app._status = {}
        app._tray = None 
        app._tree = MagicMock()
        app._rc_ver_label = MagicMock()
        app._app_up_btn = MagicMock()
        app.after = MagicMock()
        return app

    def _create_mocked_dialog(self, parent, mount=None, cfg=None):
        """Mock 다이얼로그 생성"""
        dlg = rclone_manager.MountDialog.__new__(rclone_manager.MountDialog)
        dlg._m = mount if mount else {}
        dlg._app_cfg = cfg if cfg else self.sample_cfg
        dlg._rem = MagicMock(); dlg._drv = MagicMock(); dlg._pth = MagicMock()
        dlg._cdir = MagicMock(); dlg._cmode = MagicMock(); dlg._ext = MagicMock()
        dlg._auto = MagicMock(); dlg.destroy = MagicMock()
        return dlg

    # 1. rclone 실행 파일 로드
    def test_scenario_01_load_rclone(self):
        cfg = {"rclone_path": "C:\\fake\\rclone.exe"}
        with patch("pathlib.Path.exists", return_value=True):
            exe = rclone_manager.get_rclone_exe(cfg)
        self.assertEqual(str(exe), "C:\\fake\\rclone.exe")

    # 4. 연결 테스트 로직 존재 확인
    def test_scenario_04_test_method_exists(self):
        app = self._create_mocked_app()
        dlg = self._create_mocked_dialog(app)
        self.assertTrue(hasattr(dlg, '_test'))

    # 6. 드라이브 중복 체크 로직
    def test_scenario_06_drive_conflict_detection(self):
        cfg = {"mounts": [{"id": "1", "drive": "X:", "remote": "old"}]}
        app = self._create_mocked_app(cfg)
        dlg = self._create_mocked_dialog(app, cfg=cfg)
        dlg._rem.get.return_value = "new"; dlg._drv.get.return_value = "X:"
        with patch("tkinter.messagebox.showerror") as m:
            dlg._save()
            self.assertTrue(m.called)

    # 21. 이슈 등록 URL 확인
    def test_scenario_21_repo_check(self):
        self.assertIn("Murianwind", rclone_manager.GITHUB_REPO)

    # ... (기존 21개 시나리오 생략, BDD 형식 유지)

if __name__ == "__main__":
    unittest.main()
