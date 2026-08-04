-- 管理员智能助手会话与消息表。
-- 与用户的 conversation/message 表完全独立，避免 Admin.id 与 User.id 主键
-- 序列重叠造成不同账号会话相互串扰，也便于按管理员维度做权限隔离。
CREATE TABLE IF NOT EXISTS `admin_conversation` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `admin_id` INT NOT NULL,
    `title` VARCHAR(255) NOT NULL DEFAULT '新对话',
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (`id`),
    KEY `idx_admin_conversation_admin_updated` (`admin_id`, `updated_at`),
    CONSTRAINT `fk_admin_conversation_admin` FOREIGN KEY (`admin_id`)
        REFERENCES `admin` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `admin_message` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `conversation_id` INT NOT NULL,
    `role` VARCHAR(20) NOT NULL,
    `content` LONGTEXT NOT NULL,
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (`id`),
    KEY `idx_admin_message_conversation_created` (`conversation_id`, `created_at`),
    CONSTRAINT `fk_admin_message_conversation` FOREIGN KEY (`conversation_id`)
        REFERENCES `admin_conversation` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
