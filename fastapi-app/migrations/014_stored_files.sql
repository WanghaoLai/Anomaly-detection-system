-- 为通用上传文件建立可实施对象级授权的持久化边界。
CREATE TABLE IF NOT EXISTS `stored_files` (
  `id` varchar(36) NOT NULL,
  `category` varchar(32) NOT NULL,
  `relative_path` varchar(500) NOT NULL,
  `original_name` varchar(255) NOT NULL,
  `owner_id` int NOT NULL,
  `owner_role` varchar(20) NOT NULL,
  `access_scope` varchar(20) NOT NULL DEFAULT 'OWNER',
  `size_bytes` bigint NOT NULL,
  `media_type` varchar(128) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_stored_files_relative_path` (`relative_path`),
  KEY `idx_stored_files_owner` (`owner_role`, `owner_id`),
  KEY `idx_stored_files_category` (`category`),
  CONSTRAINT `chk_stored_files_scope`
    CHECK (`access_scope` IN ('OWNER', 'AUTHENTICATED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
