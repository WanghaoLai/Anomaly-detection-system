import json
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import create_model, Field
from tortoise.contrib.pydantic import pydantic_model_creator

from common.auth import get_current_user
from common.result import Result, PageInfo
from models import Algorithm, AlgorithmInfo

router = APIRouter(prefix="/algorithm", dependencies=[Depends(get_current_user)])

# Algorithm 只读模型
AlgorithmPydantic = pydantic_model_creator(Algorithm, name="AlgorithmPydantic")
# AlgorithmInfo 只读模型
AlgorithmInfoPydantic = pydantic_model_creator(AlgorithmInfo, name="AlgorithmInfoPydantic")

# 创建用的模型，所有字段 Optional
AlgorithmCreatePydantic = create_model(
    "AlgorithmCreatePydantic",
    **{
        name: (Optional[field.annotation], None)
        for name, field in AlgorithmPydantic.model_fields.items()
    },
    created_by=(Optional[int], Field(None, alias="createdBy")),
)

AlgorithmInfoCreatePydantic = create_model(
    "AlgorithmInfoCreatePydantic",
    **{
        name: (Optional[field.annotation], None)
        for name, field in AlgorithmInfoPydantic.model_fields.items()
    },
    algorithm_id=(Optional[int], Field(None, alias="algorithmId")),
)


def _serialize_json_field(val):
    if val is None:
        return None
    return json.dumps(val, ensure_ascii=False) if isinstance(val, (dict, list)) else val


@router.get("/selectPage")
async def select_page(name: str = "", userId: int = 0, pageNum: int = 1, pageSize: int = 5):
    query = Algorithm.filter(deleted_at__isnull=True)
    if name and name != '':
        query = query.filter(name__contains=name)

    query = query.prefetch_related('algorithm_infos', 'created_by').order_by('-created_at')

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
            "framework_version": info.framework_version if info else None,
            "python_version": info.python_version if info else None,
            "cuda_requirement": info.cuda_requirement if info else None,
            "train_entrypoint": info.train_entrypoint if info else None,
            "inference_entrypoint": info.inference_entrypoint if info else None,
            "docker_image": info.docker_image if info else None,
            "docker_image_digest": info.docker_image_digest if info else None,
            "parameter_schema_json": _serialize_json_field(info.parameter_schema_json) if info else None,
            "output_schema_json": _serialize_json_field(info.output_schema_json) if info else None,
            "resource_spec_json": _serialize_json_field(info.resource_spec_json) if info else None,
            "dataset_requirement_json": _serialize_json_field(info.dataset_requirement_json) if info else None,
            "created_by_name": algo.created_by.username if algo.created_by else None,
        }
        result.append(item)

    pageinfo = PageInfo(total=total, list=result)
    return Result.success(pageinfo)


@router.post("/add")
async def add(algorithm_pydantic: AlgorithmCreatePydantic):
    create_data = algorithm_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    await Algorithm.create(**create_data)
    return Result.success()


@router.put("/update")
async def update(algorithm_pydantic: AlgorithmCreatePydantic):
    if not algorithm_pydantic.id:
        return Result.error("缺少 id")
    update_data = algorithm_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    await Algorithm.filter(id=algorithm_pydantic.id).update(**update_data)
    return Result.success()


@router.delete("/delete/{id}")
async def delete(id: int):
    await Algorithm.filter(id=id).delete()
    return Result.success()


@router.post("/info/add")
async def add_info(info_pydantic: AlgorithmInfoCreatePydantic):
    create_data = info_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    await AlgorithmInfo.create(**create_data)
    return Result.success()


@router.put("/info/update")
async def update_info(info_pydantic: AlgorithmInfoCreatePydantic):
    if not info_pydantic.id:
        return Result.error("缺少 id")
    update_data = info_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    await AlgorithmInfo.filter(id=info_pydantic.id).update(**update_data)
    return Result.success()


@router.delete("/info/delete/{id}")
async def delete_info(id: int):
    await AlgorithmInfo.filter(id=id).delete()
    return Result.success()
