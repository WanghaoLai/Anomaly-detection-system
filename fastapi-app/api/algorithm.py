import json
from typing import Optional, Any

from fastapi import APIRouter, Depends, Query
from pydantic import create_model, Field
from tortoise.transactions import in_transaction
from tortoise.contrib.pydantic import pydantic_model_creator
from tortoise.exceptions import IntegrityError

from common.auth import get_current_admin, get_current_user
from common.exception_handler import CustomException
from common.result import Result, PageInfo
from common.sequential_number import next_sequential_number
from models import Algorithm, AlgorithmInfo

router = APIRouter(prefix="/algorithm", dependencies=[Depends(get_current_user)])

# Algorithm 只读模型
AlgorithmPydantic = pydantic_model_creator(Algorithm, name="AlgorithmPydantic")
# AlgorithmInfo 只读模型
AlgorithmInfoPydantic = pydantic_model_creator(AlgorithmInfo, name="AlgorithmInfoPydantic")

# 创建用的模型，所有字段 Optional。
# Tortoise 初始化后 FK 字段会出现在 model_fields 中，与下方显式声明的
# 同名参数冲突导致 create_model 抛 TypeError；推导时必须排除同名项，
# 保证"api 先于或后于 ORM 初始化导入"两种顺序行为一致。
AlgorithmCreatePydantic = create_model(
    "AlgorithmCreatePydantic",
    **{
        name: (Optional[field.annotation], None)
        for name, field in AlgorithmPydantic.model_fields.items()
        if name != "created_by"
    },
    created_by=(Optional[int], Field(None, alias="createdBy")),
)

JSON_FIELD_NAMES = {'parameter_schema_json', 'output_schema_json', 'resource_spec_json', 'dataset_requirement_json'}

AlgorithmInfoCreatePydantic = create_model(
    "AlgorithmInfoCreatePydantic",
    **{
        name: (Optional[Any], None) if name in JSON_FIELD_NAMES else (Optional[field.annotation], None)
        for name, field in AlgorithmInfoPydantic.model_fields.items()
        if name != "algorithm_id"
    },
    algorithm_id=(Optional[int], Field(None, alias="algorithmId")),
)


def _serialize_json_field(val):
    if val is None:
        return None
    return json.dumps(val, ensure_ascii=False) if isinstance(val, (dict, list)) else val


@router.get("/selectPage")
async def select_page(
    name: str = "",
    userId: int = 0,
    pageNum: int = Query(1, ge=1),
    pageSize: int = Query(5, ge=1, le=100),
):
    query = Algorithm.filter(deleted_at__isnull=True)
    if name and name != '':
        query = query.filter(name__contains=name)

    query = query.prefetch_related('algorithm_infos', 'created_by').order_by('id')

    total = await query.count()
    algorithms_list = await query.offset((pageNum - 1) * pageSize).limit(pageSize)

    result = []
    for algo in algorithms_list:
        info = algo.algorithm_infos[0] if algo.algorithm_infos else None
        item = {
            **AlgorithmPydantic.model_validate(algo).model_dump(exclude={'created_by_id'}),
            "created_at": algo.created_at.strftime('%Y-%m-%d %H:%M:%S') if algo.created_at else None,
            "updated_at": algo.updated_at.strftime('%Y-%m-%d %H:%M:%S') if algo.updated_at else None,
            "framework": info.framework if info else None,
            "info_id": info.id if info else None,
            "framework_version": info.framework_version if info else None,
            "python_version": info.python_version if info else None,
            "cuda_requirement": info.cuda_requirement if info else None,
            "conda_env_name": info.conda_env_name if info else None,
            "conda_env_path": info.conda_env_path if info else None,
            "working_directory": info.working_directory if info else None,
            "train_entrypoint": info.train_entrypoint if info else None,
            "inference_entrypoint": info.inference_entrypoint if info else None,
            "executor_type": info.executor_type if info else None,
            "process_manager": info.process_manager if info else None,
            "protocol_version": info.protocol_version if info else None,
            "sse_enabled": info.sse_enabled if info else False,
            "parameter_schema_json": _serialize_json_field(info.parameter_schema_json) if info else None,
            "output_schema_json": _serialize_json_field(info.output_schema_json) if info else None,
            "resource_spec_json": _serialize_json_field(info.resource_spec_json) if info else None,
            "dataset_requirement_json": _serialize_json_field(info.dataset_requirement_json) if info else None,
            "created_by_name": algo.created_by.username if algo.created_by else None,
        }
        result.append(item)

    pageinfo = PageInfo(total=total, list=result)
    return Result.success(pageinfo)


@router.post("/add", dependencies=[Depends(get_current_admin)])
async def add(
    algorithm_pydantic: AlgorithmCreatePydantic,
    current_admin: dict = Depends(get_current_admin),
):
    create_data = algorithm_pydantic.model_dump(
        exclude_unset=True,
        exclude={
            'id', 'algorithm_no', 'created_by', 'created_at', 'updated_at',
            'deleted_at',
        },
    )
    create_data['created_by_id'] = current_admin['user_id']
    # 编号 max+1 单调递增；并发分配冲突由唯一索引兜底并重试。
    for _ in range(3):
        try:
            async with in_transaction() as connection:
                create_data['algorithm_no'] = str(
                    await next_sequential_number(
                        Algorithm, 'algorithm_no', connection
                    )
                )
                algorithm = await Algorithm.create(
                    using_db=connection, **create_data
                )
            return Result.success(algorithm.id)
        except IntegrityError:
            continue
    raise CustomException("算法编号分配冲突，请重试")


@router.put("/update", dependencies=[Depends(get_current_admin)])
async def update(algorithm_pydantic: AlgorithmCreatePydantic):
    if not algorithm_pydantic.id:
        return Result.error("缺少 id")
    update_data = algorithm_pydantic.model_dump(
        exclude_unset=True,
        exclude={
            'id', 'algorithm_no', 'created_by', 'created_at', 'updated_at',
            'deleted_at',
        },
    )
    await Algorithm.filter(id=algorithm_pydantic.id).update(**update_data)
    return Result.success()


@router.delete("/delete/{id}", dependencies=[Depends(get_current_admin)])
async def delete(id: int):
    try:
        async with in_transaction() as connection:
            await AlgorithmInfo.filter(algorithm_id=id).using_db(connection).delete()
            await Algorithm.filter(id=id).using_db(connection).delete()
    except IntegrityError:
        # 训练任务对算法是 RESTRICT 外键：被引用时拒绝删除而不是报系统错误。
        raise CustomException("该算法仍被训练任务引用，请先处理相关任务")
    return Result.success()


@router.post("/info/add", dependencies=[Depends(get_current_admin)])
async def add_info(info_pydantic: AlgorithmInfoCreatePydantic):
    create_data = info_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    await AlgorithmInfo.create(**create_data)
    return Result.success()


@router.put("/info/update", dependencies=[Depends(get_current_admin)])
async def update_info(info_pydantic: AlgorithmInfoCreatePydantic):
    if not info_pydantic.id:
        return Result.error("缺少 id")
    update_data = info_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    await AlgorithmInfo.filter(id=info_pydantic.id).update(**update_data)
    return Result.success()
