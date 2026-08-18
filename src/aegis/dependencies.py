"""Dependency graph and root-cause propagation."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping

from .models import HealthObservation, HealthState, HealthStatus


class DependencyCycleError(ValueError):
    """Raised when a robot dependency graph contains a cycle."""


class DependencyGraph:
    """Represent ``dependent -> prerequisites`` relationships.

    A dependency failure is projected onto downstream checks as a derived
    state.  The original check remains the root cause, which lets the event
    layer emit one actionable alarm instead of one independent alert per
    unavailable subsystem.
    """

    def __init__(self, dependencies: Mapping[str, Iterable[str]] | None = None) -> None:
        self.dependencies: dict[str, set[str]] = {
            str(node): {str(item) for item in items}
            for node, items in (dependencies or {}).items()
        }
        self._validate()

    def _validate(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise DependencyCycleError(f"dependency cycle detected at '{node}'")
            if node in visited:
                return
            visiting.add(node)
            for dependency in self.dependencies.get(node, set()):
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        all_nodes = set(self.dependencies) | {
            item for values in self.dependencies.values() for item in values
        }
        for node in all_nodes:
            visit(node)

    def add(self, dependent: str, dependency: str) -> None:
        self.dependencies.setdefault(dependent, set()).add(dependency)
        try:
            self._validate()
        except Exception:
            self.dependencies[dependent].remove(dependency)
            raise

    def prerequisites(self, name: str) -> set[str]:
        return set(self.dependencies.get(name, set()))

    def dependents(self, name: str) -> set[str]:
        return {node for node, values in self.dependencies.items() if name in values}

    def descendants(self, name: str) -> set[str]:
        result: set[str] = set()
        queue = deque([name])
        while queue:
            current = queue.popleft()
            for dependent in self.dependents(current):
                if dependent not in result:
                    result.add(dependent)
                    queue.append(dependent)
        return result

    def propagate(
        self,
        states: dict[str, HealthState],
        observations: dict[str, HealthObservation],
    ) -> dict[str, HealthObservation]:
        """Apply dependency-derived health to ``states`` and observations."""

        effective = dict(observations)
        # Repeated passes ensure a transitive chain (camera -> detector ->
        # tracking) gets the same root cause.
        ordered = self._topological_nodes(set(states) | set(self.dependencies))
        for name in ordered:
            if name not in states or name not in self.dependencies:
                continue
            failed = []
            for dependency in self.dependencies[name]:
                dependency_state = states.get(dependency)
                if dependency_state and dependency_state.status != HealthStatus.OK:
                    failed.append(dependency_state)
            if not failed:
                continue

            root = next((state.root_cause or state.name for state in failed), failed[0].name)
            severity = HealthStatus.ERROR if any(
                state.status in {HealthStatus.ERROR, HealthStatus.RECOVERING}
                for state in failed
            ) else HealthStatus.WARNING
            state = states[name]
            observation = effective.get(name)
            message = f"unavailable because dependency '{root}' is {severity.value}"
            if observation is None:
                observation = HealthObservation(
                    check_name=name,
                    status=severity,
                    message=message,
                    details={},
                )
            else:
                observation = HealthObservation(
                    check_name=name,
                    status=severity,
                    message=message,
                    observed_value=observation.observed_value,
                    timestamp=observation.timestamp,
                    details=dict(observation.details),
                )
            observation.details.update(
                {
                    "derived": True,
                    "root_cause": root,
                    "blocked_by": sorted(self.dependencies[name]),
                }
            )
            effective[name] = observation
            # Keep the working state in sync so a transitive dependent sees
            # this failure during the same propagation pass.
            state.status = severity
            state.message = message
            state.root_cause = root
            state.derived = True
        return effective

    def _topological_nodes(self, names: set[str]) -> list[str]:
        # Dependencies must be visited before dependents.  The cycle check in
        # __init__ means this Kahn traversal always completes.
        indegree = {name: 0 for name in names}
        outgoing: dict[str, set[str]] = defaultdict(set)
        for dependent, prerequisites in self.dependencies.items():
            if dependent not in names:
                continue
            for prerequisite in prerequisites:
                if prerequisite not in names:
                    continue
                indegree[dependent] += 1
                outgoing[prerequisite].add(dependent)
        queue = deque(sorted(name for name, degree in indegree.items() if degree == 0))
        result: list[str] = []
        while queue:
            current = queue.popleft()
            result.append(current)
            for dependent in sorted(outgoing[current]):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    queue.append(dependent)
        return result
