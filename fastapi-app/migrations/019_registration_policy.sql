-- 未设置过时沿用 SELF_REGISTRATION_ENABLED；管理员首次保存后以本表为准。
CREATE TABLE IF NOT EXISTS `registration_policy` (
  `id` INT NOT NULL,
  `enabled` TINYINT(1) NOT NULL,
  `updated_by` INT NULL,
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
