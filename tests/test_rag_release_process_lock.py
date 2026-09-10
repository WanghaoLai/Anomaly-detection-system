import multiprocessing
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from services.knowledge_service import KnowledgeService


def _bare_service(root: str) -> KnowledgeService:
    service = object.__new__(KnowledgeService)
    service._artifact_repository = SimpleNamespace(root=Path(root))
    return service


def _hold_release_lock(root: str, ready, release) -> None:
    with _bare_service(root)._release_lock():
        ready.set()
        release.wait(5)


class RagReleaseProcessLockTests(unittest.TestCase):
    def test_second_process_cannot_enter_release_critical_section(self):
        try:
            context = multiprocessing.get_context("fork")
        except ValueError:
            self.skipTest("当前平台不支持 fork")

        import tempfile

        with tempfile.TemporaryDirectory() as root:
            ready = context.Event()
            release = context.Event()
            process = context.Process(
                target=_hold_release_lock,
                args=(root, ready, release),
            )
            process.start()
            self.assertTrue(ready.wait(3), "子进程未能取得发布锁")

            acquired = threading.Event()

            def acquire_in_parent():
                with _bare_service(root)._release_lock():
                    acquired.set()

            thread = threading.Thread(target=acquire_in_parent, daemon=True)
            thread.start()
            time.sleep(0.15)
            self.assertFalse(acquired.is_set())
            release.set()
            thread.join(3)
            process.join(3)

            self.assertTrue(acquired.is_set())
            self.assertEqual(process.exitcode, 0)

