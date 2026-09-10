-- 知识库发布跨越 MySQL 与文件/向量索引，使用持久化操作记录实现崩溃恢复。
CREATE TABLE IF NOT EXISTS `knowledge_release_operations` (
  `release_id` varchar(64) NOT NULL,
  `operation` varchar(16) NOT NULL,
  `status` varchar(20) NOT NULL DEFAULT 'PENDING',
  `payload_json` json NOT NULL,
  `error_message` varchar(1000) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`release_id`),
  KEY `idx_knowledge_release_status` (`status`),
  CONSTRAINT `chk_knowledge_release_operation`
    CHECK (`operation` IN ('UPLOAD', 'DELETE')),
  CONSTRAINT `chk_knowledge_release_status`
    CHECK (`status` IN ('PENDING', 'PUBLISHED', 'ROLLED_BACK'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
