-- 任务和 GPU 租约必须显式记录服务器；否则不同服务器的 GPU 0 会互相冲突。
ALTER TABLE `training_jobs`
  ADD COLUMN `server_id` VARCHAR(32) NOT NULL DEFAULT 'primary' AFTER `owner_role`,
  ADD KEY `idx_training_jobs_server_id` (`server_id`);

UPDATE `training_jobs` j
JOIN `algorithm` a ON a.`algorithm_id` = j.`algorithm_id`
SET j.`server_id` = a.`server_id`;

UPDATE `training_jobs`
SET `config_json` = JSON_SET(`config_json`, '$.server_id', `server_id`);

UPDATE `training_jobs`
SET `runtime_snapshot_json` = JSON_SET(
  `runtime_snapshot_json`, '$.server_id', `server_id`
)
WHERE `runtime_snapshot_json` IS NOT NULL;

ALTER TABLE `inference_jobs`
  ADD COLUMN `server_id` VARCHAR(32) NOT NULL DEFAULT 'primary' AFTER `owner_role`,
  ADD KEY `idx_inference_jobs_server_id` (`server_id`);

UPDATE `inference_jobs` i
JOIN `training_jobs` t ON t.`id` = i.`training_job_id`
SET i.`server_id` = t.`server_id`;

UPDATE `inference_jobs`
SET `config_json` = JSON_SET(`config_json`, '$.server_id', `server_id`);

ALTER TABLE `training_job_deletions`
  ADD COLUMN `server_id` VARCHAR(32) NOT NULL DEFAULT 'primary' AFTER `owner_role`,
  ADD KEY `idx_training_job_deletions_server_id` (`server_id`);

ALTER TABLE `gpu_leases`
  DROP PRIMARY KEY,
  ADD COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT FIRST,
  ADD COLUMN `server_id` VARCHAR(32) NOT NULL DEFAULT 'primary' AFTER `id`,
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `uq_gpu_leases_server_gpu` (`server_id`, `gpu_index`),
  ADD KEY `idx_gpu_leases_server_id` (`server_id`);
