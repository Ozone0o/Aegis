"""Safe, declarative policy evaluation for Aegis."""

from __future__ import annotations

import ast
import operator
import re
import time
from dataclasses import dataclass
from typing import Any

from .models import (
    ActionSpec,
    ActionType,
    HealthObservation,
    HealthState,
    HealthStatus,
    PolicyRule,
    RecoveryDecision,
    RecoveryResult,
)

_DURATION_RE = re.compile(r"(?<![\w.])([0-9]+(?:\.[0-9]+)?)\s*(ms|s|m|h|d)\b", re.IGNORECASE)
_TOPIC_PHRASE_RE = re.compile(
    r"\b([A-Za-z_][\w-]*)(?:\s+topic)?\s+stale(?:_age)?\b",
    re.IGNORECASE,
)


def parse_duration(value: Any, default: float = 0.0) -> float:
    """Parse seconds, or a human duration such as ``2s`` or ``500ms``."""

    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(ms|s|m|h|d)?", text)
    if not match:
        raise ValueError(f"invalid duration: {value!r}")
    amount = float(match.group(1))
    factor = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0, None: 1.0}
    return amount * factor[match.group(2)]


class _CheckView:
    def __init__(self, state: HealthState | None, observation: HealthObservation | None) -> None:
        self._state = state
        self._observation = observation

    @property
    def status(self) -> HealthStatus:
        return self._state.status if self._state else HealthStatus.ERROR

    @property
    def stale_age(self) -> float | None:
        if self._observation:
            return self._observation.details.get("stale_age")
        return self._state.stale_age if self._state else None

    @property
    def stale(self) -> float | None:
        return self.stale_age

    @property
    def age(self) -> float | None:
        return self.stale_age

    @property
    def topic(self) -> _CheckView:
        return self

    @property
    def current_hz(self) -> float | None:
        if self._observation:
            return self._observation.details.get("current_hz")
        return self._state.current_hz if self._state else None

    @property
    def hz(self) -> float | None:
        return self.current_hz

    @property
    def value(self) -> float | None:
        if self._observation:
            return self._observation.observed_value
        return self._state.observed_value if self._state else None

    @property
    def expected_rate(self) -> float | None:
        return self._state.spec.expected_rate if self._state else None

    @property
    def failures(self) -> int:
        return self._state.consecutive_failures if self._state else 0


class ConditionEvaluator:
    """Evaluate a small expression language without calling ``eval``."""

    _comparators = {
        ast.Eq: operator.eq,
        ast.NotEq: operator.ne,
        ast.Gt: operator.gt,
        ast.GtE: operator.ge,
        ast.Lt: operator.lt,
        ast.LtE: operator.le,
        ast.In: lambda left, right: left in right,
        ast.NotIn: lambda left, right: left not in right,
        ast.Is: operator.is_,
        ast.IsNot: operator.is_not,
    }
    _arithmetic = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Mod: operator.mod,
    }

    def evaluate(
        self,
        condition: str | dict[str, Any] | list[Any],
        states: dict[str, HealthState],
        observations: dict[str, HealthObservation],
    ) -> tuple[bool, str | None, str]:
        if isinstance(condition, list):
            results = [self.evaluate(item, states, observations) for item in condition]
            matched = all(item[0] for item in results)
            source = next((item[1] for item in results if item[1]), None)
            return matched, source, "all conditions matched" if matched else "a condition failed"

        if isinstance(condition, dict):
            return self._evaluate_mapping(condition, states, observations)

        expression = self._normalize_expression(str(condition))
        try:
            tree = ast.parse(expression, mode="eval")
            names = self._expression_names(tree)
            context = {
                name: _CheckView(states.get(name), observations.get(name))
                for name in names
                if name in states or name in observations
            }
            value = self._eval_node(tree.body, context)
        except (SyntaxError, TypeError, ValueError, KeyError, ZeroDivisionError) as exc:
            return False, self._guess_source(expression, states), f"invalid condition: {exc}"
        source = self._guess_source(expression, states)
        return bool(value), source, expression

    def _evaluate_mapping(
        self,
        condition: dict[str, Any],
        states: dict[str, HealthState],
        observations: dict[str, HealthObservation],
    ) -> tuple[bool, str | None, str]:
        if "all" in condition:
            results = [self.evaluate(item, states, observations) for item in condition["all"]]
            return (
                all(item[0] for item in results),
                next((item[1] for item in results if item[1]), None),
                (
                    "all conditions matched"
                    if all(item[0] for item in results)
                    else "a condition failed"
                ),
            )
        if "any" in condition:
            results = [self.evaluate(item, states, observations) for item in condition["any"]]
            matched = next((item for item in results if item[0]), None)
            return (
                matched is not None,
                matched[1] if matched else next((item[1] for item in results if item[1]), None),
                "a condition matched" if matched else "no condition matched",
            )
        if "not" in condition:
            matched, source, reason = self.evaluate(condition["not"], states, observations)
            return not matched, source, f"not ({reason})"

        name = condition.get("check", condition.get("source", condition.get("name")))
        if name is None:
            return False, None, "condition has no check"
        name = str(name)
        state = states.get(name)
        observation = observations.get(name)
        view = _CheckView(state, observation)

        if "status" in condition:
            expected = condition["status"]
            if isinstance(expected, (list, tuple, set)):
                status_match = view.status.value in {self._status_value(item) for item in expected}
            else:
                status_match = view.status.value == self._status_value(expected)
            if not status_match:
                return False, name, f"{name}.status is {view.status.value}"

        field = condition.get("field")
        if field:
            actual = getattr(view, str(field), None)
            expected = condition.get("value")
            if expected is None and isinstance(condition.get("compare"), dict):
                compare = condition["compare"]
                expected = compare.get("value")
                field = compare.get("field", field)
                actual = getattr(view, str(field), None)
            operator_name = str(condition.get("operator", condition.get("op", "==")))
            if not self._compare(actual, operator_name, expected):
                return False, name, f"{name}.{field} comparison failed"
        return True, name, f"{name} condition matched"

    @staticmethod
    def _status_value(value: Any) -> str:
        if isinstance(value, HealthStatus):
            return value.value
        normalized = str(value).upper()
        return normalized

    def _compare(self, actual: Any, operator_name: str, expected: Any) -> bool:
        if isinstance(actual, HealthStatus):
            actual = actual.value
        if isinstance(expected, HealthStatus):
            expected = expected.value
        if isinstance(expected, str):
            try:
                expected = parse_duration(expected)
            except ValueError:
                expected = (
                    self._status_value(expected)
                    if operator_name in {"==", "!=", "in", "not in"}
                    else expected
                )
        function = {
            "==": operator.eq,
            "!=": operator.ne,
            ">": operator.gt,
            ">=": operator.ge,
            "<": operator.lt,
            "<=": operator.le,
            "in": lambda left, right: left in right,
            "not in": lambda left, right: left not in right,
        }.get(operator_name)
        if function is None:
            raise ValueError(f"unsupported operator {operator_name!r}")
        try:
            return bool(function(actual, expected))
        except TypeError:
            return False

    def _normalize_expression(self, expression: str) -> str:
        expression = re.sub(r"^\s*if\s+", "", expression, flags=re.IGNORECASE)
        expression = _TOPIC_PHRASE_RE.sub(r"\1.stale_age", expression)
        return _DURATION_RE.sub(lambda match: str(parse_duration(match.group(0))), expression)

    def _expression_names(self, tree: ast.AST) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
        return names

    @staticmethod
    def _guess_source(expression: str, states: dict[str, HealthState]) -> str | None:
        for name in sorted(states, key=len, reverse=True):
            if re.search(rf"\b{re.escape(name)}\b", expression):
                return name
        return None

    def _eval_node(self, node: ast.AST, context: dict[str, Any]) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in {"OK", "WARNING", "ERROR", "RECOVERING"}:
                return self._status_value(node.id)
            if node.id in {"True", "False", "None"}:
                return {"True": True, "False": False, "None": None}[node.id]
            if node.id not in context:
                raise KeyError(node.id)
            return context[node.id]
        if isinstance(node, ast.Attribute):
            value = self._eval_node(node.value, context)
            if node.attr.startswith("_"):
                raise ValueError("private attributes are not allowed")
            return getattr(value, node.attr)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            values = [self._eval_node(item, context) for item in node.elts]
            return type({ast.List: list, ast.Tuple: tuple, ast.Set: set}[type(node)])(values)
        if isinstance(node, ast.BoolOp):
            values = [bool(self._eval_node(item, context)) for item in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.USub, ast.UAdd)):
            value = self._eval_node(node.operand, context)
            if isinstance(node.op, ast.Not):
                return not value
            return -value if isinstance(node.op, ast.USub) else +value
        if isinstance(node, ast.BinOp) and type(node.op) in self._arithmetic:
            return self._arithmetic[type(node.op)](
                self._eval_node(node.left, context), self._eval_node(node.right, context)
            )
        if isinstance(node, ast.Compare):
            left = self._eval_node(node.left, context)
            for operator_node, comparator in zip(node.ops, node.comparators):
                right = self._eval_node(comparator, context)
                function = next(
                    (
                        function
                        for kind, function in self._comparators.items()
                        if isinstance(operator_node, kind)
                    ),
                    None,
                )
                if function is None:
                    raise ValueError(f"unsupported comparison {type(operator_node).__name__}")
                if isinstance(left, HealthStatus):
                    left = left.value
                if isinstance(right, HealthStatus):
                    right = right.value
                try:
                    matched = function(left, right)
                except TypeError:
                    matched = False
                if not matched:
                    return False
                left = right
            return True
        raise ValueError(f"unsupported expression node {type(node).__name__}")


@dataclass
class _RuleRuntime:
    active: bool = False
    last_fired: float | None = None
    attempts: int = 0


class PolicyEngine:
    """Evaluate policies once per controller tick with edge triggering."""

    def __init__(
        self,
        policies: list[PolicyRule] | None = None,
        actions: dict[str, ActionSpec] | None = None,
        default_cooldown: float = 30.0,
        clock: callable = time.time,
    ) -> None:
        self.policies = list(policies or [])
        self.actions = dict(actions or {})
        self.default_cooldown = default_cooldown
        self.clock = clock
        self.evaluator = ConditionEvaluator()
        self._runtime: dict[str, _RuleRuntime] = {
            policy.name: _RuleRuntime() for policy in self.policies
        }

    def evaluate(
        self,
        states: dict[str, HealthState],
        observations: dict[str, HealthObservation],
        now: float | None = None,
    ) -> list[RecoveryDecision]:
        current = self.clock() if now is None else now
        decisions: list[RecoveryDecision] = []
        for policy in self.policies:
            runtime = self._runtime.setdefault(policy.name, _RuleRuntime())
            if not policy.enabled:
                runtime.active = False
                continue
            matched, source, reason = self.evaluator.evaluate(
                policy.condition, states, observations
            )
            if not matched:
                runtime.active = False
                runtime.attempts = 0
                continue

            cooldown = max(
                0.0,
                self.default_cooldown if policy.cooldown is None else policy.cooldown,
            )
            within_cooldown = (
                runtime.last_fired is not None and current - runtime.last_fired < cooldown
            )
            attempts_exhausted = (
                policy.max_attempts is not None and runtime.attempts >= policy.max_attempts
            )
            should_fire = (
                (not runtime.active or policy.repeat)
                and not within_cooldown
                and not attempts_exhausted
            )
            if not should_fire:
                runtime.active = True
                continue
            if source is None:
                source = policy.name
            action = self.resolve_action(policy.action, source, policy.name)
            runtime.active = True
            runtime.last_fired = current
            runtime.attempts += 1
            decisions.append(
                RecoveryDecision(
                    policy_name=policy.name,
                    source=source,
                    action=action,
                    reason=reason,
                    created_at=current,
                    dedupe_key=f"policy:{policy.name}:{source}",
                )
            )
        return decisions

    def resolve_action(
        self,
        action: ActionSpec | str | dict[str, Any],
        source: str,
        policy_name: str,
    ) -> ActionSpec:
        if isinstance(action, ActionSpec):
            return action
        if isinstance(action, dict):
            action_type = action.get(
                "type", action.get("action", action.get("name", "execute_command"))
            )
            params = dict(action.get("params", {}) or {})
            reserved = {
                "name", "type", "action", "target", "node", "launch", "command",
                "timeout", "unsafe_shell", "params",
            }
            params.update({str(key): value for key, value in action.items() if key not in reserved})
            return ActionSpec(
                name=str(action.get("name", policy_name)),
                type=str(action_type),
                target=action.get("target", action.get("node", action.get("launch", source))),
                command=action.get("command"),
                unsafe_shell=bool(action.get("unsafe_shell", False)),
                timeout=parse_duration(action.get("timeout"), 30.0),
                params=params,
            )
        text = str(action).strip()
        if text in self.actions:
            configured = self.actions[text]
            return ActionSpec(
                name=configured.name,
                type=configured.type,
                target=configured.target or source,
                command=configured.command,
                unsafe_shell=configured.unsafe_shell,
                timeout=configured.timeout,
                cooldown=configured.cooldown,
                max_attempts=configured.max_attempts,
                params=dict(configured.params),
            )
        normalized = text.lower()
        if normalized.startswith("restart") and "launch" in normalized:
            action_type = ActionType.RESTART_LAUNCH
        elif normalized.startswith("restart"):
            action_type = ActionType.RESTART_NODE
        elif normalized.startswith("notify"):
            action_type = ActionType.NOTIFY_OPERATOR
        elif normalized.startswith("safe") or "shutdown" in normalized:
            action_type = ActionType.SAFE_SHUTDOWN
        elif normalized.startswith("execute") or normalized.startswith("run"):
            action_type = ActionType.EXECUTE_COMMAND
        else:
            action_type = text
        target = source
        match = re.search(r"\b(?:node|launch)\s+([\w./:-]+)", text, re.IGNORECASE)
        if match:
            target = match.group(1)
        return ActionSpec(name=policy_name, type=action_type, target=target)

    def notify_recovery_result(self, decision: RecoveryDecision, result: RecoveryResult) -> None:
        """Allow a failed action to retry after its configured cooldown."""

        if result.success:
            return
        runtime = self._runtime.get(decision.policy_name)
        if runtime is not None:
            runtime.active = False
