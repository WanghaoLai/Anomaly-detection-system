-- 阶段 3：训练进度、远程日志同步游标和持久化日志。
ALTER TABLE `training_jobs`
  ADD COLUMN `progress_percent` double NOT NULL DEFAULT '0' AFTER `attempt`,
  ADD COLUMN `current_epoch` int DEFAULT NULL AFTER `progress_percent`,
  ADD COLUMN `total_epochs` int DEFAULT NULL AFTER `current_epoch`,
  ADD COLUMN `log_offset` bigint NOT NULL DEFAULT '0' AFTER `total_epochs`;

UPDATE `training_jobs`
SET
  `total_epochs` = CAST(
    JSON_UNQUOTE(JSON_EXTRACT(`config_json`, '$.parameters.epochs'))
    AS UNSIGNED
  ),
  `progress_percent` = CASE
    WHEN `status` = 'SUCCEEDED' THEN 100
    ELSE 0
  END
WHERE JSON_EXTRACT(`config_json`, '$.parameters.epochs') IS NOT NULL;

CREATE TABLE IF NOT EXISTS `training_logs` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `job_id` bigint NOT NULL,
  `sequence` int NOT NULL,
  `stream` varchar(20) NOT NULL DEFAULT 'STDOUT',
  `content` text NOT NULL,
  `remote_offset` bigint NOT NULL DEFAULT '0',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_training_logs_job_sequence` (`job_id`, `sequence`),
  KEY `idx_training_logs_job_id` (`job_id`, `id`),
  KEY `idx_training_logs_created` (`created_at`),
  CONSTRAINT `fk_training_logs_job`
    FOREIGN KEY (`job_id`) REFERENCES `training_jobs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
