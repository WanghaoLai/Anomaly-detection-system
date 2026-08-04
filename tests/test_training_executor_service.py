import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from services.training_executor_service import (  # noqa: E402
    TrainingExecutorError,
    TrainingExecutorService,
    _absolute_path,
    _isolated_output_root,
)
from services.training_log_parser import parse_training_line  # noqa: E402
from services.training_reliability import (  # noqa: E402
    artifact_descriptor,
    classify_failure,
    hard_delete_blockers,
    safe_artifact_path,
)


class TrainingExecutorServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = TrainingExecutorService()

    def test_rejects_unknown_user_parameter(self):
        with self.assertRaises(TrainingExecutorError):
            self.service._validated_parameters({"command": "anything"})

    def test_rejects_path_traversal_as_mvtec_class(self):
        with self.assertRaises(TrainingExecutorError):
            self.service._validated_parameters({"classes": ["../escape"]})

    def test_rejects_more_than_ten_epochs(self):
        with self.assertRaises(TrainingExecutorError):
            self.service._validated_parameters({"epochs": 11})

    def test_normalizes_fixed_absolute_path(self):
        self.assertEqual(_absolute_path("/srv/PBAS/./main", "path"), "/srv/PBAS/main")
        with self.assertRaises(TrainingExecutorError):
            _absolute_path("relative/path", "path")

    def test_output_root_is_isolated_by_algorithm_dataset_and_job(self):
        pair_root = _isolated_output_root(
            "/runs",
            algorithm_id=1,
            algorithm_name="PBAS",
            dataset_id=3,
            dataset_name="VisA",
        )

        self.assertEqual(
            pair_root,
            "/runs/algorithm-1-PBAS/dataset-3-VisA",
        )
        self.assertEqual(
            f"{pair_root}/job-1",
            "/runs/algorithm-1-PBAS/dataset-3-VisA/job-1",
        )

    def test_output_root_sanitizes_untrusted_names(self):
        pair_root = _isolated_output_root(
            "/runs",
            algorithm_id=2,
            algorithm_name="../DEMO/ALG",
            dataset_id=9,
            dataset_name="../../data set",
        )

        self.assertEqual(
            pair_root,
            "/runs/algorithm-2-DEMO_ALG/dataset-9-data_set",
        )

    def test_remote_config_uses_only_whitelisted_runtime(self):
        parameters = self.service._validated_parameters({"classes": ["screw"]})
        config = self.service._build_remote_config(
            {
                "conda_env_path": "/envs/pbas",
                "source_directory": "/srv/PBAS",
                "entrypoint": "main.py",
            },
            {"root_directory": "/data/mvtec"},
            parameters,
            2,
        )
        self.assertEqual(config["runtime"]["python_executable"], "/envs/pbas/bin/python")
        self.assertEqual(config["runtime"]["entrypoint"], "main.py")
        self.assertEqual(config["resources"]["gpu_index"], 2)
        self.assertNotIn("command", config)

class TrainingExecutorReconcileTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_job_is_not_reconciled_as_remote_process(self):
        service = TrainingExecutorService()
        queued_job = SimpleNamespace(status="QUEUED")

        result = await service.reconcile_job(queued_job)

        self.assertIs(result, queued_job)


class TrainingLogParserTests(unittest.TestCase):
    def test_parses_completed_epoch_metrics_and_progress(self):
        parsed = parse_training_line(
            "epoch:3 sl:1.01e+00 bl:4.01e-01 sample:320 "
            "IAUC:89.08(89.08) PAUC:98.28(98.28) E:3(3): "
            "80%|████████  | 4/5 [02:45<00:39, 39.92s/epoch]"
        )

        self.assertTrue(parsed.persist)
        self.assertEqual(parsed.current_epoch, 4)
        self.assertEqual(parsed.total_epochs, 5)
        self.assertEqual(parsed.progress_percent, 80)
        self.assertIn(("eval/image_auroc", 0.8908, 4), parsed.metrics)
        self.assertIn(("train/binary_loss", 0.401, 4), parsed.metrics)

    def test_batch_updates_are_not_persisted(self):
        parsed = parse_training_line(
            "epoch:1 sl:1.30e+00 bl:6.60e-01 sample:88 "
            "IAUC:55.97(55.97) PAUC:9.4(9.4) E:0(0): "
            "20%|██ | 1/5 [00:50<03:07, 46.98s/epoch]"
        )

        self.assertFalse(parsed.persist)
        self.assertEqual(parsed.current_epoch, 1)

    def test_parses_final_metrics_as_ratios(self):
        parsed = parse_training_line(
            "image_auroc:89.08 image_ap:96.01 pixel_auroc:98.28 "
            "pixel_ap:21.78 pixel_pro:92.5 best_epoch:3"
        )

        self.assertEqual(parsed.progress_percent, 100)
        self.assertIn(("pixel_pro", 0.925, 3), parsed.metrics)

    def test_persists_runtime_error_for_failure_classification(self):
        parsed = parse_training_line(
            "RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB"
        )
        self.assertTrue(parsed.persist)
        self.assertEqual(parsed.stream, "ERROR")


class TrainingReliabilityTests(unittest.TestCase):
    def test_classifies_cuda_oom(self):
        code, reason = classify_failure(
            1,
            "RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB",
        )
        self.assertEqual(code, "CUDA_OOM")
        self.assertIn("显存不足", reason)

    def test_classifies_disk_full_and_abnormal_exit(self):
        self.assertEqual(
            classify_failure(1, "OSError: No space left on device")[0],
            "DISK_FULL",
        )
        self.assertEqual(classify_failure(7, "")[0], "ABNORMAL_EXIT")

    def test_timeout_has_priority_over_log_pattern(self):
        code, _ = classify_failure(143, "CUDA out of memory", "TIMEOUT")
        self.assertEqual(code, "TIMEOUT")

    def test_assigns_checkpoint_and_log_roles(self):
        self.assertEqual(
            artifact_descriptor("artifacts/ckpt_best_4.pth")[1],
            "BEST_CHECKPOINT",
        )
        self.assertEqual(
            artifact_descriptor("raw.log"),
            ("LOG", "TRAIN_LOG", True),
        )

    def test_artifact_path_must_stay_inside_run_directory(self):
        root = "/home/adtrainer/training-runs/job-1"
        self.assertTrue(safe_artifact_path(root, f"{root}/artifacts/results.csv"))
        self.assertFalse(safe_artifact_path(root, f"{root}/../job-2/raw.log"))
        self.assertFalse(safe_artifact_path(root, "/etc/passwd"))

    def test_hard_delete_requires_every_safety_condition(self):
        blockers = hard_delete_blockers(
            status="RUNNING",
            archived=False,
            cleanup_status="RETAINED",
            has_retry_children=True,
            remote_paths_exist=True,
            remote_process_exists=True,
        )
        self.assertEqual(
            blockers,
            [
                "任务尚未结束",
                "任务尚未归档",
                "远程产物尚未清理",
                "仍有重试任务引用当前任务",
                "远程任务目录仍然存在",
                "远程训练进程仍然存在",
            ],
        )

    def test_hard_delete_allows_only_fully_released_archived_job(self):
        self.assertEqual(
            hard_delete_blockers(
                status="SUCCEEDED",
                archived=True,
                cleanup_status="CLEANED",
                has_retry_children=False,
                remote_paths_exist=False,
                remote_process_exists=False,
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
