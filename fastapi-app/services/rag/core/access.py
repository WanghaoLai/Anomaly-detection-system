"""知识 Node 的服务端访问控制；不信任问题文本或模型输出。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class AccessPrincipal:
    user_id: int | None
    role: str

    @classmethod
    def from_mapping(cls, value: Mapping | None) -> "AccessPrincipal":
        # 内部兼容调用未传 principal 时使用系统角色；HTTP API 必须传入
        # get_current_user/get_current_admin 产生的可信身份。
        if value is None:
            return cls(user_id=None, role="系统")
        return cls(
            user_id=(int(value["user_id"]) if value.get("user_id") is not None else None),
            role=str(value.get("role") or ""),
        )


@dataclass(frozen=True)
class DocumentAccessPolicy:
    """可持久化到 Release Manifest/Node metadata 的规范化文档权限。"""

    visibility: str
    allowed_roles: str
    allowed_user_ids: str

    VISIBILITIES = frozenset({"public", "internal", "admin_only"})
    ROLES = frozenset({"管理员", "用户"})

    @classmethod
    def normalize(
        cls,
        *,
        visibility: object = "internal",
        allowed_roles: object = "管理员,用户",
        allowed_user_ids: object = "",
    ) -> "DocumentAccessPolicy":
        normalized_visibility = str(visibility or "internal").strip().lower()
        if normalized_visibility not in cls.VISIBILITIES:
            raise ValueError("visibility 仅支持 public、internal、admin_only")

        roles = KnowledgeAccessPolicy._roles(allowed_roles)
        if not roles or not roles.issubset(cls.ROLES):
            raise ValueError("allowed_roles 仅支持管理员、用户且不能为空")
        if normalized_visibility == "admin_only" and roles != {"管理员"}:
            raise ValueError("admin_only 文档只能授权管理员")

        raw_user_ids = KnowledgeAccessPolicy._roles(
            allowed_user_ids, empty_default=False
        )
        user_ids: list[str] = []
        for value in raw_user_ids:
            try:
                user_id = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("allowed_user_ids 必须是正整数列表") from exc
            if user_id <= 0:
                raise ValueError("allowed_user_ids 必须是正整数列表")
            user_ids.append(str(user_id))
        if user_ids and "用户" not in roles:
            raise ValueError("指定 allowed_user_ids 时 allowed_roles 必须包含用户")

        role_order = [role for role in ("管理员", "用户") if role in roles]
        return cls(
            visibility=normalized_visibility,
            allowed_roles=",".join(role_order),
            allowed_user_ids=",".join(sorted(set(user_ids), key=int)),
        )

    def as_metadata(self) -> dict[str, str]:
        return {
            "visibility": self.visibility,
            "allowed_roles": self.allowed_roles,
            "allowed_user_ids": self.allowed_user_ids,
        }


class KnowledgeAccessPolicy:
    """基于索引 metadata 的强制访问控制，用户 Prompt 无法参与决策。"""

    USER_VISIBILITIES = frozenset({"public", "internal"})

    @staticmethod
    def _roles(value: object, *, empty_default: bool = True) -> set[str]:
        if value is None or value == "":
            return {"管理员", "用户"} if empty_default else set()
        if isinstance(value, (list, tuple, set)):
            return {str(item).strip() for item in value if str(item).strip()}
        text = str(value).strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return {
                        str(item).strip() for item in parsed if str(item).strip()
                    }
            except (TypeError, ValueError, json.JSONDecodeError):
                return set()
        return {
            item.strip() for item in text.replace("，", ",").split(",")
            if item.strip()
        }

    def is_allowed(self, result: Mapping, principal: AccessPrincipal) -> bool:
        visibility = str(result.get("visibility") or "internal").lower()
        allowed_roles = self._roles(result.get("allowed_roles"))
        if principal.role in {"系统", "管理员"}:
            return principal.role == "系统" or "管理员" in allowed_roles
        if principal.role != "用户":
            return False
        if visibility not in self.USER_VISIBILITIES or "用户" not in allowed_roles:
            return False
        allowed_user_ids = result.get("allowed_user_ids")
        if allowed_user_ids in (None, ""):
            return True
        raw_ids = self._roles(allowed_user_ids)
        return principal.user_id is not None and str(principal.user_id) in raw_ids

    def filter(self, results: list, principal: AccessPrincipal) -> list:
        return [item for item in (results or []) if self.is_allowed(item, principal)]


__all__ = [
    "AccessPrincipal",
    "DocumentAccessPolicy",
    "KnowledgeAccessPolicy",
]
