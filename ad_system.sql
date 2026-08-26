/*
 Navicat Premium Dump SQL

 Source Server         : mysql
 Source Server Type    : MySQL
 Source Server Version : 90500 (9.5.0)
 Source Host           : localhost:3306
 Source Schema         : ad_system

 Target Server Type    : MySQL
 Target Server Version : 90500 (9.5.0)
 File Encoding         : 65001

 Date: 25/08/2026 10:26:42
*/

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ----------------------------
-- Table structure for admin
-- ----------------------------
DROP TABLE IF EXISTS `admin`;
CREATE TABLE `admin` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT 'ID',
  `username` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '账号',
  `password` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '密码',
  `name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '名称',
  `avatar` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '头像',
  `role` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '角色',
  `token_version` int NOT NULL DEFAULT '0' COMMENT '令牌版本',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE KEY `username` (`username`) USING BTREE
) ENGINE=InnoDB AUTO_INCREMENT=6 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='管理员信息';

-- ----------------------------
-- Table structure for admin_conversation
-- ----------------------------
DROP TABLE IF EXISTS `admin_conversation`;
CREATE TABLE `admin_conversation` (
  `id` int NOT NULL AUTO_INCREMENT,
  `admin_id` int NOT NULL,
  `title` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '新对话',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  KEY `idx_admin_conversation_admin_updated` (`admin_id`,`updated_at`),
  CONSTRAINT `fk_admin_conversation_admin` FOREIGN KEY (`admin_id`) REFERENCES `admin` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=5 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------
-- Table structure for admin_message
-- ----------------------------
DROP TABLE IF EXISTS `admin_message`;
CREATE TABLE `admin_message` (
  `id` int NOT NULL AUTO_INCREMENT,
  `conversation_id` int NOT NULL,
  `role` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `content` longtext COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  KEY `idx_admin_message_conversation_created` (`conversation_id`,`created_at`),
  CONSTRAINT `fk_admin_message_conversation` FOREIGN KEY (`conversation_id`) REFERENCES `admin_conversation` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=32 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------
-- Table structure for algorithm
-- ----------------------------
DROP TABLE IF EXISTS `algorithm`;
CREATE TABLE `algorithm` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT 'ID',
  `algorithm_id` int NOT NULL COMMENT '关联所属算法',
  `framework` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '所使用的框架',
  `framework_version` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '框架版本号',
  `python_version` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'Python 版本要求',
  `cuda_requirement` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'CUDA 版本要求',
  `conda_env_name` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'Conda 独立环境名称',
  `conda_env_path` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'Conda 环境绝对路径',
  `working_directory` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '算法运行工作目录',
  `train_entrypoint` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '训练入口脚本路径',
  `inference_entrypoint` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '推理入口脚本路径',
  `executor_type` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'GPU' COMMENT '执行器类型',
  `process_manager` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'SYSTEMD' COMMENT '任务进程管理方式',
  `protocol_version` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '1.0' COMMENT 'JSONL 训练协议版本',
  `sse_enabled` tinyint(1) NOT NULL DEFAULT '1' COMMENT '是否支持 SSE 实时推送',
  `parameter_schema_json` json DEFAULT NULL COMMENT '参数结构定义',
  `output_schema_json` json DEFAULT NULL COMMENT '输出结构定义',
  `resource_spec_json` json DEFAULT NULL COMMENT '资源需求规格',
  `dataset_requirement_json` json DEFAULT NULL COMMENT '数据集要求定义',
  PRIMARY KEY (`id`) USING BTREE,
  KEY `fk_algorithm_versions_algorithm` (`algorithm_id`),
  CONSTRAINT `fk_algorithm_versions_algorithm` FOREIGN KEY (`algorithm_id`) REFERENCES `algorithms` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB AUTO_INCREMENT=10 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='算法信息';

-- ----------------------------
-- Table structure for algorithms
-- ----------------------------
DROP TABLE IF EXISTS `algorithms`;
CREATE TABLE `algorithms` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT 'ID',
  `algorithm_no` char(26) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '算法编号',
  `name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '算法名称',
  `abbreviation` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '算法简称/缩写',
  `description` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '算法描述',
  `task_category` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'ANOMALY_DETECTION' COMMENT '任务类别',
  `created_by` int NOT NULL COMMENT '创建人 ID',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  `deleted_at` datetime(3) DEFAULT NULL COMMENT '软删除标记',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE KEY `uk_algorithms_no` (`algorithm_no`) USING BTREE,
  KEY `idx_algorithms_name` (`name`) USING BTREE,
  KEY `fk_algorithms_creator` (`created_by`),
  CONSTRAINT `fk_algorithms_creator` FOREIGN KEY (`created_by`) REFERENCES `admin` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB AUTO_INCREMENT=10 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='算法集合';

-- ----------------------------
-- Table structure for auth_session
-- ----------------------------
DROP TABLE IF EXISTS `auth_session`;
CREATE TABLE `auth_session` (
  `id` varchar(36) COLLATE utf8mb4_unicode_ci NOT NULL,
  `user_id` int NOT NULL,
  `role` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `refresh_jti` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `expires_at` datetime(6) NOT NULL,
  `revoked_at` datetime(6) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_auth_session_refresh_jti` (`refresh_jti`),
  KEY `idx_auth_session_principal` (`role`,`user_id`),
  KEY `idx_auth_session_expires_at` (`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------
-- Table structure for conversation
-- ----------------------------
DROP TABLE IF EXISTS `conversation`;
CREATE TABLE `conversation` (
  `id` int NOT NULL AUTO_INCREMENT,
  `title` varchar(255) NOT NULL DEFAULT '新对话',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  `user_id` int NOT NULL,
  PRIMARY KEY (`id`),
  KEY `fk_conversa_user_84883661` (`user_id`),
  CONSTRAINT `fk_conversa_user_84883661` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=11 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='对话信息';

-- ----------------------------
-- Table structure for dataset
-- ----------------------------
DROP TABLE IF EXISTS `dataset`;
CREATE TABLE `dataset` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT 'ID',
  `dataset_id` int NOT NULL COMMENT '关联所属数据集',
  `root_directory` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '数据根目录路径',
  `class_count` int NOT NULL DEFAULT '0' COMMENT '类别数量',
  `train_sample_count` int NOT NULL DEFAULT '0' COMMENT '训练集样本数',
  `test_sample_count` int NOT NULL DEFAULT '0' COMMENT '测试集样本数',
  `anomaly_sample_count` int NOT NULL DEFAULT '0' COMMENT '异常样本数',
  PRIMARY KEY (`id`) USING BTREE,
  KEY `idx_dataset_creator` (`dataset_id`) USING BTREE,
  CONSTRAINT `fk_dataset_versions_dataset` FOREIGN KEY (`dataset_id`) REFERENCES `datasets` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB AUTO_INCREMENT=10 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='数据集信息';

-- ----------------------------
-- Table structure for datasets
-- ----------------------------
DROP TABLE IF EXISTS `datasets`;
CREATE TABLE `datasets` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT 'ID',
  `dataset_no` char(26) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '数据集编号',
  `name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '数据集名称',
  `description` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '数据集描述',
  `domain_type` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '数据集领域类型',
  `created_by` int NOT NULL COMMENT '创建人 ID',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  `deleted_at` datetime(3) DEFAULT NULL COMMENT '软删除标记',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE KEY `uk_datasets_no` (`dataset_no`) USING BTREE,
  KEY `idx_datasets_name` (`name`) USING BTREE,
  KEY `idx_datasets_creator` (`created_by`) USING BTREE,
  CONSTRAINT `fk_datasets_creator` FOREIGN KEY (`created_by`) REFERENCES `admin` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB AUTO_INCREMENT=10 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='数据集集合';

-- ----------------------------
-- Table structure for inference_jobs
-- ----------------------------
DROP TABLE IF EXISTS `inference_jobs`;
CREATE TABLE `inference_jobs` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `job_no` varchar(36) COLLATE utf8mb4_unicode_ci NOT NULL,
  `owner_id` int NOT NULL,
  `owner_role` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `training_job_id` bigint NOT NULL,
  `status` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'QUEUED',
  `config_json` json NOT NULL,
  `result_json` json DEFAULT NULL,
  `assigned_gpu` int DEFAULT NULL,
  `remote_control_dir` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `remote_run_dir` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `launcher_pid` int DEFAULT NULL,
  `exit_code` int DEFAULT NULL,
  `failure_reason` varchar(1000) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `submitted_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `started_at` datetime(6) DEFAULT NULL,
  `finished_at` datetime(6) DEFAULT NULL,
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_inference_jobs_job_no` (`job_no`),
  KEY `idx_inference_jobs_status_gpu` (`status`,`assigned_gpu`),
  KEY `idx_inference_jobs_owner` (`owner_role`,`owner_id`),
  KEY `idx_inference_jobs_training_job` (`training_job_id`),
  CONSTRAINT `fk_inference_jobs_training_job` FOREIGN KEY (`training_job_id`) REFERENCES `training_jobs` (`id`) ON DELETE RESTRICT
) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------
-- Table structure for knowledge
-- ----------------------------
DROP TABLE IF EXISTS `knowledge`;
CREATE TABLE `knowledge` (
  `id` int NOT NULL AUTO_INCREMENT,
  `filename` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `original_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `file_size` int DEFAULT NULL,
  `chunk_count` int DEFAULT NULL,
  `created_at` datetime(6) DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=6 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='知识库信息';

-- ----------------------------
-- Table structure for login_throttle
-- ----------------------------
DROP TABLE IF EXISTS `login_throttle`;
CREATE TABLE `login_throttle` (
  `key` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `failures` int NOT NULL DEFAULT '0',
  `window_started` datetime(6) NOT NULL,
  `locked_until` datetime(6) DEFAULT NULL,
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------
-- Table structure for message
-- ----------------------------
DROP TABLE IF EXISTS `message`;
CREATE TABLE `message` (
  `id` int NOT NULL AUTO_INCREMENT,
  `role` varchar(20) NOT NULL,
  `content` longtext NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `conversation_id` int NOT NULL,
  PRIMARY KEY (`id`),
  KEY `fk_message_conversa_a556956b` (`conversation_id`),
  CONSTRAINT `fk_message_conversa_a556956b` FOREIGN KEY (`conversation_id`) REFERENCES `conversation` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=93 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='消息信息';

-- ----------------------------
-- Table structure for notice
-- ----------------------------
DROP TABLE IF EXISTS `notice`;
CREATE TABLE `notice` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '标题',
  `content` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '内容',
  `time` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '时间',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=5 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='公告信息';

-- ----------------------------
-- Table structure for rag_retrieval_traces
-- ----------------------------
DROP TABLE IF EXISTS `rag_retrieval_traces`;
CREATE TABLE `rag_retrieval_traces` (
  `id` varchar(36) COLLATE utf8mb4_unicode_ci NOT NULL,
  `conversation_type` varchar(16) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `conversation_id` int DEFAULT NULL,
  `message_id` int DEFAULT NULL,
  `principal_role` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `principal_id` int DEFAULT NULL,
  `query_hash` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `mode` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'knowledge_base',
  `release_id` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `status` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'completed',
  `error_code` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `embedding_provider` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `embedding_model` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `embedding_schema_version` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `prompt_version` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `reranker_model` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `retrieval_config` json DEFAULT NULL,
  `candidate_counts` json DEFAULT NULL,
  `stage_durations_ms` json DEFAULT NULL,
  `token_usage` json DEFAULT NULL,
  `candidates` json DEFAULT NULL,
  `citation_map` json DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `completed_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_rag_trace_created` (`created_at`),
  KEY `idx_rag_trace_release` (`release_id`),
  KEY `idx_rag_trace_status` (`status`),
  KEY `idx_rag_trace_conversation` (`conversation_id`),
  KEY `idx_rag_trace_message` (`message_id`),
  KEY `idx_rag_trace_principal` (`principal_id`),
  KEY `idx_rag_trace_query_hash` (`query_hash`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------
-- Table structure for training_artifacts
-- ----------------------------
DROP TABLE IF EXISTS `training_artifacts`;
CREATE TABLE `training_artifacts` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `job_id` bigint NOT NULL,
  `artifact_type` varchar(40) COLLATE utf8mb4_unicode_ci NOT NULL,
  `artifact_role` varchar(40) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'OTHER',
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `remote_path` varchar(1000) COLLATE utf8mb4_unicode_ci NOT NULL,
  `size_bytes` bigint NOT NULL DEFAULT '0',
  `downloadable` tinyint(1) NOT NULL DEFAULT '1',
  `metadata_json` json DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_training_artifacts_job_path` (`job_id`,`remote_path`(700)),
  KEY `idx_training_artifacts_type` (`artifact_type`),
  KEY `idx_training_artifacts_role` (`artifact_role`),
  KEY `idx_training_artifacts_downloadable` (`downloadable`),
  CONSTRAINT `fk_training_artifacts_job` FOREIGN KEY (`job_id`) REFERENCES `training_jobs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=992 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------
-- Table structure for training_audits
-- ----------------------------
DROP TABLE IF EXISTS `training_audits`;
CREATE TABLE `training_audits` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `job_id` bigint NOT NULL,
  `actor_id` int DEFAULT NULL,
  `actor_role` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '系统',
  `action` varchar(40) COLLATE utf8mb4_unicode_ci NOT NULL,
  `result` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'SUCCESS',
  `message` varchar(1000) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `payload_json` json DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  KEY `idx_training_audits_job_created` (`job_id`,`created_at`),
  KEY `idx_training_audits_actor_id` (`actor_id`),
  KEY `idx_training_audits_actor_role` (`actor_role`),
  KEY `idx_training_audits_action` (`action`),
  KEY `idx_training_audits_created` (`created_at`),
  CONSTRAINT `fk_training_audits_job` FOREIGN KEY (`job_id`) REFERENCES `training_jobs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=35 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------
-- Table structure for training_events
-- ----------------------------
DROP TABLE IF EXISTS `training_events`;
CREATE TABLE `training_events` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `job_id` bigint NOT NULL,
  `sequence` int NOT NULL,
  `event_type` varchar(40) COLLATE utf8mb4_unicode_ci NOT NULL,
  `message` varchar(1000) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `payload_json` json DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_training_events_job_sequence` (`job_id`,`sequence`),
  KEY `idx_training_events_type` (`event_type`),
  KEY `idx_training_events_created` (`created_at`),
  CONSTRAINT `fk_training_events_job` FOREIGN KEY (`job_id`) REFERENCES `training_jobs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=74 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------
-- Table structure for training_job_deletions
-- ----------------------------
DROP TABLE IF EXISTS `training_job_deletions`;
CREATE TABLE `training_job_deletions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `original_job_id` bigint NOT NULL,
  `job_no` varchar(36) COLLATE utf8mb4_unicode_ci NOT NULL,
  `owner_id` int NOT NULL,
  `owner_role` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `algorithm_id` bigint NOT NULL,
  `dataset_id` bigint NOT NULL,
  `terminal_status` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL,
  `actor_id` int NOT NULL,
  `actor_role` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '管理员',
  `reason` varchar(500) COLLATE utf8mb4_unicode_ci NOT NULL,
  `snapshot_json` json NOT NULL,
  `deleted_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_training_job_deletions_job_no` (`job_no`),
  KEY `idx_training_job_deletions_original_job_id` (`original_job_id`),
  KEY `idx_training_job_deletions_actor_id` (`actor_id`),
  KEY `idx_training_job_deletions_deleted_at` (`deleted_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------
-- Table structure for training_jobs
-- ----------------------------
DROP TABLE IF EXISTS `training_jobs`;
CREATE TABLE `training_jobs` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `job_no` varchar(36) COLLATE utf8mb4_unicode_ci NOT NULL,
  `owner_id` int NOT NULL,
  `owner_role` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `algorithm_id` int NOT NULL,
  `dataset_id` int NOT NULL,
  `status` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'QUEUED',
  `config_json` json NOT NULL,
  `runtime_snapshot_json` json DEFAULT NULL,
  `assigned_gpu` int DEFAULT NULL,
  `remote_control_dir` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `remote_run_dir` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `launcher_pid` int DEFAULT NULL,
  `worker_pid` int DEFAULT NULL,
  `process_pid` int DEFAULT NULL,
  `process_pgid` int DEFAULT NULL,
  `exit_code` int DEFAULT NULL,
  `failure_code` varchar(40) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `failure_reason` varchar(1000) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `retry_of_job_id` bigint DEFAULT NULL,
  `attempt` int NOT NULL DEFAULT '1',
  `progress_percent` double NOT NULL DEFAULT '0',
  `current_epoch` int DEFAULT NULL,
  `total_epochs` int DEFAULT NULL,
  `log_offset` bigint NOT NULL DEFAULT '0',
  `timeout_seconds` int DEFAULT NULL,
  `cleanup_status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'RETAINED',
  `cleaned_at` datetime(6) DEFAULT NULL,
  `reconcile_failures` int NOT NULL DEFAULT '0',
  `archived_at` datetime(6) DEFAULT NULL,
  `archived_by` int DEFAULT NULL,
  `submitted_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `started_at` datetime(6) DEFAULT NULL,
  `finished_at` datetime(6) DEFAULT NULL,
  `last_reconciled_at` datetime(6) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_training_jobs_job_no` (`job_no`),
  KEY `idx_training_jobs_status_gpu` (`status`,`assigned_gpu`),
  KEY `idx_training_jobs_owner` (`owner_role`,`owner_id`),
  KEY `idx_training_jobs_algorithm` (`algorithm_id`),
  KEY `idx_training_jobs_dataset` (`dataset_id`),
  KEY `idx_training_jobs_retry_of` (`retry_of_job_id`),
  KEY `idx_training_jobs_failure_code` (`failure_code`),
  KEY `idx_training_jobs_cleanup_status` (`cleanup_status`),
  KEY `idx_training_jobs_archived_at` (`archived_at`),
  KEY `idx_training_jobs_archived_by` (`archived_by`),
  CONSTRAINT `fk_training_jobs_algorithm` FOREIGN KEY (`algorithm_id`) REFERENCES `algorithms` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_training_jobs_dataset` FOREIGN KEY (`dataset_id`) REFERENCES `datasets` (`id`) ON DELETE RESTRICT
) ENGINE=InnoDB AUTO_INCREMENT=26 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------
-- Table structure for training_logs
-- ----------------------------
DROP TABLE IF EXISTS `training_logs`;
CREATE TABLE `training_logs` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `job_id` bigint NOT NULL,
  `sequence` int NOT NULL,
  `stream` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'STDOUT',
  `content` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `remote_offset` bigint NOT NULL DEFAULT '0',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_training_logs_job_sequence` (`job_id`,`sequence`),
  KEY `idx_training_logs_job_id` (`job_id`,`id`),
  KEY `idx_training_logs_created` (`created_at`),
  CONSTRAINT `fk_training_logs_job` FOREIGN KEY (`job_id`) REFERENCES `training_jobs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=75 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------
-- Table structure for training_metrics
-- ----------------------------
DROP TABLE IF EXISTS `training_metrics`;
CREATE TABLE `training_metrics` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `job_id` bigint NOT NULL,
  `metric_name` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `metric_value` double NOT NULL,
  `epoch` int DEFAULT NULL,
  `step` int DEFAULT NULL,
  `recorded_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  KEY `idx_training_metrics_job_name` (`job_id`,`metric_name`),
  CONSTRAINT `fk_training_metrics_job` FOREIGN KEY (`job_id`) REFERENCES `training_jobs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=108 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------
-- Table structure for user
-- ----------------------------
DROP TABLE IF EXISTS `user`;
CREATE TABLE `user` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `username` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '账号',
  `password` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '密码',
  `name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '名称',
  `avatar` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '头像',
  `role` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '角色',
  `token_version` int NOT NULL DEFAULT '0' COMMENT '令牌版本',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=11 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户信息';

SET FOREIGN_KEY_CHECKS = 1;
