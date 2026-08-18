"""Built-in Aegis health collectors."""

from .base import CallableCollector, CollectorRegistry, HealthCollector
from .hardware import HardwareCollector
from .node import NodeCollector
from .process import ProcessCollector
from .resource import ResourceCollector
from .topic import TopicCollector, TopicProbe

TopicHealthCollector = TopicCollector
NodeHealthCollector = NodeCollector
ProcessHealthCollector = ProcessCollector
HardwareHealthCollector = HardwareCollector
ResourceHealthCollector = ResourceCollector

__all__ = [
    "CallableCollector",
    "CollectorRegistry",
    "HardwareCollector",
    "HardwareHealthCollector",
    "HealthCollector",
    "NodeCollector",
    "NodeHealthCollector",
    "ProcessCollector",
    "ProcessHealthCollector",
    "ResourceCollector",
    "ResourceHealthCollector",
    "TopicCollector",
    "TopicHealthCollector",
    "TopicProbe",
]
