import unittest
from pathlib import Path

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).parents[1]


class ProductionDeploymentConfigTests(unittest.TestCase):
    def test_frontend_uses_runtime_or_same_origin_api_configuration(self):
        frontend = PROJECT_ROOT / "vue"
        production = dotenv_values(frontend / ".env.production")
        development = dotenv_values(frontend / ".env.development")
        index = (frontend / "index.html").read_text(encoding="utf-8")
        auth = (frontend / "src" / "utils" / "auth.js").read_text(
            encoding="utf-8"
        )
        runtime = (frontend / "public" / "runtime-config.js").read_text(
            encoding="utf-8"
        )
        vite = (frontend / "vite.config.js").read_text(encoding="utf-8")

        self.assertEqual(production["VITE_BASE_URL"], "/api")
        self.assertEqual(development["VITE_BASE_URL"], "/api")
        self.assertIn("window.__APP_CONFIG__?.apiBaseUrl", auth)
        self.assertLess(index.index("/runtime-config.js"), index.index("/src/main.js"))
        self.assertIn("apiBaseUrl: ''", runtime)
        self.assertNotIn("localhost", runtime)
        self.assertIn("'/api'", vite)
        self.assertIn("VITE_PROXY_TARGET", vite)

    def test_production_environment_template_contains_no_secrets(self):
        path = PROJECT_ROOT / ".env.production.example"
        values = dotenv_values(path)
        for name in (
            "MYSQL_PASSWORD",
            "JWT_SECRET_KEY",
            "DASHSCOPE_API_KEY",
            "AI_QDRANT_API_KEY",
            "GPU_SERVER_SSH_PASSWORD",
        ):
            self.assertIn(name, values)
            self.assertFalse(values[name], name)
        self.assertEqual(values["JWT_COOKIE_SECURE"], "true")
        self.assertEqual(values["DB_SCHEMA_CHECK_ENABLED"], "true")
        self.assertEqual(values["SELF_REGISTRATION_ENABLED"], "false")
        self.assertEqual(values["AI_VECTOR_STORE_PROVIDER"], "qdrant")
        self.assertEqual(values["AI_QDRANT_MODE"], "server")
        self.assertEqual(
            values["FILE_UPLOAD_DIR"],
            "/var/lib/anomaly-detection-system/uploads",
        )
        self.assertFalse(values["TRAINING_EXECUTOR_ENABLED"] == "true")
        self.assertNotIn("/Users/", path.read_text(encoding="utf-8"))

    def test_nginx_and_service_share_the_same_private_backend(self):
        nginx = (PROJECT_ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
        service = (
            PROJECT_ROOT / "deploy" / "anomaly-detection-system.service"
        ).read_text(encoding="utf-8")
        self.assertIn("location /api/", nginx)
        self.assertIn("proxy_pass http://127.0.0.1:9090;", nginx)
        self.assertIn("proxy_buffering off;", nginx)
        self.assertIn("try_files $uri $uri/ /index.html;", nginx)
        self.assertIn("return 301 https://$host$request_uri;", nginx)
        self.assertIn("listen 443 ssl;", nginx)
        self.assertIn("ssl_certificate ", nginx)
        self.assertIn("--host 127.0.0.1 --port 9090", service)
        self.assertIn("--workers 1", service)
        self.assertIn("--forwarded-allow-ips=127.0.0.1", service)
        self.assertIn("StateDirectory=anomaly-detection-system", service)


if __name__ == "__main__":
    unittest.main()
