"""内置算法适配器及公共注册 API。"""

from services.algorithm_adapters.base import (
    AlgorithmAdapter,
    AlgorithmAdapterError,
)
from services.algorithm_adapters.pbas import PbasAlgorithmAdapter, pbas_algorithm_adapter
from services.algorithm_adapters.registry import (
    AlgorithmAdapterRegistry,
    algorithm_adapter_registry,
)


algorithm_adapter_registry.register(pbas_algorithm_adapter)


__all__ = [
    "AlgorithmAdapter",
    "AlgorithmAdapterError",
    "AlgorithmAdapterRegistry",
    "PbasAlgorithmAdapter",
    "algorithm_adapter_registry",
]
