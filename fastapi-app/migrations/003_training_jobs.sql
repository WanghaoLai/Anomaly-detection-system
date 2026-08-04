-- 阶段 1：持久化训练任务、事件、指标和产物索引。
CREATE TABLE IF NOT EXISTS `training_jobs` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `job_no` varchar(36) NOT NULL,
  `owner_id` int NOT NULL,
  `owner_role` varchar(20) NOT NULL,
  `algorithm_id` int NOT NULL,
  `dataset_id` int NOT NULL,
  `status` varchar(24) NOT NULL DEFAULT 'QUEUED',
  `config_json` json NOT NULL,
  `runtime_snapshot_json` json DEFAULT NULL,
  `assigned_gpu` int DEFAULT NULL,
  `remote_control_dir` varchar(500) DEFAULT NULL,
  `remote_run_dir` varchar(500) DEFAULT NULL,
  `launcher_pid` int DEFAULT NULL,
  `worker_pid` int DEFAULT NULL,
  `process_pid` int DEFAULT NULL,
  `process_pgid` int DEFAULT NULL,
  `exit_code` int DEFAULT NULL,
  `failure_reason` varchar(1000) DEFAULT NULL,
  `submitted_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `started_at` datetime(6) DEFAULT NULL,
  `finished_at` datetime(6) DEFAULT NULL,
  `last_reconciled_at` datetime(6) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_training_jobs_job_no` (`job_no`),
  KEY `idx_training_jobs_status_gpu` (`status`, `assigned_gpu`),
  KEY `idx_training_jobs_owner` (`owner_role`, `owner_id`),
  KEY `idx_training_jobs_algorithm` (`algorithm_id`),
  KEY `idx_training_jobs_dataset` (`dataset_id`),
  CONSTRAINT `fk_training_jobs_algorithm` FOREIGN KEY (`algorithm_id`) REFERENCES `algorithms` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_training_jobs_dataset` FOREIGN KEY (`dataset_id`) REFERENCES `datasets` (`id`) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `training_events` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `job_id` bigint NOT NULL,
  `sequence` int NOT NULL,
  `event_type` varchar(40) NOT NULL,
  `message` varchar(1000) DEFAULT NULL,
  `payload_json` json DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_training_events_job_sequence` (`job_id`, `sequence`),
  KEY `idx_training_events_type` (`event_type`),
  KEY `idx_training_events_created` (`created_at`),
  CONSTRAINT `fk_training_events_job` FOREIGN KEY (`job_id`) REFERENCES `training_jobs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `training_metrics` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `job_id` bigint NOT NULL,
  `metric_name` varchar(64) NOT NULL,
  `metric_value` double NOT NULL,
  `epoch` int DEFAULT NULL,
  `step` int DEFAULT NULL,
  `recorded_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  KEY `idx_training_metrics_job_name` (`job_id`, `metric_name`),
  CONSTRAINT `fk_training_metrics_job` FOREIGN KEY (`job_id`) REFERENCES `training_jobs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `training_artifacts` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `job_id` bigint NOT NULL,
  `artifact_type` varchar(40) NOT NULL,
  `name` varchar(255) NOT NULL,
  `remote_path` varchar(1000) NOT NULL,
  `size_bytes` bigint NOT NULL DEFAULT '0',
  `metadata_json` json DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_training_artifacts_job_path` (`job_id`, `remote_path`(700)),
  KEY `idx_training_artifacts_type` (`artifact_type`),
  CONSTRAINT `fk_training_artifacts_job` FOREIGN KEY (`job_id`) REFERENCES `training_jobs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
