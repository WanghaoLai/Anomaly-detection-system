-- 阶段 4：失败分类、超时、产物角色/下载状态和任务审计。
ALTER TABLE `training_jobs`
  ADD COLUMN `failure_code` varchar(40) DEFAULT NULL AFTER `exit_code`,
  ADD COLUMN `timeout_seconds` int DEFAULT NULL AFTER `log_offset`,
  ADD COLUMN `cleanup_status` varchar(20) NOT NULL DEFAULT 'RETAINED' AFTER `timeout_seconds`,
  ADD COLUMN `cleaned_at` datetime(6) DEFAULT NULL AFTER `cleanup_status`,
  ADD COLUMN `reconcile_failures` int NOT NULL DEFAULT '0' AFTER `cleaned_at`,
  ADD KEY `idx_training_jobs_failure_code` (`failure_code`),
  ADD KEY `idx_training_jobs_cleanup_status` (`cleanup_status`);

UPDATE `training_jobs`
SET `timeout_seconds` = 21600
WHERE `timeout_seconds` IS NULL;

UPDATE `training_jobs`
SET `failure_code` = CASE
  WHEN `status` = 'LOST' THEN 'EXECUTOR_LOST'
  WHEN `status` = 'STOPPED' AND `failure_reason` LIKE '%排队%' THEN 'CANCELED'
  WHEN `status` = 'STOPPED' THEN 'USER_STOPPED'
  ELSE `failure_code`
END
WHERE `failure_code` IS NULL;

ALTER TABLE `training_artifacts`
  ADD COLUMN `artifact_role` varchar(40) NOT NULL DEFAULT 'OTHER' AFTER `artifact_type`,
  ADD COLUMN `downloadable` tinyint(1) NOT NULL DEFAULT '1' AFTER `size_bytes`,
  ADD KEY `idx_training_artifacts_role` (`artifact_role`),
  ADD KEY `idx_training_artifacts_downloadable` (`downloadable`);

UPDATE `training_artifacts`
SET `artifact_role` = CASE
  WHEN LOWER(`name`) = 'raw.log' THEN 'TRAIN_LOG'
  WHEN LOWER(`name`) = 'config.json' THEN 'TRAIN_CONFIG'
  WHEN LOWER(`name`) = 'command.json' THEN 'EXECUTION_COMMAND'
  WHEN LOWER(`name`) = 'manifest.json' THEN 'RUN_MANIFEST'
  WHEN LOWER(`name`) = 'results.csv' THEN 'EVALUATION_RESULT'
  WHEN LOWER(`name`) REGEXP 'best.*\\.(pth|pt|ckpt)$'
    OR LOWER(`name`) REGEXP 'ckpt_best.*\\.(pth|pt|ckpt)$'
    THEN 'BEST_CHECKPOINT'
  WHEN LOWER(`name`) REGEXP '(last|latest).*\\.(pth|pt|ckpt)$'
    THEN 'LAST_CHECKPOINT'
  WHEN LOWER(`name`) REGEXP '\\.(pth|pt|ckpt)$' THEN 'AUXILIARY_CHECKPOINT'
  ELSE 'OTHER'
END;

CREATE TABLE IF NOT EXISTS `training_audits` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `job_id` bigint NOT NULL,
  `actor_id` int DEFAULT NULL,
  `actor_role` varchar(20) NOT NULL DEFAULT '系统',
  `action` varchar(40) NOT NULL,
  `result` varchar(20) NOT NULL DEFAULT 'SUCCESS',
  `message` varchar(1000) DEFAULT NULL,
  `payload_json` json DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  KEY `idx_training_audits_job_created` (`job_id`, `created_at`),
  KEY `idx_training_audits_actor_id` (`actor_id`),
  KEY `idx_training_audits_actor_role` (`actor_role`),
  KEY `idx_training_audits_action` (`action`),
  KEY `idx_training_audits_created` (`created_at`),
  CONSTRAINT `fk_training_audits_job`
    FOREIGN KEY (`job_id`) REFERENCES `training_jobs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
