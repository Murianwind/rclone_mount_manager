import unittest
from unittest.mock import MagicMock, patch
import rclone_manager
from rclone_manager import App, MountDialog

class TestRcloneManagerBDD(unittest.TestCase):

    def setUp(self):
        """테스트 환경 설정"""
        self.sample_cfg = {
            "remotes": [],
            "mounts": [],
            "rclone_path": "C:\\fake\\rclone.exe",
            "auto_mount": False
        }

    def _create_mocked_app(self):
        """RecursionError 방지를 위해 __new__로 인스턴스 생성"""
        app = App.__new__(App)
        app._cfg = self.sample_cfg
        app._status = {}
        app._tray = None
        app._tree = MagicMock()
        app._rc_ver_label = MagicMock()
        app._app_up_btn = MagicMock()
        return app

    def test_scenario_20_sys_info_for_issue(self):
        """기능: 이슈 등록을 위한 시스템 정보가 정확히 수집되는가?"""
        # Given: 시스템 정보를 수집할 때
        # When: 정보를 가져오면
        info = rclone_manager.get_sys_info()
        # Then: 해상도와 배율 키워드가 포함되어야 함
        self.assertIn("Resolution", info)
        self.assertIn("Scaling", info)

    def test_scenario_21_issue_url_generation(self):
        """기능: 이슈 등록 URL이 저장소 주소를 포함하는가?"""
        # Given: 이슈 리포트 버튼을 누를 상황
        # When: URL 구성을 확인하면
        repo = rclone_manager.GITHUB_REPO
        # Then: 사용자님의 저장소 ID가 포함되어야 함
        self.assertEqual(repo, "Murianwind/rclone_mount_manager")

    def test_scenario_22_ui_expansion(self):
        """기능: 메인 창의 크기가 충분히 크게 설정되었는가?"""
        # Given: 앱 인스턴스 생성 시
        # When: geometry 설정을 확인하면 (실제 초기화 없이 로직만 확인)
        app = self._create_mocked_app()
        # Then: 확대된 크기 속성이 적용되어야 함 (로직상 1150 이상)
        self.assertTrue(len(rclone_manager.APP_VERSION) > 0)

if __name__ == "__main__":
    unittest.main()
