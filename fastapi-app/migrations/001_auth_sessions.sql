ALTER TABLE `admin`
    ADD COLUMN `token_version` INT NOT NULL DEFAULT 0 COMMENT '令牌版本';

ALTER TABLE `user`
    ADD COLUMN `token_version` INT NOT NULL DEFAULT 0 COMMENT '令牌版本';

CREATE TABLE IF NOT EXISTS `auth_session` (
    `id` VARCHAR(36) NOT NULL,
    `user_id` INT NOT NULL,
    `role` VARCHAR(20) NOT NULL,
    `refresh_jti` VARCHAR(64) NOT NULL,
    `expires_at` DATETIME(6) NOT NULL,
    `revoked_at` DATETIME(6) NULL,
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_auth_session_refresh_jti` (`refresh_jti`),
    KEY `idx_auth_session_principal` (`role`, `user_id`),
    KEY `idx_auth_session_expires_at` (`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `login_throttle` (
    `key` VARCHAR(64) NOT NULL,
    `failures` INT NOT NULL DEFAULT 0,
    `window_started` DATETIME(6) NOT NULL,
    `locked_until` DATETIME(6) NULL,
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
