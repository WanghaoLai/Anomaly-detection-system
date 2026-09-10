-- 一个算法/数据集主记录只能对应一条详情。
-- 若历史数据已存在重复详情，本迁移会在 ADD UNIQUE 时失败并保持原数据不变；
-- 必须先人工核对重复记录，禁止自动删除或任意保留“第一条”。
ALTER TABLE `algorithm`
  ADD UNIQUE KEY `uq_algorithm_algorithm_id` (`algorithm_id`);

ALTER TABLE `dataset`
  ADD UNIQUE KEY `uq_dataset_dataset_id` (`dataset_id`);
