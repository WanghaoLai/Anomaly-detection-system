import json
from pathlib import PurePosixPath
from typing import Any, Literal

from common.auth import get_current_admin, get_current_user
from common.exception_handler import CustomException
from common.result import PageInfo, Result
from common.sequential_number import next_sequential_number
from fastapi import APIRouter, Depends, Query
from models import Algorithm, AlgorithmInfo
from pydantic import BaseModel, ConfigDict, Field, field_validator
from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

router = APIRouter(prefix="/algorithm", dependencies=[Depends(get_current_user)])


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class AlgorithmInfoFields(_StrictModel):
    framework: str = Field(min_length=1, max_length=64)
    framework_version: str | None = Field(default=None, max_length=64)
    python_version: str | None = Field(default=None, max_length=32)
    cuda_requirement: str | None = Field(default=None, max_length=64)
    conda_env_name: str = Field(min_length=1, max_length=128)
    conda_env_path: str = Field(min_length=1, max_length=500)
    working_directory: str = Field(min_length=1, max_length=500)
    train_entrypoint: str = Field(min_length=1, max_length=500)
    inference_entrypoint: str | None = Field(default=None, max_length=500)
    executor_type: Literal["GPU"] = "GPU"
    process_manager: Literal["PROCESS_GROUP"] = "PROCESS_GROUP"
    protocol_version: str = Field(default="1.0", min_length=1, max_length=32)
    sse_enabled: bool = True
    parameter_schema_json: dict[str, Any] | None = None
    output_schema_json: dict[str, Any] | None = None
    resource_spec_json: dict[str, Any] | None = None
    dataset_requirement_json: dict[str, Any] | None = None

    @field_validator("conda_env_path", "working_directory")
    @classmethod
    def validate_remote_directory(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("运行目录必须是安全的绝对路径")
        return value


class AlgorithmCreatePydantic(AlgorithmInfoFields):
    """算法目录与唯一运行配置的原子创建请求。"""

    name: str = Field(min_length=1, max_length=255)
    abbreviation: str | None = Field(default=None, max_length=64)
    description: str | None = None
    task_category: Literal["ANOMALY_DETECTION"] = "ANOMALY_DETECTION"


class AlgorithmUpdatePydantic(AlgorithmCreatePydantic):
    id: int = Field(gt=0)


class AlgorithmInfoCreatePydantic(AlgorithmInfoFields):
    """兼容历史缺失详情记录的修复入口。"""

    algorithm_id: int = Field(alias="algorithmId", gt=0)


class AlgorithmInfoUpdatePydantic(AlgorithmInfoFields):
    id: int = Field(gt=0)


ALGORITHM_INFO_FIELDS = {
    "framework", "framework_version", "python_version", "cuda_requirement",
    "conda_env_name", "conda_env_path", "working_directory",
    "train_entrypoint", "inference_entrypoint", "executor_type",
    "process_manager", "protocol_version", "sse_enabled",
    "parameter_schema_json", "output_schema_json", "resource_spec_json",
    "dataset_requirement_json",
}


def _algorithm_info_data(payload: AlgorithmInfoFields) -> dict:
    return payload.model_dump(include=ALGORITHM_INFO_FIELDS)


def _serialize_json_field(value):
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value


@router.get("/selectPage")
async def select_page(
    name: str = "",
    userId: int = 0,
    pageNum: int = Query(1, ge=1),
    pageSize: int = Query(5, ge=1, le=100),
):
    query = Algorithm.filter(deleted_at__isnull=True)
    if name:
        query = query.filter(name__contains=name)
    query = query.prefetch_related("algorithm_info", "created_by").order_by("id")

    total = await query.count()
    algorithms = await query.offset((pageNum - 1) * pageSize).limit(pageSize)
    result = []
    for algorithm in algorithms:
        info = algorithm.algorithm_info
        result.append({
            "id": algorithm.id,
            "algorithm_no": algorithm.algorithm_no,
            "name": algorithm.name,
            "abbreviation": algorithm.abbreviation,
            "description": algorithm.description,
            "task_category": algorithm.task_category,
            "created_at": algorithm.created_at.strftime("%Y-%m-%d %H:%M:%S") if algorithm.created_at else None,
            "updated_at": algorithm.updated_at.strftime("%Y-%m-%d %H:%M:%S") if algorithm.updated_at else None,
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
            "created_by_name": algorithm.created_by.username if algorithm.created_by else None,
        })
    return Result.success(PageInfo(total=total, list=result))


@router.post("/add", dependencies=[Depends(get_current_admin)])
async def add(
    payload: AlgorithmCreatePydantic,
    current_admin: dict = Depends(get_current_admin),
):
    main_data = payload.model_dump(
        include={"name", "abbreviation", "description", "task_category"}
    )
    for _ in range(3):
        try:
            async with in_transaction() as connection:
                main_data["algorithm_no"] = str(
                    await next_sequential_number(
                        Algorithm, "algorithm_no", connection
                    )
                )
                algorithm = await Algorithm.create(
                    using_db=connection,
                    created_by_id=current_admin["user_id"],
                    **main_data,
                )
                await AlgorithmInfo.create(
                    using_db=connection,
                    algorithm_id=algorithm.id,
                    **_algorithm_info_data(payload),
                )
            return Result.success(algorithm.id)
        except IntegrityError:
            continue
    raise CustomException("算法编号分配冲突，请重试", status_code=409)


@router.put("/update", dependencies=[Depends(get_current_admin)])
async def update(payload: AlgorithmUpdatePydantic):
    async with in_transaction() as connection:
        algorithm = await Algorithm.filter(id=payload.id).using_db(
            connection
        ).select_for_update().first()
        if algorithm is None:
            raise CustomException("算法不存在", status_code=404)
        await Algorithm.filter(id=payload.id).using_db(connection).update(
            **payload.model_dump(
                include={"name", "abbreviation", "description", "task_category"}
            )
        )
        info = await AlgorithmInfo.filter(algorithm_id=payload.id).using_db(
            connection
        ).select_for_update().first()
        info_data = _algorithm_info_data(payload)
        if info is None:
            await AlgorithmInfo.create(
                using_db=connection,
                algorithm_id=payload.id,
                **info_data,
            )
        else:
            await AlgorithmInfo.filter(id=info.id).using_db(connection).update(
                **info_data
            )
    return Result.success()


@router.delete("/delete/{id}", dependencies=[Depends(get_current_admin)])
async def delete(id: int):
    try:
        async with in_transaction() as connection:
            await AlgorithmInfo.filter(algorithm_id=id).using_db(connection).delete()
            deleted = await Algorithm.filter(id=id).using_db(connection).delete()
            if deleted != 1:
                raise CustomException("算法不存在", status_code=404)
    except IntegrityError as exc:
        raise CustomException(
            "该算法仍被训练任务引用，请先处理相关任务",
            status_code=409,
        ) from exc
    return Result.success()


@router.post("/info/add", dependencies=[Depends(get_current_admin)])
async def add_info(payload: AlgorithmInfoCreatePydantic):
    if not await Algorithm.filter(id=payload.algorithm_id).exists():
        raise CustomException("算法不存在", status_code=404)
    try:
        await AlgorithmInfo.create(
            algorithm_id=payload.algorithm_id,
            **_algorithm_info_data(payload),
        )
    except IntegrityError as exc:
        raise CustomException("算法运行配置已存在", status_code=409) from exc
    return Result.success()


@router.put("/info/update", dependencies=[Depends(get_current_admin)])
async def update_info(payload: AlgorithmInfoUpdatePydantic):
    updated = await AlgorithmInfo.filter(id=payload.id).update(
        **_algorithm_info_data(payload)
    )
    if updated != 1:
        raise CustomException("算法运行配置不存在", status_code=404)
    return Result.success()
