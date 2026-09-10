-- 训练与推理跨进程共享的 GPU 独占租约。
CREATE TABLE IF NOT EXISTS `gpu_leases` (
  `gpu_index` int NOT NULL,
  `workload_type` varchar(16) NOT NULL,
  `workload_id` bigint NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`gpu_index`),
  UNIQUE KEY `uq_gpu_leases_workload` (`workload_type`, `workload_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
