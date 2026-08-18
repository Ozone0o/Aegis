"""ROS graph node collector with an injectable graph provider."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from ..models import HealthCheckSpec, HealthObservation, HealthStatus
from .base import HealthCollector


class NodeCollector(HealthCollector):
    kind = "node"

    def __init__(self, node_list_provider: Callable[[], Iterable[object]] | None = None) -> None:
        self.node_list_provider = node_list_provider
        self._presence: dict[str, bool] = {}

    def set_node_list_provider(self, provider: Callable[[], Iterable[object]]) -> None:
        self.node_list_provider = provider

    def set_presence(self, target: str, exists: bool) -> None:
        self._presence[target] = exists

    def _exists(self, target: str) -> bool:
        if target in self._presence:
            return self._presence[target]
        if self.node_list_provider is None:
            return False
        try:
            nodes = self.node_list_provider()
            for item in nodes:
                if isinstance(item, str) and item == target:
                    return True
                if isinstance(item, (tuple, list)) and item and item[0] == target:
                    return True
                if isinstance(item, dict) and item.get("name") == target:
                    return True
        except Exception:
            return False
        return False

    def collect(self, check: HealthCheckSpec) -> HealthObservation:
        target = check.target or check.params.get("node_name")
        if not target:
            return self.unavailable(check, "node check has no target")
        exists = self._exists(str(target))
        return HealthObservation(
            check_name=check.name,
            status=HealthStatus.OK if exists else HealthStatus.ERROR,
            message=f"node {target} is {'present' if exists else 'missing'}",
            details={"collector": self.kind, "node": target, "exists": exists},
        )
