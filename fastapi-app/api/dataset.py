from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import create_model, Field
from tortoise.transactions import in_transaction
from tortoise.contrib.pydantic import pydantic_model_creator
from tortoise.exceptions import IntegrityError

from common.auth import get_current_admin, get_current_user
from common.exception_handler import CustomException
from common.result import Result, PageInfo
from common.sequential_number import next_sequential_number
from models import Dataset, DatasetInfo

router = APIRouter(prefix="/dataset", dependencies=[Depends(get_current_user)])

# Dataset 只读模型
DatasetPydantic = pydantic_model_creator(Dataset, name="DatasetPydantic")
# DatasetInfo 只读模型
DatasetInfoPydantic = pydantic_model_creator(DatasetInfo, name="DatasetInfoPydantic")

# 创建用的模型，所有字段 Optional。
# 同 algorithm.py：FK 字段仅在 Tortoise 初始化后才进入 model_fields，
# 推导时排除与显式声明同名的项，消除导入顺序依赖。
DatasetCreatePydantic = create_model(
    "DatasetCreatePydantic",
    **{
        name: (Optional[field.annotation], None)
        for name, field in DatasetPydantic.model_fields.items()
        if name != "created_by"
    },
    created_by=(Optional[int], Field(None, alias="createdBy")),
)

DatasetInfoCreatePydantic = create_model(
    "DatasetInfoCreatePydantic",
    **{
        name: (Optional[field.annotation], None)
        for name, field in DatasetInfoPydantic.model_fields.items()
        if name != "dataset_id"
    },
    dataset_id=(Optional[int], Field(None, alias="datasetId")),
)


@router.get("/selectPage")
async def select_page(
    name: str = "",
    userId: int = 0,
    pageNum: int = Query(1, ge=1),
    pageSize: int = Query(5, ge=1, le=100),
):
    query = Dataset.filter(deleted_at__isnull=True)
    if name and name != '':
        query = query.filter(name__contains=name)

    query = query.prefetch_related('dataset_infos', 'created_by').order_by('id')

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
        }
        result.append(item)

    pageinfo = PageInfo(total=total, list=result)
    return Result.success(pageinfo)


@router.post("/add", dependencies=[Depends(get_current_admin)])
async def add(
    dataset_pydantic: DatasetCreatePydantic,
    current_admin: dict = Depends(get_current_admin),
):
    create_data = dataset_pydantic.model_dump(
        exclude_unset=True,
        exclude={
            'id', 'dataset_no', 'created_by', 'created_at', 'updated_at',
            'deleted_at',
        },
    )
    create_data['created_by_id'] = current_admin['user_id']
    # 编号 max+1 单调递增；并发分配冲突由唯一索引兜底并重试。
    for _ in range(3):
        try:
            async with in_transaction() as connection:
                create_data['dataset_no'] = str(
                    await next_sequential_number(
                        Dataset, 'dataset_no', connection
                    )
                )
                dataset = await Dataset.create(using_db=connection, **create_data)
            return Result.success(dataset.id)
        except IntegrityError:
            continue
    raise CustomException("数据集编号分配冲突，请重试")


@router.put("/update", dependencies=[Depends(get_current_admin)])
async def update(dataset_pydantic: DatasetCreatePydantic):
    if not dataset_pydantic.id:
        return Result.error("缺少 id")
    update_data = dataset_pydantic.model_dump(
        exclude_unset=True,
        exclude={
            'id', 'dataset_no', 'created_by', 'created_at', 'updated_at',
            'deleted_at',
        },
    )
    await Dataset.filter(id=dataset_pydantic.id).update(**update_data)
    return Result.success()


@router.delete("/delete/{id}", dependencies=[Depends(get_current_admin)])
async def delete(id: int):
    try:
        async with in_transaction() as connection:
            await DatasetInfo.filter(dataset_id=id).using_db(connection).delete()
            await Dataset.filter(id=id).using_db(connection).delete()
    except IntegrityError:
        # 训练任务对数据集是 RESTRICT 外键：被引用时拒绝删除而不是报系统错误。
        raise CustomException("该数据集仍被训练任务引用，请先处理相关任务")
    return Result.success()


@router.post("/info/add", dependencies=[Depends(get_current_admin)])
async def add_info(info_pydantic: DatasetInfoCreatePydantic):
    create_data = info_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    await DatasetInfo.create(**create_data)
    return Result.success()


@router.put("/info/update", dependencies=[Depends(get_current_admin)])
async def update_info(info_pydantic: DatasetInfoCreatePydantic):
    if not info_pydantic.id:
        return Result.error("缺少 id")
    update_data = info_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    await DatasetInfo.filter(id=info_pydantic.id).update(**update_data)
    return Result.success()
