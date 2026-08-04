-- 阶段 4 补充：管理员任务归档与受控物理删除审计存根。
ALTER TABLE `training_jobs`
  ADD COLUMN `archived_at` datetime(6) DEFAULT NULL AFTER `reconcile_failures`,
  ADD COLUMN `archived_by` int DEFAULT NULL AFTER `archived_at`,
  ADD KEY `idx_training_jobs_archived_at` (`archived_at`),
  ADD KEY `idx_training_jobs_archived_by` (`archived_by`);

CREATE TABLE IF NOT EXISTS `training_job_deletions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `original_job_id` bigint NOT NULL,
  `job_no` varchar(36) NOT NULL,
  `owner_id` int NOT NULL,
  `owner_role` varchar(20) NOT NULL,
  `algorithm_id` bigint NOT NULL,
  `dataset_id` bigint NOT NULL,
  `terminal_status` varchar(24) NOT NULL,
  `actor_id` int NOT NULL,
  `actor_role` varchar(20) NOT NULL DEFAULT '管理员',
  `reason` varchar(500) NOT NULL,
  `snapshot_json` json NOT NULL,
  `deleted_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_training_job_deletions_job_no` (`job_no`),
  KEY `idx_training_job_deletions_original_job_id` (`original_job_id`),
  KEY `idx_training_job_deletions_actor_id` (`actor_id`),
  KEY `idx_training_job_deletions_deleted_at` (`deleted_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
