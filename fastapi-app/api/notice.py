from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import create_model
from tortoise.contrib.pydantic import pydantic_model_creator

from common.auth import get_current_admin, get_current_user
from common.result import Result, PageInfo
from models import Notice

router = APIRouter(prefix="/notice", dependencies=[Depends(get_current_user)])

# 创建 pydantic 只读模型 把数据库模型转化成pydantic模型
NoticePydantic = pydantic_model_creator(Notice)
# 自动生成所有字段为 Optional 的更新模型
NoticeCreatePydantic = create_model(
    "NoticePydantic",
    **{
        # 从只读模型中读取所有字段然后给它设置成可选
        name: (Optional[field.annotation], None)
        for name, field in NoticePydantic.model_fields.items()
    }
)


@router.post("/add", dependencies=[Depends(get_current_admin)])
async def add(notice_pydantic: NoticeCreatePydantic):
    create_data = notice_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    create_data['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    await Notice.create(**create_data)
    return Result.success()


@router.put("/update", dependencies=[Depends(get_current_admin)])
async def update(notice_pydantic: NoticeCreatePydantic):
    if not notice_pydantic.id:
        return Result.error("缺少 id")
    update_data = notice_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    await Notice.filter(id=notice_pydantic.id).update(**update_data)
    return Result.success()


@router.delete("/delete/{notice_id}", dependencies=[Depends(get_current_admin)])
async def delete(notice_id: int):
    await Notice.filter(id=notice_id).delete()
    return Result.success()


# 查询所有
@router.get("/selectAll")
async def select_all(name: str = ""):
    notice_list = await Notice.filter(name__contains=name)
    return Result.success(notice_list)


@router.get("/selectPage", dependencies=[Depends(get_current_admin)])
async def select(
    name: str = "",
    page_num: int = Query(1, ge=1),
    page_size: int = Query(5, ge=1, le=100),
):
    # 同时获取分页数据和总数
    query = Notice.filter(name__contains=name)
    # 获取分页数据
    notice_list = await query.order_by("-id").offset((page_num - 1) * page_size).limit(page_size)
    notice_list = [
        # 遍历每个 Notice 实例（ORM实例），通过 Pydantic 模型，转为字典
        NoticePydantic.model_validate(notice).model_dump()
        for notice in notice_list
    ]
    # 计算总数
    total = await query.count()
    # 封装分页数据
    pageinfo = PageInfo(total=total, list=notice_list)
    return Result.success(pageinfo)
