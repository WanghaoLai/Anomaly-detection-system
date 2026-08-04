"""进程内算法适配器注册表。"""

from __future__ import annotations

from collections.abc import Iterable

from services.algorithm_adapters.base import AlgorithmAdapter


class AlgorithmAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, AlgorithmAdapter] = {}

    @staticmethod
    def normalize(key: str) -> str:
        return (key or "").strip().upper()

    def register(self, adapter: AlgorithmAdapter) -> AlgorithmAdapter:
        key = self.normalize(adapter.key)
        if not key:
            raise ValueError("算法适配器 key 不能为空")
        if key in self._adapters:
            raise ValueError(f"算法适配器重复注册: {key}")
        self._adapters[key] = adapter
        return adapter

    def get(self, key: str) -> AlgorithmAdapter | None:
        return self._adapters.get(self.normalize(key))

    def require(self, key: str) -> AlgorithmAdapter:
        adapter = self.get(key)
        if adapter is None:
            raise KeyError(f"算法尚未安装训练适配器: {self.normalize(key) or '<empty>'}")
        return adapter

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))

    def values(self) -> Iterable[AlgorithmAdapter]:
        return self._adapters.values()


algorithm_adapter_registry = AlgorithmAdapterRegistry()

