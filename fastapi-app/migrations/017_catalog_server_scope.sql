-- 为算法运行配置和数据集路径增加明确的 GPU 服务器归属。
-- 历史记录统一归属 primary；服务器凭据仍只保存在环境配置中，不进入业务表。
ALTER TABLE `algorithm`
  ADD COLUMN `server_id` VARCHAR(32) NOT NULL DEFAULT 'primary'
    COMMENT '算法所在 GPU 服务器稳定 ID' AFTER `algorithm_id`,
  ADD KEY `idx_algorithm_server_id` (`server_id`);

ALTER TABLE `dataset`
  ADD COLUMN `server_id` VARCHAR(32) NOT NULL DEFAULT 'primary'
    COMMENT '数据集所在 GPU 服务器稳定 ID' AFTER `dataset_id`,
  ADD KEY `idx_dataset_server_id` (`server_id`);
