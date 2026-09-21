import json
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from services.gpu_server_service import (  # noqa: E402
    GpuServerError,
    GpuServerRegistry,
    build_gpu_server_registry,
)
from settings import GPU_SERVER_CONFIG  # noqa: E402


class GpuServerRegistryTests(unittest.TestCase):
    def test_legacy_primary_server_remains_default(self):
        registry = GpuServerRegistry.from_environment(GPU_SERVER_CONFIG, "[]")

        self.assertIs(registry.get(), registry.get("primary"))
        self.assertEqual([item["id"] for item in registry.public_options()], ["primary"])

    def test_additional_server_is_selectable_and_inherits_safe_defaults(self):
        extra = json.dumps([{
            "id": "gpu-lab-2",
            "name": "GPU 服务器 2",
            "host": "192.0.2.11",
            "port": 2222,
            "ssh_user": "observer-2",
            "private_key_path": "/keys/observer-2",
            "known_hosts_path": "/keys/known-hosts-2",
            "expected_gpu_count": 2,
            "account_map": {"alice": "alice-gpu-2"},
        }])

        registry = GpuServerRegistry.from_environment(GPU_SERVER_CONFIG, extra)
        service = registry.get("gpu-lab-2")

        self.assertEqual(service.config["host"], "192.0.2.11")
        self.assertEqual(service.config["port"], 2222)
        self.assertEqual(service.resolve_linux_account("alice"), "alice-gpu-2")
        self.assertEqual(len(registry.public_options()), 2)
        self.assertTrue(registry.public_options()[1]["supportsFiles"])
        self.assertFalse(registry.public_options()[1]["supportsConda"])

    def test_gpu_only_server_advertises_no_file_or_conda_capabilities(self):
        extra = json.dumps([{
            "id": "gpu-only",
            "name": "GPU Only",
            "host": "192.0.2.13",
            "ssh_user": "observer",
            "private_key_path": "/keys/observer",
            "known_hosts_path": "/keys/known-hosts",
            "account_map": {},
            "allowed_directories": {},
            "conda_env_roots": {},
        }])

        registry = GpuServerRegistry.from_environment(GPU_SERVER_CONFIG, extra)
        option = registry.public_options()[1]

        self.assertFalse(option["supportsFiles"])
        self.assertFalse(option["supportsConda"])

    def test_public_options_never_expose_credentials_or_key_paths(self):
        extra = json.dumps([{
            "id": "gpu-lab-2",
            "name": "GPU 服务器 2",
            "host": "192.0.2.11",
            "ssh_password": "secret",
            "private_key_path": "/secret/key",
            "ssh_user": "observer-2",
            "known_hosts_path": "/keys/known-hosts-2",
        }])
        registry = GpuServerRegistry.from_environment(GPU_SERVER_CONFIG, extra)

        serialized = json.dumps(registry.public_options())

        self.assertNotIn("secret", serialized)
        self.assertNotIn("private_key", serialized)

    def test_unknown_server_is_rejected(self):
        registry = GpuServerRegistry.from_environment(GPU_SERVER_CONFIG, "[]")

        with self.assertRaises(GpuServerError):
            registry.get("attacker-controlled-host")

    def test_duplicate_or_unknown_configuration_fails_closed(self):
        duplicate = json.dumps([{"id": "primary", "name": "duplicate"}])
        unknown = json.dumps([{"id": "gpu-2", "name": "GPU 2", "url": "ssh://bad"}])

        with self.assertRaises(GpuServerError):
            GpuServerRegistry.from_environment(GPU_SERVER_CONFIG, duplicate)
        with self.assertRaises(GpuServerError):
            GpuServerRegistry.from_environment(GPU_SERVER_CONFIG, unknown)

    def test_additional_server_must_supply_its_own_credentials_and_host_key(self):
        missing_credentials = json.dumps([{
            "id": "gpu-2",
            "name": "GPU 2",
            "host": "192.0.2.12",
            "ssh_user": "observer-2",
        }])

        with self.assertRaises(GpuServerError):
            GpuServerRegistry.from_environment(
                GPU_SERVER_CONFIG,
                missing_credentials,
            )

    def test_invalid_additional_configuration_reports_visible_fallback(self):
        registry, status = build_gpu_server_registry(
            GPU_SERVER_CONFIG,
            '[{"id":"gpu-2","name":"GPU 2","url":"ssh://bad"}]',
        )

        self.assertFalse(status["healthy"])
        self.assertTrue(status["fallbackToPrimary"])
        self.assertEqual(status["activeServerCount"], 1)
        self.assertIn("未支持字段", status["error"])
        self.assertEqual([item["id"] for item in registry.public_options()], ["primary"])


if __name__ == "__main__":
    unittest.main()
