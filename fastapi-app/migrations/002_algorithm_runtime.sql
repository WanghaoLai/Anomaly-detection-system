-- 将算法运行配置从 Docker 镜像切换为 Conda + GPU 执行器配置。
-- 应在升级现有数据库时执行；全新安装请直接导入 ad_system.sql。
ALTER TABLE `algorithm`
    ADD COLUMN `conda_env_name` VARCHAR(128) NULL COMMENT 'Conda 独立环境名称' AFTER `cuda_requirement`,
    ADD COLUMN `conda_env_path` VARCHAR(500) NULL COMMENT 'Conda 环境绝对路径' AFTER `conda_env_name`,
    ADD COLUMN `working_directory` VARCHAR(500) NULL COMMENT '算法运行工作目录' AFTER `conda_env_path`,
    ADD COLUMN `executor_type` VARCHAR(32) NOT NULL DEFAULT 'GPU' COMMENT '执行器类型' AFTER `inference_entrypoint`,
    ADD COLUMN `process_manager` VARCHAR(32) NOT NULL DEFAULT 'SYSTEMD' COMMENT '任务进程管理方式' AFTER `executor_type`,
    ADD COLUMN `protocol_version` VARCHAR(32) NOT NULL DEFAULT '1.0' COMMENT 'JSONL 训练协议版本' AFTER `process_manager`,
    ADD COLUMN `sse_enabled` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否支持 SSE 实时推送' AFTER `protocol_version`,
    DROP COLUMN `docker_image`,
    DROP COLUMN `docker_image_digest`;

-- 为已有算法生成可展示、可继续编辑的初始运行配置；管理员可在页面中
-- 按实际 GPU 服务器目录调整这些值。
UPDATE `algorithm` AS info
JOIN `algorithms` AS base ON base.`id` = info.`algorithm_id`
SET
    info.`conda_env_name` = COALESCE(
        info.`conda_env_name`,
        LOWER(COALESCE(NULLIF(base.`abbreviation`, ''), CONCAT('algorithm-', base.`id`)))
    ),
    info.`conda_env_path` = COALESCE(
        info.`conda_env_path`,
        CONCAT(
            '/opt/conda/envs/',
            LOWER(COALESCE(NULLIF(base.`abbreviation`, ''), CONCAT('algorithm-', base.`id`)))
        )
    ),
    info.`working_directory` = COALESCE(
        info.`working_directory`,
        CONCAT(
            '/srv/algorithms/',
            LOWER(COALESCE(NULLIF(base.`abbreviation`, ''), CONCAT('algorithm-', base.`id`)))
        )
    );
