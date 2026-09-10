from datetime import datetime

from common.auth import get_current_admin, get_current_user
from common.exception_handler import CustomException
from common.result import PageInfo, Result
from fastapi import APIRouter, Depends, Query
from models import Notice
from pydantic import BaseModel, ConfigDict, Field, model_validator
from tortoise.contrib.pydantic import pydantic_model_creator

router = APIRouter(prefix="/notice", dependencies=[Depends(get_current_user)])

# 创建 pydantic 只读模型 把数据库模型转化成pydantic模型
NoticePydantic = pydantic_model_creator(Notice)
class _StrictNoticeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NoticeCreatePydantic(_StrictNoticeModel):
    name: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=255)


class NoticeUpdatePydantic(_StrictNoticeModel):
    id: int = Field(gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def require_change(self):
        if not (self.model_fields_set - {"id"}):
            raise ValueError("至少提供一个需要更新的字段")
        return self


@router.post("/add", dependencies=[Depends(get_current_admin)])
async def add(notice_pydantic: NoticeCreatePydantic):
    create_data = notice_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    create_data['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    await Notice.create(**create_data)
    return Result.success()


@router.put("/update", dependencies=[Depends(get_current_admin)])
async def update(notice_pydantic: NoticeUpdatePydantic):
    update_data = notice_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    updated = await Notice.filter(id=notice_pydantic.id).update(**update_data)
    if updated != 1:
        raise CustomException("公告不存在", status_code=404)
    return Result.success()


@router.delete("/delete/{notice_id}", dependencies=[Depends(get_current_admin)])
async def delete(notice_id: int):
    deleted = await Notice.filter(id=notice_id).delete()
    if deleted != 1:
        raise CustomException("公告不存在", status_code=404)
    return Result.success()


# 查询所有
@router.get("/selectAll")
async def select_all(name: str = ""):
    notice_list = await Notice.filter(name__contains=name)
    return Result.success(notice_list)


@router.get("/selectPage", dependencies=[Depends(get_current_admin)])
async def select(
    name: str = "",
    page_num: int = Query(1, alias="pageNum", ge=1),
    page_size: int = Query(5, alias="pageSize", ge=1, le=100),
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
