-- 实际 runner 使用 nohup + setsid 管理独立进程组，修正数据库中的错误标识。
UPDATE `algorithm`
SET `process_manager` = 'PROCESS_GROUP'
WHERE `process_manager` = 'SYSTEMD';

ALTER TABLE `algorithm`
  MODIFY COLUMN `process_manager` VARCHAR(32) NOT NULL DEFAULT 'PROCESS_GROUP'
  COMMENT '任务进程管理方式';
