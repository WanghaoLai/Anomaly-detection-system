-- 阶段 2：任务重试关系、尝试次数，以及 PBAS 动态参数 Schema。
ALTER TABLE `training_jobs`
  ADD COLUMN `retry_of_job_id` bigint DEFAULT NULL AFTER `failure_reason`,
  ADD COLUMN `attempt` int NOT NULL DEFAULT '1' AFTER `retry_of_job_id`,
  ADD KEY `idx_training_jobs_retry_of` (`retry_of_job_id`);

UPDATE `algorithm`
SET
  `parameter_schema_json` = JSON_OBJECT(
    'type', 'object',
    'properties', JSON_OBJECT(
      'classes', JSON_OBJECT(
        'type', 'array',
        'title', 'MVTec 类别',
        'items', JSON_OBJECT(
          'type', 'string',
          'enum', JSON_ARRAY(
            'bottle','cable','capsule','carpet','grid','hazelnut','leather',
            'metal_nut','pill','screw','tile','toothbrush','transistor','wood','zipper'
          )
        ),
        'default', JSON_ARRAY('screw')
      ),
      'epochs', JSON_OBJECT('type','integer','title','训练轮数','minimum',1,'maximum',10,'default',5),
      'batch_size', JSON_OBJECT('type','integer','title','批大小','minimum',1,'maximum',64,'default',8),
      'learning_rate', JSON_OBJECT('type','number','title','学习率','exclusiveMinimum',0,'maximum',1,'default',0.0001),
      'num_workers', JSON_OBJECT('type','integer','title','数据线程','minimum',0,'maximum',32,'default',4),
      'resize', JSON_OBJECT('type','integer','title','缩放尺寸','minimum',64,'maximum',1024,'default',288),
      'image_size', JSON_OBJECT('type','integer','title','输入尺寸','minimum',64,'maximum',1024,'default',288),
      'seed', JSON_OBJECT('type','integer','title','随机种子','minimum',0,'maximum',2147483647,'default',0),
      'eval_every', JSON_OBJECT('type','integer','title','评估间隔','minimum',1,'maximum',10,'default',1)
    ),
    'required', JSON_ARRAY('classes','epochs','batch_size','learning_rate')
  ),
  `resource_spec_json` = JSON_OBJECT(
    'gpu_count', 1,
    'min_free_gpu_memory_mb', 8000,
    'gpu_selection', 'AUTO_OR_ALLOWLIST'
  ),
  `dataset_requirement_json` = JSON_OBJECT(
    'dataset_names', JSON_ARRAY('MVTec AD'),
    'read_only', true
  )
WHERE `algorithm_id` = (
  SELECT `id` FROM `algorithms` WHERE UPPER(`abbreviation`) = 'PBAS' LIMIT 1
);
