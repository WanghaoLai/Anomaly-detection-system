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

 Date: 17/07/2026 15:49:31
*/

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ----------------------------
-- Table structure for address
-- ----------------------------
DROP TABLE IF EXISTS `address`;
CREATE TABLE `address` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `user_id` int DEFAULT NULL COMMENT '用户ID',
  `name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '收货人',
  `address` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '收货地址',
  `phone` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '联系方式',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=5 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='地址信息';

-- ----------------------------
-- Records of address
-- ----------------------------
BEGIN;
INSERT INTO `address` (`id`, `user_id`, `name`, `address`, `phone`) VALUES (1, 1, '111', '123', '222');
INSERT INTO `address` (`id`, `user_id`, `name`, `address`, `phone`) VALUES (3, 1, '333', '333', '333');
INSERT INTO `address` (`id`, `user_id`, `name`, `address`, `phone`) VALUES (4, 4, '234', '234', '234');
COMMIT;

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
) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='管理员信息';

-- ----------------------------
-- Records of admin
-- ----------------------------
BEGIN;
INSERT INTO `admin` (`id`, `username`, `password`, `name`, `avatar`, `role`) VALUES (1, 'admin', 'admin', '管理员', 'http://localhost:9090/files/download/1.png', '管理员');
INSERT INTO `admin` (`id`, `username`, `password`, `name`, `avatar`, `role`) VALUES (2, '222', '222', '222', 'http://127.0.0.1:9090/files/download/head.jpg', '管理员');
INSERT INTO `admin` (`id`, `username`, `password`, `name`, `avatar`, `role`) VALUES (3, '111', '$2b$12$j62UQt7k0BvjYrBdVBkW9ugLlSkUic7qOCwSNBXLLaGuLnTdSG/tK', '111', 'http://127.0.0.1:9090/files/download/head.jpg', '管理员');
COMMIT;

-- ----------------------------
-- Table structure for auth_session
-- ----------------------------
DROP TABLE IF EXISTS `auth_session`;
CREATE TABLE `auth_session` (
  `id` varchar(36) NOT NULL,
  `user_id` int NOT NULL,
  `role` varchar(20) NOT NULL,
  `refresh_jti` varchar(64) NOT NULL,
  `expires_at` datetime(6) NOT NULL,
  `revoked_at` datetime(6) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_auth_session_refresh_jti` (`refresh_jti`),
  KEY `idx_auth_session_principal` (`role`, `user_id`),
  KEY `idx_auth_session_expires_at` (`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='认证会话';

-- ----------------------------
-- Table structure for login_throttle
-- ----------------------------
DROP TABLE IF EXISTS `login_throttle`;
CREATE TABLE `login_throttle` (
  `key` varchar(64) NOT NULL,
  `failures` int NOT NULL DEFAULT '0',
  `window_started` datetime(6) NOT NULL,
  `locked_until` datetime(6) DEFAULT NULL,
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='登录限流';

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
  `conda_env_name` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'Conda 独立环境名称',
  `conda_env_path` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'Conda 环境绝对路径',
  `working_directory` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '算法运行工作目录',
  `train_entrypoint` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '训练入口脚本路径',
  `inference_entrypoint` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '推理入口脚本路径',
  `executor_type` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'GPU' COMMENT '执行器类型',
  `process_manager` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'SYSTEMD' COMMENT '任务进程管理方式',
  `protocol_version` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '1.0' COMMENT 'JSONL 训练协议版本',
  `sse_enabled` tinyint(1) NOT NULL DEFAULT '1' COMMENT '是否支持 SSE 实时推送',
  `parameter_schema_json` json DEFAULT NULL COMMENT '参数结构定义',
  `output_schema_json` json DEFAULT NULL COMMENT '输出结构定义',
  `resource_spec_json` json DEFAULT NULL COMMENT '资源需求规格',
  `dataset_requirement_json` json DEFAULT NULL COMMENT '数据集要求定义',
  PRIMARY KEY (`id`) USING BTREE,
  KEY `fk_algorithm_versions_algorithm` (`algorithm_id`),
  CONSTRAINT `fk_algorithm_versions_algorithm` FOREIGN KEY (`algorithm_id`) REFERENCES `algorithms` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB AUTO_INCREMENT=2 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='算法信息';

-- ----------------------------
-- Records of algorithm
-- ----------------------------
BEGIN;
INSERT INTO `algorithm` (`id`, `algorithm_id`, `framework`, `framework_version`, `python_version`, `cuda_requirement`, `conda_env_name`, `conda_env_path`, `working_directory`, `train_entrypoint`, `inference_entrypoint`, `executor_type`, `process_manager`, `protocol_version`, `sse_enabled`, `parameter_schema_json`, `output_schema_json`, `resource_spec_json`, `dataset_requirement_json`) VALUES (1, 1, 'PyTorch', '2.1.0', '3.10', 'CUDA 11.8', 'pbas', '/opt/conda/envs/pbas', '/srv/algorithms/pbas', 'train.py', 'test.py', 'GPU', 'SYSTEMD', '1.0', 1, JSON_OBJECT(), JSON_OBJECT(), JSON_OBJECT('gpu_count', 1), JSON_OBJECT());
COMMIT;

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
) ENGINE=InnoDB AUTO_INCREMENT=2 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='算法集合';

-- ----------------------------
-- Records of algorithms
-- ----------------------------
BEGIN;
INSERT INTO `algorithms` (`id`, `algorithm_no`, `name`, `abbreviation`, `description`, `task_category`, `created_by`, `created_at`, `updated_at`, `deleted_at`) VALUES (1, '1', 'Progressive Boundary Anomaly Synthesis', 'PBAS', '这是一个渐进式边界引导的异常检测算法。', 'ANOMALY_DETECTION', 1, '2026-07-16 20:49:05.713', '2026-07-16 20:49:05.713', NULL);
COMMIT;

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
) ENGINE=InnoDB AUTO_INCREMENT=10 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='对话信息';

-- ----------------------------
-- Records of conversation
-- ----------------------------
BEGIN;
INSERT INTO `conversation` (`id`, `title`, `created_at`, `updated_at`, `user_id`) VALUES (5, 'Hello', '2026-06-04 13:30:12.321516', '2026-06-04 21:30:13.191019', 1);
INSERT INTO `conversation` (`id`, `title`, `created_at`, `updated_at`, `user_id`) VALUES (8, '我的订单', '2026-07-04 02:55:46.580183', '2026-07-04 10:55:53.797417', 1);
INSERT INTO `conversation` (`id`, `title`, `created_at`, `updated_at`, `user_id`) VALUES (9, '配送范围是什么', '2026-07-07 02:20:27.718161', '2026-07-07 02:20:48.482741', 3);
COMMIT;

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
  `mask_count` int NOT NULL DEFAULT '0' COMMENT '掩码（标注）数量',
  PRIMARY KEY (`id`) USING BTREE,
  KEY `idx_dataset_creator` (`dataset_id`) USING BTREE,
  CONSTRAINT `fk_dataset_versions_dataset` FOREIGN KEY (`dataset_id`) REFERENCES `datasets` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB AUTO_INCREMENT=2 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='数据集信息';

-- ----------------------------
-- Records of dataset
-- ----------------------------
BEGIN;
INSERT INTO `dataset` (`id`, `dataset_id`, `root_directory`, `class_count`, `train_sample_count`, `test_sample_count`, `anomaly_sample_count`, `mask_count`) VALUES (1, 1, '/Users/xiaohao/Desktop/mvtec', 15, 1000, 100, 50, 50);
COMMIT;

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
) ENGINE=InnoDB AUTO_INCREMENT=2 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='数据集集合';

-- ----------------------------
-- Records of datasets
-- ----------------------------
BEGIN;
INSERT INTO `datasets` (`id`, `dataset_no`, `name`, `description`, `domain_type`, `created_by`, `created_at`, `updated_at`, `deleted_at`) VALUES (1, '1', 'MVTec AD', '这是一个典型的工业基准数据集。', '工业', 1, '2026-07-16 16:23:20.690', '2026-07-16 16:23:20.690', NULL);
COMMIT;

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
) ENGINE=InnoDB AUTO_INCREMENT=2 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='知识库信息';

-- ----------------------------
-- Records of knowledge
-- ----------------------------
BEGIN;
INSERT INTO `knowledge` (`id`, `filename`, `original_name`, `file_size`, `chunk_count`, `created_at`) VALUES (1, 'cbbee6c0c4d94ea6a2f05f42be3b7770', '蛋糕商城智能客服知识库文档.docx', 33569, 12, '2026-07-07 02:20:02.572812');
COMMIT;

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
) ENGINE=InnoDB AUTO_INCREMENT=67 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='消息信息';

-- ----------------------------
-- Records of message
-- ----------------------------
BEGIN;
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (17, 'user', 'Hello', '2026-06-04 13:30:12.334395', 5);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (18, 'assistant', 'Hello! 😊 欢迎来到 Little-bear Cake Store！我是您的智能客服助手，有什么可以帮您的吗？无论是想了解我们的蛋糕产品、查询订单状态，还是需要推荐，我都在这里哦！', '2026-06-04 13:30:13.188452', 5);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (21, 'user', '祝寿蛋糕', '2026-07-04 02:26:09.181769', 5);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (22, 'assistant', '您好！我们有一款非常适合祝寿的蛋糕——「福寿安康祝寿蛋糕」，价格是 108.0 元。这款蛋糕口感温润、松软低脂，搭配鲜果和软糯夹层，寓意长辈平安健康、福寿绵长。非常适合表达对长辈的祝福哦！🎉🎂 如果您有其他需求，欢迎随时告诉我～', '2026-07-04 02:26:10.327035', 5);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (23, 'user', '有什么祝寿蛋糕推荐吗', '2026-07-04 02:26:35.324564', 5);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (24, 'assistant', '我们有几款非常受欢迎的祝寿蛋糕推荐给您：\n\n1. **福寿安康祝寿蛋糕** - 价格 108.0 元，口感温润、松软低脂，搭配鲜果和软糯夹层，寓意长辈平安健康、福寿绵长。\n2. **长寿金榜蛋糕** - 价格 138.0 元，采用经典红丝绒口味，内含坚果夹层，象征事业高升、福寿双全。\n3. **百岁好合蛋糕** - 价格 168.0 元，融合香草与巧克力风味，装饰精美，适合表达对长辈的美好祝愿。\n\n如果您需要定制或有其他需求，欢迎随时告诉我哦！🎂✨', '2026-07-04 02:26:37.273435', 5);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (25, 'user', '我的订单', '2026-07-04 02:40:16.738281', 5);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (26, 'assistant', '请提供您的用户ID，以便我为您查询订单状态。如果您不确定用户ID，也可以提供其他信息，我会尽力帮您查找。😊', '2026-07-04 02:40:17.398679', 5);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (27, 'user', '321', '2026-07-04 02:40:32.185239', 5);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (28, 'assistant', '您的订单信息如下：\n\n- 订单 #1：轻奢双人告白蛋糕 x1，¥88.0，下单时间：2026-05-19 17:28:15\n\n如果您需要进一步帮助，比如取消订单或了解其他信息，请随时告诉我哦！🎂', '2026-07-04 02:40:33.786729', 5);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (29, 'user', '有什么蛋糕推荐吗', '2026-07-04 02:40:56.067460', 5);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (30, 'assistant', '看起来我遇到了一点小问题，无法为您推荐蛋糕。您可以告诉我您的偏好（比如“生日”、“情侣”、“送朋友”等），我会尽力为您提供合适的建议！🎂✨', '2026-07-04 02:40:57.204334', 5);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (31, 'user', '过生日有什么蛋糕推荐吗', '2026-07-04 02:41:10.789782', 5);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (32, 'assistant', '很抱歉，我暂时无法为您提供生日蛋糕推荐。您可以告诉我您喜欢的口味或风格，我会尽力为您提供建议！🎂✨', '2026-07-04 02:41:12.032151', 5);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (35, 'user', '我的订单', '2026-07-04 02:45:43.331274', 5);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (36, 'assistant', '您的订单信息如下：\n\n- 订单 #1：轻奢双人告白蛋糕 x1，¥88.0，下单时间：2026-05-19 17:28:15\n\n如果您需要进一步帮助，比如取消订单或了解其他信息，请随时告诉我哦！🎂', '2026-07-04 02:45:44.888853', 5);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (47, 'user', '我的订单', '2026-07-04 02:55:53.154376', 8);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (48, 'assistant', '请提供您的订单ID或订单号，以便我为您查询订单状态。', '2026-07-04 02:55:53.793931', 8);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (49, 'user', '202607041055091665', '2026-07-04 02:56:13.281860', 8);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (50, 'assistant', '您的订单信息如下：\n\n订单号：202607041055091665  \n- 商品：欢聚同乐蛋糕  \n- 数量：1  \n- 单价：¥88.0  \n- 总价：¥88.0  \n- 收货地址：333  \n- 下单时间：2026-07-04 10:55:09  \n- 状态：待发货  \n\n如有其他问题，欢迎随时咨询！🎂', '2026-07-04 02:56:15.209319', 8);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (51, 'user', '配送范围是什么', '2026-07-07 02:20:46.009933', 9);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (52, 'assistant', '我们的配送范围包括核心商圈的即时配送（2-4小时内送达），以及预约配送和定时达服务。您可以在下单时选择指定的送达日期和时段（如10:00-12:00、14:00-16:00、18:00-20:00）。常规配送服务时间为每日 09:00 - 21:00，节假日（如春节、情人节、母亲节、圣诞节等）配送量激增，建议提前2-3天预约，当日订单可能无法保证指定时段送达。恶劣天气可能导致配送延迟，配送员会提前与您联系协商。', '2026-07-07 02:20:48.476482', 9);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (53, 'user', '常规款蛋糕什么时候配送', '2026-07-07 02:21:29.944567', 9);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (54, 'assistant', '常规款蛋糕的配送时间如下：\n\n- **即时配送（同城急送）**：适用于现货常规款蛋糕，下单后2-4小时内送达，覆盖核心商圈。\n- **预约配送**：用户可指定送达日期和时段（如10:00-12:00、14:00-16:00、18:00-20:00），系统将在指定时段内完成配送。\n- **定时达**：支持精确到小时的配送预约，需提前至少24小时下单。\n\n常规配送服务时间是每日 **09:00 - 21:00**。如果在节假日（如春节、情人节、母亲节、圣诞节等）下单，建议提前2-3天预约，因为这些时段配送量大，可能无法保证指定时段送达。', '2026-07-07 02:21:33.298573', 9);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (55, 'user', '七天无理由退化', '2026-07-07 02:22:11.058068', 9);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (56, 'assistant', '根据《中华人民共和国消费者权益保护法》及相关规定，蛋糕属于“易于腐败、保存期限较短”的商品，因此**不适用七天无理由退货**政策。以下情况也不接受任何形式的退货：\n\n1. 已拆封或食用的蛋糕；\n2. 因消费者保存不当（如未冷藏导致变质、失温融化）造成的损坏；\n3. 个人主观因素（口味不合、甜度不满意、与图片有色差等）要求退货；\n4. 超过24小时才反映商品问题且无有效凭证；\n5. 轻微运输瑕疵（装饰物轻微位移、奶油轻微刮痕）不影响食用的情况；\n6. 非商城责任（如第三方配送导致延迟，但蛋糕品质未受影响）。\n\n如有质量问题（如运输途中严重毁损、错送款式等），我们提供售后处理。建议您在收到蛋糕后尽快检查，并在24小时内联系客服处理。', '2026-07-07 02:22:15.764137', 9);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (57, 'user', '祝寿蛋糕的配送费是多少？', '2026-07-07 02:23:21.179181', 9);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (58, 'assistant', '祝寿蛋糕的配送费用会根据收货地址、订单金额、蛋糕尺寸和重量综合计算，具体费用以您在下单页面看到的金额为准。如在特殊时段（如情人节、除夕等），可能会有节日配送服务费，会在下单页面提前明示。', '2026-07-07 02:23:22.985135', 9);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (59, 'user', '祝寿蛋糕有哪些推荐', '2026-07-07 02:23:47.563557', 9);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (60, 'assistant', '为您推荐以下祝寿蛋糕：\n\n1. **福寿安康祝寿蛋糕** - ¥108.0（有货，剩余5份）  \n   松软低脂口感温润，鲜果搭配软糯夹层，祝愿长辈平安健康福寿绵长。\n\n2. **轻奢双层祝寿蛋糕** - ¥188.0（有货，剩余18份）  \n   双层造型大气华丽，用料扎实口感细腻，宴席聚会庆生场面十足。\n\n如果您需要定制款或有特殊需求，建议提前3-7天预订哦！祝您选购愉快~ 🎂', '2026-07-07 02:23:50.494373', 9);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (61, 'user', '我想退货', '2026-07-07 06:36:29.355397', 8);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (62, 'assistant', '根据您的订单号202607041055091665，当前订单状态为“待发货”。如果您需要取消订单，请告知，我将为您处理。需要注意的是，若订单已进入制作流程，可能无法取消或需支付一定手续费。请确认您的需求！', '2026-07-07 06:36:31.123538', 8);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (63, 'user', '退货规则是什么', '2026-07-07 06:36:42.306790', 8);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (64, 'assistant', '根据我们的退货规则，以下是关键点：\n\n1. **不适用七天无理由退货**：蛋糕属于易腐商品，且定制蛋糕是依消费者要求制作的，因此不适用七天无理由退货。\n\n2. **不接受退货的情形**：\n   - 已拆封、食用的蛋糕；\n   - 因保存不当（如未冷藏导致变质）造成的损坏；\n   - 个人主观因素（如口味不合、甜度不满意等）；\n   - 超过24小时才反映问题且无有效凭证；\n   - 轻微运输瑕疵（如装饰物轻微位移、奶油轻微刮痕）；\n   - 非商城责任（如第三方配送导致延迟，但蛋糕品质未受影响）。\n\n3. **质量问题售后处理**：\n   - 若蛋糕在运输途中发生严重毁损（倾倒、大面积变形、融化）、商品与订单不符或存在明显变质等问题，可申请售后。\n   - 售后流程包括即时反馈、提供凭证（开箱视频、照片等）、客服审核及处理方案（补发、部分退款或全额退款）。\n   - 退款时效为审核通过后7-14个工作日，信用卡退款可能需要更长时间。\n\n4. **退换货费用承担**：根据具体情况而定。\n\n如果您遇到任何问题，请尽快联系客服，并提供相关凭证。希望这些信息对您有帮助！如有其他疑问，请随时告知 😊', '2026-07-07 06:36:45.647517', 8);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (65, 'user', '你好', '2026-07-17 03:35:53.199600', 9);
INSERT INTO `message` (`id`, `role`, `content`, `created_at`, `conversation_id`) VALUES (66, 'assistant', '你好！👋 有什么可以帮到你的吗？无论是关于蛋糕款式、配送信息，还是订单相关的问题，都可以告诉我哦！', '2026-07-17 03:35:54.315151', 9);
COMMIT;

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
-- Records of notice
-- ----------------------------
BEGIN;
INSERT INTO `notice` (`id`, `name`, `content`, `time`) VALUES (4, '机器人与智能系统实验室', '机器人与智能系统实验室-异常检测系统Beta版今日已上线！', '2026-07-17 10:53:47');
COMMIT;

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
) ENGINE=InnoDB AUTO_INCREMENT=6 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户信息';

-- ----------------------------
-- Records of user
-- ----------------------------
BEGIN;
INSERT INTO `user` (`id`, `username`, `password`, `name`, `avatar`, `role`) VALUES (1, '123', '$2b$12$kbu9Kh/ChoYOvwMGn/3QyuMF7tZ7HFESMut5KnATm689BlSGPUAb.', '123', 'http://127.0.0.1:9090/files/download/head.jpg', '用户');
INSERT INTO `user` (`id`, `username`, `password`, `name`, `avatar`, `role`) VALUES (3, '222', '123', '222', 'http://127.0.0.1:9090/files/download/head.jpg', '用户');
COMMIT;

SET FOREIGN_KEY_CHECKS = 1;
