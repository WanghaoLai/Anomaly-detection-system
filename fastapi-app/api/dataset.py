from common.auth import get_current_admin, get_current_user
from common.exception_handler import CustomException
from common.result import PageInfo, Result
from common.sequential_number import next_sequential_number
from fastapi import APIRouter, Depends, Query
from models import Dataset, DatasetInfo
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator
from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

router = APIRouter(prefix="/dataset", dependencies=[Depends(get_current_user)])


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class DatasetInfoFields(_StrictModel):
    root_directory: str | None = Field(default=None, max_length=500)
    class_count: int = Field(default=0, ge=0)
    train_sample_count: int = Field(default=0, ge=0)
    test_sample_count: int = Field(default=0, ge=0)
    anomaly_sample_count: int = Field(default=0, ge=0)

    @field_validator("root_directory")
    @classmethod
    def validate_root_directory(cls, value: str | None) -> str | None:
        if value is None:
            return value
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("数据源目录必须是安全的绝对路径")
        return value


class DatasetCreatePydantic(DatasetInfoFields):
    """数据集主记录与唯一详情的原子创建请求。"""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    domain_type: str | None = Field(default=None, max_length=64)
    root_directory: str = Field(min_length=1, max_length=500)


class DatasetUpdatePydantic(DatasetCreatePydantic):
    id: int = Field(gt=0)


class DatasetInfoCreatePydantic(DatasetInfoFields):
    """兼容历史缺失详情记录的修复入口；唯一约束仍由数据库裁决。"""

    dataset_id: int = Field(alias="datasetId", gt=0)
    root_directory: str = Field(min_length=1, max_length=500)


class DatasetInfoUpdatePydantic(DatasetInfoFields):
    id: int = Field(gt=0)
    root_directory: str = Field(min_length=1, max_length=500)


def _dataset_info_data(payload: DatasetInfoFields) -> dict:
    return payload.model_dump(
        include={
            "root_directory",
            "class_count",
            "train_sample_count",
            "test_sample_count",
            "anomaly_sample_count",
        }
    )


@router.get("/selectPage")
async def select_page(
    name: str = "",
    userId: int = 0,
    pageNum: int = Query(1, ge=1),
    pageSize: int = Query(5, ge=1, le=100),
):
    query = Dataset.filter(deleted_at__isnull=True)
    if name:
        query = query.filter(name__contains=name)
    query = query.prefetch_related("dataset_info", "created_by").order_by("id")

    total = await query.count()
    datasets = await query.offset((pageNum - 1) * pageSize).limit(pageSize)
    result = []
    for dataset in datasets:
        info = dataset.dataset_info
        result.append({
            "id": dataset.id,
            "dataset_no": dataset.dataset_no,
            "name": dataset.name,
            "description": dataset.description,
            "domain_type": dataset.domain_type,
            "created_at": (
                dataset.created_at.strftime("%Y-%m-%d %H:%M:%S")
                if dataset.created_at else None
            ),
            "updated_at": (
                dataset.updated_at.strftime("%Y-%m-%d %H:%M:%S")
                if dataset.updated_at else None
            ),
            "created_by_name": (
                dataset.created_by.username if dataset.created_by else None
            ),
            "root_directory": info.root_directory if info else None,
            "info_id": info.id if info else None,
            "class_count": info.class_count if info else 0,
            "train_sample_count": info.train_sample_count if info else 0,
            "test_sample_count": info.test_sample_count if info else 0,
            "anomaly_sample_count": info.anomaly_sample_count if info else 0,
        })
    return Result.success(PageInfo(total=total, list=result))


@router.post("/add", dependencies=[Depends(get_current_admin)])
async def add(
    payload: DatasetCreatePydantic,
    current_admin: dict = Depends(get_current_admin),
):
    main_data = payload.model_dump(
        include={"name", "description", "domain_type"}
    )
    for _ in range(3):
        try:
            async with in_transaction() as connection:
                main_data["dataset_no"] = str(
                    await next_sequential_number(
                        Dataset, "dataset_no", connection
                    )
                )
                dataset = await Dataset.create(
                    using_db=connection,
                    created_by_id=current_admin["user_id"],
                    **main_data,
                )
                await DatasetInfo.create(
                    using_db=connection,
                    dataset_id=dataset.id,
                    **_dataset_info_data(payload),
                )
            return Result.success(dataset.id)
        except IntegrityError:
            continue
    raise CustomException("数据集编号分配冲突，请重试", status_code=409)


@router.put("/update", dependencies=[Depends(get_current_admin)])
async def update(payload: DatasetUpdatePydantic):
    async with in_transaction() as connection:
        dataset = await Dataset.filter(id=payload.id).using_db(
            connection
        ).select_for_update().first()
        if dataset is None:
            raise CustomException("数据集不存在", status_code=404)
        await Dataset.filter(id=payload.id).using_db(connection).update(
            **payload.model_dump(
                include={"name", "description", "domain_type"}
            )
        )
        info = await DatasetInfo.filter(dataset_id=payload.id).using_db(
            connection
        ).select_for_update().first()
        info_data = _dataset_info_data(payload)
        if info is None:
            await DatasetInfo.create(
                using_db=connection,
                dataset_id=payload.id,
                **info_data,
            )
        else:
            await DatasetInfo.filter(id=info.id).using_db(connection).update(
                **info_data
            )
    return Result.success()


@router.delete("/delete/{id}", dependencies=[Depends(get_current_admin)])
async def delete(id: int):
    try:
        async with in_transaction() as connection:
            await DatasetInfo.filter(dataset_id=id).using_db(connection).delete()
            deleted = await Dataset.filter(id=id).using_db(connection).delete()
            if deleted != 1:
                raise CustomException("数据集不存在", status_code=404)
    except IntegrityError as exc:
        raise CustomException(
            "该数据集仍被训练任务引用，请先处理相关任务",
            status_code=409,
        ) from exc
    return Result.success()


@router.post("/info/add", dependencies=[Depends(get_current_admin)])
async def add_info(payload: DatasetInfoCreatePydantic):
    if not await Dataset.filter(id=payload.dataset_id).exists():
        raise CustomException("数据集不存在", status_code=404)
    try:
        await DatasetInfo.create(
            dataset_id=payload.dataset_id,
            **_dataset_info_data(payload),
        )
    except IntegrityError as exc:
        raise CustomException("数据集详情已存在", status_code=409) from exc
    return Result.success()


@router.put("/info/update", dependencies=[Depends(get_current_admin)])
async def update_info(payload: DatasetInfoUpdatePydantic):
    updated = await DatasetInfo.filter(id=payload.id).update(
        **_dataset_info_data(payload)
    )
    if updated != 1:
        raise CustomException("数据集详情不存在", status_code=404)
    return Result.success()
