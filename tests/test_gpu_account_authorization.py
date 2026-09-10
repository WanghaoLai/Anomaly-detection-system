import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from services.gpu_server_service import GpuServerError, GpuServerService  # noqa: E402


class GpuAccountAuthorizationTests(unittest.TestCase):
    @staticmethod
    def service(account_map_json: str) -> GpuServerService:
        service = GpuServerService()
        service.config = {**service.config, "account_map_json": account_map_json}
        return service

    def test_missing_mapping_fails_closed(self):
        with self.assertRaises(GpuServerError):
            self.service("{}").resolve_linux_account("alice")

    def test_exact_mapping_is_used(self):
        account = self.service('{"alice":"gpu-alice"}').resolve_linux_account(
            "alice"
        )
        self.assertEqual(account, "gpu-alice")

    def test_explicit_shared_account_is_supported(self):
        account = self.service('{"*":"gpu-reader"}').resolve_linux_account("alice")
        self.assertEqual(account, "gpu-reader")

    def test_editing_app_username_cannot_select_linux_account_implicitly(self):
        service = self.service('{"alice":"gpu-alice"}')
        with self.assertRaises(GpuServerError):
            service.resolve_linux_account("root")


if __name__ == "__main__":
    unittest.main()
