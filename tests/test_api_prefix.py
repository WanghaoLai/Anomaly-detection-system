import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from main import app  # noqa: E402
from settings import API_PREFIX  # noqa: E402


class ApiPrefixTests(unittest.TestCase):
    def test_all_application_routes_use_stable_api_prefix(self):
        application_paths = {
            route.path
            for route in app.routes
        }
        self.assertTrue(application_paths)
        self.assertTrue(
            all(path.startswith(f"{API_PREFIX}/") for path in application_paths),
            application_paths,
        )

    def test_representative_routes_are_prefixed_and_legacy_paths_are_absent(self):
        paths = {route.path for route in app.routes}
        for path in (
            "/login",
            "/health",
            "/files/upload",
            "/chat/send",
            "/docs",
            "/docs/oauth2-redirect",
            "/redoc",
            "/openapi.json",
        ):
            self.assertIn(f"{API_PREFIX}{path}", paths)
            self.assertNotIn(path, paths)


if __name__ == "__main__":
    unittest.main()
