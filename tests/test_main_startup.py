import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

import main  # noqa: E402


class RagStartupValidationTests(unittest.TestCase):
    def test_vector_store_outage_does_not_abort_application_startup(self):
        with (
            patch.object(
                main.knowledge_service,
                "validate_embedding_config",
                side_effect=RuntimeError("Qdrant temporarily unavailable"),
            ),
            patch.object(main.logger, "exception") as log_exception,
        ):
            main._validate_rag_startup_state()

        log_exception.assert_called_once()

    def test_inconsistent_embedding_contract_is_logged_as_warning(self):
        with (
            patch.object(
                main.knowledge_service,
                "validate_embedding_config",
                return_value={
                    "consistent": False,
                    "issues": ["embedding dimension mismatch"],
                },
            ),
            patch.object(main.logger, "warning") as log_warning,
        ):
            main._validate_rag_startup_state()

        log_warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
