-- 阶段 5：成功训练产物到 PBAS 推理/评估任务的闭环。
CREATE TABLE IF NOT EXISTS `inference_jobs` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `job_no` varchar(36) NOT NULL,
  `owner_id` int NOT NULL,
  `owner_role` varchar(20) NOT NULL,
  `training_job_id` bigint NOT NULL,
  `status` varchar(24) NOT NULL DEFAULT 'QUEUED',
  `config_json` json NOT NULL,
  `result_json` json DEFAULT NULL,
  `assigned_gpu` int DEFAULT NULL,
  `remote_control_dir` varchar(500) DEFAULT NULL,
  `remote_run_dir` varchar(500) DEFAULT NULL,
  `launcher_pid` int DEFAULT NULL,
  `exit_code` int DEFAULT NULL,
  `failure_reason` varchar(1000) DEFAULT NULL,
  `submitted_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `started_at` datetime(6) DEFAULT NULL,
  `finished_at` datetime(6) DEFAULT NULL,
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_inference_jobs_job_no` (`job_no`),
  KEY `idx_inference_jobs_status_gpu` (`status`, `assigned_gpu`),
  KEY `idx_inference_jobs_owner` (`owner_role`, `owner_id`),
  KEY `idx_inference_jobs_training_job` (`training_job_id`),
  CONSTRAINT `fk_inference_jobs_training_job` FOREIGN KEY (`training_job_id`)
    REFERENCES `training_jobs` (`id`) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
