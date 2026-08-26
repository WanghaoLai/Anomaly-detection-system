import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"

# 在子进程中复现"先 Tortoise.init、后导入 api"的顺序（生产环境顺序相反，
# 但测试、脚本或 main.py 导入顺序调整都可能触发）。必须在子进程执行：
# 主测试会话已按生产顺序导入过 api，模块缓存无法重放另一种顺序。
PROBE_SCRIPT = """
import asyncio

from tortoise import Tortoise, connections


async def main():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["models"]},
    )
    try:
        import api  # 触发 pkgutil 自动导入全部路由模块
        print("api-import-ok")
    finally:
        # aiosqlite 工作线程非 daemon，不关闭连接子进程无法退出。
        await connections.close_all(discard=True)


asyncio.run(main())
"""


class ApiImportOrderTests(unittest.TestCase):
    def test_api_imports_after_tortoise_init(self):
        result = subprocess.run(
            [sys.executable, "-c", PROBE_SCRIPT],
            capture_output=True,
            text=True,
            cwd=str(BACKEND_DIR),
            timeout=180,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"Tortoise 初始化后导入 api 失败:\\n{result.stderr[-2000:]}",
        )
        self.assertIn("api-import-ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
