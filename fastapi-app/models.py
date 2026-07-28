from tortoise.models import Model
from tortoise import fields

# 创建Admin的Model
class Admin(Model):
    id = fields.IntField(pk=True, null=False)
    username = fields.CharField(max_length=255, null=True)
    password = fields.CharField(max_length=255, null=True)
    name = fields.CharField(max_length=255, null=True)
    avatar = fields.CharField(max_length=255, null=True)
    role = fields.CharField(max_length=255, null=True)
    token_version = fields.IntField(default=0, null=False)

    class Meta:
        table = 'admin'


# 创建User的Model
class User(Model):
    id = fields.IntField(pk=True, null=False)
    username = fields.CharField(max_length=255, null=True)
    password = fields.CharField(max_length=255, null=True)
    name = fields.CharField(max_length=255, null=True)
    avatar = fields.CharField(max_length=255, null=True)
    role = fields.CharField(max_length=255, null=True)
    token_version = fields.IntField(default=0, null=False)

    class Meta:
        table = 'user'


class AuthSession(Model):
    id = fields.CharField(max_length=36, pk=True)
    user_id = fields.IntField(index=True)
    role = fields.CharField(max_length=20, index=True)
    refresh_jti = fields.CharField(max_length=64, unique=True)
    expires_at = fields.DatetimeField(index=True)
    revoked_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'auth_session'


class LoginThrottle(Model):
    key = fields.CharField(max_length=64, pk=True)
    failures = fields.IntField(default=0)
    window_started = fields.DatetimeField()
    locked_until = fields.DatetimeField(null=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'login_throttle'


class Address(Model):
    id = fields.IntField(pk=True, null=False)
    user = fields.ForeignKeyField('models.User', null=True)
    name = fields.CharField(max_length=255, null=True)
    address = fields.CharField(max_length=255, null=True)
    phone = fields.CharField(max_length=255, null=True)

    class Meta:
        table = 'address'

class Notice(Model):
    id = fields.IntField(pk=True, null=False)
    name = fields.CharField(max_length=255, null=True)
    content = fields.CharField(max_length=255, null=True)
    time = fields.CharField(max_length=255, null=True)

    class Meta:
        table = 'notice'


class Conversation(Model):
    id = fields.IntField(pk=True, null=False)
    user = fields.ForeignKeyField('models.User', related_name='conversations')
    title = fields.CharField(max_length=255, default='新对话')
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'conversation'


class Message(Model):
    id = fields.IntField(pk=True, null=False)
    conversation = fields.ForeignKeyField('models.Conversation', related_name='messages')
    role = fields.CharField(max_length=20)  # 'user' 或 'assistant'
    content = fields.TextField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'message'


class Knowledge(Model):
    id = fields.IntField(pk=True, null=False)
    filename = fields.CharField(max_length=255, null=True)
    original_name = fields.CharField(max_length=255, null=True)
    file_size = fields.IntField(null=True)
    chunk_count = fields.IntField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'knowledge'


class Dataset(Model):
    id = fields.IntField(pk=True, null=False)
    dataset_no = fields.CharField(max_length=26, null=False, unique=True, description='数据集编号')
    name = fields.CharField(max_length=255, null=False, index=True, description='数据集名称')
    description = fields.TextField(null=True, description='数据集描述')
    domain_type = fields.CharField(max_length=64, null=True, description='数据集领域类型')
    created_by = fields.ForeignKeyField('models.Admin', null=False, related_name='datasets', source_field='created_by', description='创建人')
    created_at = fields.DatetimeField(auto_now_add=True, description='创建时间')
    updated_at = fields.DatetimeField(auto_now=True, description='更新时间')
    deleted_at = fields.DatetimeField(null=True, description='软删除标记')

    class Meta:
        table = 'datasets'


class DatasetInfo(Model):
    id = fields.IntField(pk=True, null=False)
    dataset = fields.ForeignKeyField('models.Dataset', null=False, related_name='dataset_infos', description='关联所属数据集')
    root_directory = fields.CharField(max_length=500, null=True, description='数据根目录路径')
    class_count = fields.IntField(default=0, null=False, description='类别数量')
    train_sample_count = fields.IntField(default=0, null=False, description='训练集样本数')
    test_sample_count = fields.IntField(default=0, null=False, description='测试集样本数')
    anomaly_sample_count = fields.IntField(default=0, null=False, description='异常样本数')
    mask_count = fields.IntField(default=0, null=False, description='掩码（标注）数量')

    class Meta:
        table = 'dataset'


class Algorithm(Model):
    id = fields.IntField(pk=True, null=False)
    algorithm_no = fields.CharField(max_length=26, null=False, unique=True, description='算法编号')
    name = fields.CharField(max_length=255, null=False, index=True, description='算法名称')
    abbreviation = fields.CharField(max_length=64, null=True, description='算法简称/缩写')
    description = fields.TextField(null=True, description='算法描述')
    task_category = fields.CharField(max_length=64, null=False, default='ANOMALY_DETECTION', description='任务类别')
    created_by = fields.ForeignKeyField('models.Admin', null=False, related_name='algorithms', source_field='created_by', description='创建人')
    created_at = fields.DatetimeField(auto_now_add=True, description='创建时间')
    updated_at = fields.DatetimeField(auto_now=True, description='更新时间')
    deleted_at = fields.DatetimeField(null=True, description='软删除标记')

    class Meta:
        table = 'algorithms'


class AlgorithmInfo(Model):
    id = fields.IntField(pk=True, null=False)
    algorithm = fields.ForeignKeyField('models.Algorithm', null=False, related_name='algorithm_infos', source_field='algorithm_id', description='关联所属算法')
    framework = fields.CharField(max_length=64, null=False, description='所使用的框架')
    framework_version = fields.CharField(max_length=64, null=True, description='框架版本号')
    python_version = fields.CharField(max_length=32, null=True, description='Python 版本要求')
    cuda_requirement = fields.CharField(max_length=64, null=True, description='CUDA 版本要求')
    conda_env_name = fields.CharField(max_length=128, null=True, description='Conda 独立环境名称')
    conda_env_path = fields.CharField(max_length=500, null=True, description='Conda 环境绝对路径')
    working_directory = fields.CharField(max_length=500, null=True, description='算法运行工作目录')
    train_entrypoint = fields.CharField(max_length=500, null=False, description='训练入口脚本路径')
    inference_entrypoint = fields.CharField(max_length=500, null=True, description='推理入口脚本路径')
    executor_type = fields.CharField(max_length=32, null=False, default='GPU', description='执行器类型')
    process_manager = fields.CharField(max_length=32, null=False, default='SYSTEMD', description='任务进程管理方式')
    protocol_version = fields.CharField(max_length=32, null=False, default='1.0', description='JSONL 训练协议版本')
    sse_enabled = fields.BooleanField(null=False, default=True, description='是否支持 SSE 实时推送')
    parameter_schema_json = fields.JSONField(null=True, description='参数结构定义')
    output_schema_json = fields.JSONField(null=True, description='输出结构定义')
    resource_spec_json = fields.JSONField(null=True, description='资源需求规格')
    dataset_requirement_json = fields.JSONField(null=True, description='数据集要求定义')

    class Meta:
        table = 'algorithm'
