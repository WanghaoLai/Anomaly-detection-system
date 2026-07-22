from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import create_model, Field
from tortoise.contrib.pydantic import pydantic_model_creator

from common.auth import get_current_user
from common.result import Result, PageInfo
from models import Dataset, DatasetInfo

router = APIRouter(prefix="/dataset", dependencies=[Depends(get_current_user)])

# Dataset 只读模型
DatasetPydantic = pydantic_model_creator(Dataset, name="DatasetPydantic")
# DatasetInfo 只读模型
DatasetInfoPydantic = pydantic_model_creator(DatasetInfo, name="DatasetInfoPydantic")

# 创建用的模型，所有字段 Optional
DatasetCreatePydantic = create_model(
    "DatasetCreatePydantic",
    **{
        name: (Optional[field.annotation], None)
        for name, field in DatasetPydantic.model_fields.items()
    },
    created_by=(Optional[int], Field(None, alias="createdBy")),
)

DatasetInfoCreatePydantic = create_model(
    "DatasetInfoCreatePydantic",
    **{
        name: (Optional[field.annotation], None)
        for name, field in DatasetInfoPydantic.model_fields.items()
    },
    dataset_id=(Optional[int], Field(None, alias="datasetId")),
)


@router.get("/selectPage")
async def select_page(name: str = "", userId: int = 0, pageNum: int = 1, pageSize: int = 5):
    query = Dataset.filter(deleted_at__isnull=True)
    if name and name != '':
        query = query.filter(name__contains=name)

    query = query.prefetch_related('dataset_infos', 'created_by').order_by('-created_at')

    total = await query.count()
    datasets_list = await query.offset((pageNum - 1) * pageSize).limit(pageSize)

    result = []
    for ds in datasets_list:
        info = ds.dataset_infos[0] if ds.dataset_infos else None
        item = {
            **DatasetPydantic.model_validate(ds).model_dump(exclude={'created_by_id'}),
            "created_at": ds.created_at.strftime('%Y-%m-%d %H:%M:%S') if ds.created_at else None,
            "updated_at": ds.updated_at.strftime('%Y-%m-%d %H:%M:%S') if ds.updated_at else None,
            "created_by_name": ds.created_by.username if ds.created_by else None,
            "root_directory": info.root_directory if info else None,
            "info_id": info.id if info else None,
            "class_count": info.class_count if info else 0,
            "train_sample_count": info.train_sample_count if info else 0,
            "test_sample_count": info.test_sample_count if info else 0,
            "anomaly_sample_count": info.anomaly_sample_count if info else 0,
            "mask_count": info.mask_count if info else 0,
        }
        result.append(item)

    pageinfo = PageInfo(total=total, list=result)
    return Result.success(pageinfo)


@router.post("/add")
async def add(dataset_pydantic: DatasetCreatePydantic):
    create_data = dataset_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    dataset = await Dataset.create(**create_data)
    return Result.success(dataset.id)


@router.put("/update")
async def update(dataset_pydantic: DatasetCreatePydantic):
    if not dataset_pydantic.id:
        return Result.error("缺少 id")
    update_data = dataset_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    await Dataset.filter(id=dataset_pydantic.id).update(**update_data)
    return Result.success()


@router.delete("/delete/{id}")
async def delete(id: int):
    await Dataset.filter(id=id).delete()
    return Result.success()


@router.post("/info/add")
async def add_info(info_pydantic: DatasetInfoCreatePydantic):
    create_data = info_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    await DatasetInfo.create(**create_data)
    return Result.success()


@router.put("/info/update")
async def update_info(info_pydantic: DatasetInfoCreatePydantic):
    if not info_pydantic.id:
        return Result.error("缺少 id")
    update_data = info_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    await DatasetInfo.filter(id=info_pydantic.id).update(**update_data)
    return Result.success()


@router.delete("/info/delete/{id}")
async def delete_info(id: int):
    await DatasetInfo.filter(id=id).delete()
    return Result.success()
