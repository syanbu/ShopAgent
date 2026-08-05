"""Pure compilation of one parsed turn into a complete search snapshot."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from math import isfinite
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, ValidationError

from shop_agent.catalog import ProductCatalog
from shop_agent.models.conversation import ConversationState, QuerySnapshot
from shop_agent.models.query import NumericConstraint, ParsedIntent, SearchConstraints
from shop_agent.models.turn_query import (
    CategoryCandidate,
    SemanticTermOperation,
    SlotOperation,
    TurnQuery,
)


SearchIntent = Literal[
    "new_search",
    "refine_search",
    "switch_category",
    "more_results",
]
ResultStrategy = Literal["stable_refine", "full_rerank", "more_results"]

MISSING_CONTEXT_MESSAGE = "请先说明想购买的商品类型，我才能继续细化或换一批。"
INVALID_CONDITION_MESSAGE = "查询条件与当前商品目录不匹配，请调整后再试。"
PRICE_BASELINE_MESSAGE = "请先说明明确预算，或从最近展示的商品中指定价格基准。"
PRICE_CONFLICT_MESSAGE = "最低价格不能高于最高价格，请调整预算范围。"
_PRICE_STEP = Decimal("0.01")


class QueryMergeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: SearchIntent
    result_strategy: ResultStrategy = "full_rerank"
    state: ConversationState
    snapshot: QuerySnapshot | None = None
    parsed_intent: ParsedIntent | None = None
    needs_clarification: bool = False
    clarification_message: str | None = None


class _ClarificationNeeded(ValueError):
    pass


def merge_turn_query(
    turn: TurnQuery,
    state: ConversationState,
    catalog: ProductCatalog,
    *,
    resolved_product_id: str | None = None,
    resolved_brand: str | None = None,
    resolved_category_scope: CategoryCandidate | None = None,
) -> QueryMergeResult:
    """Compile a search turn without mutating the supplied persistent state."""
    intent = _search_intent(turn)
    base_state = state.model_copy(deep=True)
    try:
        base_snapshot = _select_base_snapshot(turn, base_state)
    except _ClarificationNeeded as error:
        return _clarification_result(intent, base_state, None, str(error))

    try:
        intent = _resolve_search_intent(
            turn,
            base_snapshot,
            base_state,
            catalog,
            resolved_category_scope=resolved_category_scope,
        )
        if intent == "more_results" and _has_query_mutation(turn, resolved_brand):
            intent = "refine_search"
        operation_base = (
            QuerySnapshot() if intent == "switch_category" else base_snapshot
        )
        operation_base = _apply_resolved_category_scope(
            operation_base,
            resolved_category_scope,
        )
        snapshot = _compile_operations(operation_base, turn, catalog)
        snapshot = _apply_resolved_brand(snapshot, turn, resolved_brand)
        snapshot = _apply_approximate_price(snapshot, turn)
        snapshot = _apply_relative_price(
            snapshot,
            turn,
            base_state,
            resolved_product_id=resolved_product_id,
        )
        _validate_taxonomy(snapshot, catalog)
    except _ClarificationNeeded as error:
        return _clarification_result(intent, base_state, None, str(error))

    conflict = _price_conflict(snapshot)
    if conflict is not None:
        return _clarification_result(intent, base_state, snapshot, conflict)
    try:
        snapshot = QuerySnapshot.model_validate(snapshot.model_dump())
    except ValidationError:
        return _clarification_result(
            intent,
            base_state,
            snapshot,
            INVALID_CONDITION_MESSAGE,
        )

    result_strategy = _result_strategy(
        intent,
        turn,
        old_snapshot=state.query_snapshot,
        new_snapshot=snapshot,
    )
    next_state = _update_search_state(base_state, snapshot, intent)
    return QueryMergeResult(
        intent=intent,
        result_strategy=result_strategy,
        state=next_state,
        snapshot=snapshot,
        parsed_intent=snapshot.to_parsed_intent(),
    )


def _search_intent(turn: TurnQuery) -> SearchIntent:
    if turn.intent not in {
        "new_search",
        "refine_search",
        "switch_category",
        "more_results",
    }:
        raise ValueError(f"turn intent {turn.intent!r} is not a search intent")
    return cast(SearchIntent, turn.intent)


def _has_query_mutation(turn: TurnQuery, resolved_brand: str | None) -> bool:
    return bool(
        turn.semantic_term_operations
        or turn.slot_operations
        or turn.relative_price is not None
        or turn.approximate_price is not None
        or resolved_brand is not None
    )


def _compile_operations(
    base_snapshot: QuerySnapshot,
    turn: TurnQuery,
    catalog: ProductCatalog,
) -> QuerySnapshot:
    snapshot = _apply_semantic_operations(
        base_snapshot,
        turn.semantic_term_operations,
    )
    return _apply_slot_operations(snapshot, turn.slot_operations, catalog)


def _select_base_snapshot(
    turn: TurnQuery,
    state: ConversationState,
) -> QuerySnapshot:
    if turn.intent in {"new_search", "switch_category"}:
        return QuerySnapshot()
    if turn.intent in {"refine_search", "more_results"}:
        if state.query_snapshot is None:
            raise _ClarificationNeeded(MISSING_CONTEXT_MESSAGE)
        return state.query_snapshot.model_copy(deep=True)
    raise ValueError(f"turn intent {turn.intent!r} is not a search intent")


def _resolve_search_intent(
    turn: TurnQuery,
    snapshot: QuerySnapshot,
    state: ConversationState,
    catalog: ProductCatalog,
    *,
    resolved_category_scope: CategoryCandidate | None,
) -> SearchIntent:
    intent = _search_intent(turn)
    old_snapshot = state.query_snapshot
    if old_snapshot is None:
        return intent
    new_pair = (
        (
            resolved_category_scope.category,
            resolved_category_scope.sub_category,
        )
        if resolved_category_scope is not None
        else _target_category_pair(snapshot, turn.slot_operations)
    )
    old_pair = (old_snapshot.category, old_snapshot.sub_category)
    valid_pairs: set[tuple[str, str | None]] = {
        (product.category, product.sub_category) for product in catalog.all()
    }
    valid_pairs.update(
        (product.category, None) for product in catalog.all()
    )
    if new_pair in valid_pairs and new_pair != old_pair:
        return "switch_category"
    return intent


def _apply_resolved_category_scope(
    snapshot: QuerySnapshot,
    scope: CategoryCandidate | None,
) -> QuerySnapshot:
    if scope is None:
        return snapshot.model_copy(deep=True)
    return snapshot.model_copy(
        update={
            "category": scope.category,
            "sub_category": scope.sub_category,
        },
        deep=True,
    )


def _apply_semantic_operations(
    snapshot: QuerySnapshot,
    operations: list[SemanticTermOperation],
) -> QuerySnapshot:
    terms = list(snapshot.semantic_terms)
    for operation in operations:
        if operation.operation == "clear":
            terms = []
            continue
        value = _non_blank_string(operation.value)
        if operation.operation == "prioritize":
            terms = [
                value,
                *_stable_remove(terms, value),
            ]
        elif operation.operation == "add":
            terms = _stable_add(terms, value)
        else:
            terms = _stable_remove(terms, value)
    return snapshot.model_copy(update={"semantic_terms": terms}, deep=True)


def _apply_slot_operations(
    snapshot: QuerySnapshot,
    operations: list[SlotOperation],
    catalog: ProductCatalog,
) -> QuerySnapshot:
    category, sub_category = _target_category_pair(snapshot, operations)
    _validate_operation_taxonomy(
        category,
        sub_category,
        operations,
        catalog,
    )
    constraints = snapshot.constraints.model_copy(deep=True)

    for operation in operations:
        if operation.slot == "category":
            category = (
                None
                if operation.operation == "clear"
                else _non_blank_string(operation.value)
            )
        elif operation.slot == "sub_category":
            sub_category = (
                None
                if operation.operation == "clear"
                else _non_blank_string(operation.value)
            )
        elif operation.slot in {
            "constraints.min_price",
            "constraints.max_price",
        }:
            field = operation.slot.removeprefix("constraints.")
            value = (
                None
                if operation.operation == "clear"
                else _non_negative_number(operation.value)
            )
            constraints = constraints.model_copy(update={field: value}, deep=True)
        elif operation.slot == "constraints.price_preference":
            preference = (
                None
                if operation.operation == "clear"
                else _price_preference(operation.value)
            )
            constraints = constraints.model_copy(
                update={"price_preference": preference},
                deep=True,
            )
        elif operation.slot in {
            "constraints.include_brands",
            "constraints.exclude_brands",
            "constraints.required_features",
            "constraints.excluded_features",
        }:
            constraints = _apply_list_slot(constraints, operation)
        elif operation.slot == "constraints.sku_constraints":
            constraints = _apply_sku_operation(constraints, operation)
        else:
            constraints = _apply_numeric_operation(constraints, operation)

    result = snapshot.model_copy(
        update={
            "category": category,
            "sub_category": sub_category,
            "constraints": constraints,
        },
        deep=True,
    )
    _validate_taxonomy(result, catalog)
    return result


def _target_category_pair(
    snapshot: QuerySnapshot,
    operations: list[SlotOperation],
) -> tuple[str | None, str | None]:
    category = snapshot.category
    sub_category = snapshot.sub_category
    for operation in operations:
        if operation.slot == "category":
            category = (
                None
                if operation.operation == "clear"
                else _non_blank_string(operation.value)
            )
        elif operation.slot == "sub_category":
            sub_category = (
                None
                if operation.operation == "clear"
                else _non_blank_string(operation.value)
            )
    return category, sub_category


def _validate_operation_taxonomy(
    category: str | None,
    sub_category: str | None,
    operations: list[SlotOperation],
    catalog: ProductCatalog,
) -> None:
    _validate_taxonomy(
        QuerySnapshot(category=category, sub_category=sub_category),
        catalog,
    )
    allowed_brands = set(catalog.brands())
    sku_taxonomy = catalog.sku_taxonomy()
    allowed_sku = (
        sku_taxonomy.get(f"{category}/{sub_category}", {})
        if category is not None and sub_category is not None
        else {}
    )
    for operation in operations:
        if operation.slot in {
            "constraints.include_brands",
            "constraints.exclude_brands",
        }:
            if operation.operation == "clear":
                continue
            if _non_blank_string(operation.value) not in allowed_brands:
                raise _ClarificationNeeded(INVALID_CONDITION_MESSAGE)
        elif operation.slot == "constraints.sku_constraints":
            key = operation.sku_key
            if key is None or key not in allowed_sku:
                raise _ClarificationNeeded(INVALID_CONDITION_MESSAGE)
            if operation.operation == "clear":
                continue
            value = _non_blank_string(operation.value)
            if value not in allowed_sku[key]:
                raise _ClarificationNeeded(INVALID_CONDITION_MESSAGE)


def _apply_list_slot(
    constraints: SearchConstraints,
    operation: SlotOperation,
) -> SearchConstraints:
    field = operation.slot.removeprefix("constraints.")
    current = list(cast(list[str], getattr(constraints, field)))
    if operation.operation == "clear":
        current = []
    else:
        value = _non_blank_string(operation.value)
        current = (
            _stable_add(current, value)
            if operation.operation == "add"
            else _stable_remove(current, value)
        )

        if operation.operation == "add":
            opposite = {
                "include_brands": "exclude_brands",
                "exclude_brands": "include_brands",
                "required_features": "excluded_features",
                "excluded_features": "required_features",
            }[field]
            opposite_values = _stable_remove(
                list(cast(list[str], getattr(constraints, opposite))),
                value,
            )
            constraints = constraints.model_copy(
                update={opposite: opposite_values},
                deep=True,
            )
    return constraints.model_copy(update={field: current}, deep=True)


def _apply_sku_operation(
    constraints: SearchConstraints,
    operation: SlotOperation,
) -> SearchConstraints:
    key = operation.sku_key
    if key is None:
        raise _ClarificationNeeded(INVALID_CONDITION_MESSAGE)
    sku_constraints = {
        existing_key: list(values)
        for existing_key, values in constraints.sku_constraints.items()
    }
    if operation.operation == "clear":
        sku_constraints.pop(key, None)
    else:
        value = _non_blank_string(operation.value)
        values = list(sku_constraints.get(key, []))
        values = (
            _stable_add(values, value, case_insensitive=False)
            if operation.operation == "add"
            else _stable_remove(values, value, case_insensitive=False)
        )
        if values:
            sku_constraints[key] = values
        else:
            sku_constraints.pop(key, None)
    return constraints.model_copy(
        update={"sku_constraints": sku_constraints},
        deep=True,
    )


def _apply_numeric_operation(
    constraints: SearchConstraints,
    operation: SlotOperation,
) -> SearchConstraints:
    values = [item.model_copy(deep=True) for item in constraints.numeric_constraints]
    if operation.operation == "clear":
        values = []
    else:
        if not isinstance(operation.value, NumericConstraint):
            raise _ClarificationNeeded(INVALID_CONDITION_MESSAGE)
        condition_id = operation.value.condition_id()
        if operation.operation == "add":
            if all(item.condition_id() != condition_id for item in values):
                values.append(operation.value.model_copy(deep=True))
        else:
            values = [item for item in values if item.condition_id() != condition_id]
    return constraints.model_copy(
        update={"numeric_constraints": values},
        deep=True,
    )


def _apply_resolved_brand(
    snapshot: QuerySnapshot,
    turn: TurnQuery,
    resolved_brand: str | None,
) -> QuerySnapshot:
    del turn
    if resolved_brand is None:
        return snapshot.model_copy(deep=True)
    brand = _non_blank_string(resolved_brand)
    constraints = snapshot.constraints.model_copy(
        update={
            "include_brands": _stable_add(
                list(snapshot.constraints.include_brands),
                brand,
            ),
            "exclude_brands": _stable_remove(
                list(snapshot.constraints.exclude_brands),
                brand,
            ),
        },
        deep=True,
    )
    return snapshot.model_copy(update={"constraints": constraints}, deep=True)


def _apply_approximate_price(
    snapshot: QuerySnapshot,
    turn: TurnQuery,
) -> QuerySnapshot:
    approximate = turn.approximate_price
    if approximate is None:
        return snapshot.model_copy(deep=True)

    amount = Decimal(str(approximate.amount))
    tolerance_value = Decimal(str(approximate.tolerance_value))
    integer_digits = max(
        1,
        amount.adjusted() + 1,
        tolerance_value.adjusted() + 1,
    )
    try:
        with localcontext() as context:
            context.prec = max(28, integer_digits + 8)
            tolerance = tolerance_value
            if approximate.tolerance_kind == "percent":
                tolerance = amount * tolerance / Decimal("100")
            lower = max(Decimal("0"), amount - tolerance).quantize(
                _PRICE_STEP,
                rounding=ROUND_HALF_UP,
            )
            upper = (amount + tolerance).quantize(
                _PRICE_STEP,
                rounding=ROUND_HALF_UP,
            )
    except InvalidOperation as error:
        raise _ClarificationNeeded(INVALID_CONDITION_MESSAGE) from error
    lower_value = float(lower)
    upper_value = float(upper)
    if not isfinite(lower_value) or not isfinite(upper_value):
        raise _ClarificationNeeded(INVALID_CONDITION_MESSAGE)
    updates: dict[str, float | None] = {
        "min_price": lower_value if approximate.mode == "target" else None,
        "max_price": upper_value,
    }
    constraints = snapshot.constraints.model_copy(update=updates, deep=True)
    return snapshot.model_copy(update={"constraints": constraints}, deep=True)


def _apply_relative_price(
    snapshot: QuerySnapshot,
    turn: TurnQuery,
    state: ConversationState,
    *,
    resolved_product_id: str | None,
) -> QuerySnapshot:
    direction = turn.relative_price
    if direction is None:
        return snapshot.model_copy(deep=True)

    applicable_slot = (
        "constraints.max_price"
        if direction == "cheaper"
        else "constraints.min_price"
    )
    if any(
        operation.slot == applicable_slot and operation.operation == "replace"
        for operation in turn.slot_operations
    ):
        return snapshot.model_copy(deep=True)

    prices = {
        candidate.product_id: candidate.display_price
        for candidate in state.recent_candidates
    }
    if resolved_product_id is not None:
        baseline = prices.get(resolved_product_id)
        if baseline is None:
            raise _ClarificationNeeded(PRICE_BASELINE_MESSAGE)
    elif state.focused_product_id is not None:
        baseline = prices.get(state.focused_product_id)
        if baseline is None:
            raise _ClarificationNeeded(PRICE_BASELINE_MESSAGE)
    elif prices:
        baseline = min(prices.values()) if direction == "cheaper" else max(prices.values())
    else:
        raise _ClarificationNeeded(PRICE_BASELINE_MESSAGE)

    decimal_baseline = Decimal(str(baseline)).quantize(_PRICE_STEP)
    if direction == "cheaper":
        boundary = decimal_baseline - _PRICE_STEP
        if boundary < 0:
            raise _ClarificationNeeded(PRICE_BASELINE_MESSAGE)
        field = "max_price"
    else:
        boundary = decimal_baseline + _PRICE_STEP
        field = "min_price"
    constraints = snapshot.constraints.model_copy(
        update={field: float(boundary)},
        deep=True,
    )
    return snapshot.model_copy(update={"constraints": constraints}, deep=True)


def _price_conflict(snapshot: QuerySnapshot) -> str | None:
    minimum = snapshot.constraints.min_price
    maximum = snapshot.constraints.max_price
    if minimum is not None and maximum is not None and minimum > maximum:
        return PRICE_CONFLICT_MESSAGE
    return None


def _clarification_result(
    intent: SearchIntent,
    state: ConversationState,
    snapshot: QuerySnapshot | None,
    message: str,
) -> QueryMergeResult:
    return QueryMergeResult(
        intent=intent,
        state=state.model_copy(deep=True),
        snapshot=snapshot.model_copy(deep=True) if snapshot is not None else None,
        needs_clarification=True,
        clarification_message=message,
    )


def _update_search_state(
    state: ConversationState,
    snapshot: QuerySnapshot,
    intent: SearchIntent,
) -> ConversationState:
    if intent not in {"new_search", "switch_category"}:
        return state.model_copy(
            update={
                "active_task": "product_search",
                "query_snapshot": snapshot.model_copy(deep=True),
                "scenario_snapshot": None,
                "pending_clarification": None,
            },
            deep=True,
        )
    return state.model_copy(
        update={
            "active_task": "product_search",
            "query_snapshot": snapshot.model_copy(deep=True),
            "scenario_snapshot": None,
            "recent_candidates": [],
            "focused_product_id": None,
            "seen_product_ids": [],
            "pending_clarification": None,
        },
        deep=True,
    )


def _result_strategy(
    intent: SearchIntent,
    turn: TurnQuery,
    *,
    old_snapshot: QuerySnapshot | None,
    new_snapshot: QuerySnapshot,
) -> ResultStrategy:
    if intent == "more_results":
        return "more_results"
    if intent in {"new_search", "switch_category"} or old_snapshot is None:
        return "full_rerank"
    if _has_soft_mutation(turn):
        return "full_rerank"
    relation = _hard_constraint_relation(
        old_snapshot.constraints,
        new_snapshot.constraints,
    )
    return "stable_refine" if relation == {"tighten"} else "full_rerank"


def _has_soft_mutation(turn: TurnQuery) -> bool:
    return bool(
        turn.semantic_term_operations
        or any(
            operation.slot == "constraints.price_preference"
            for operation in turn.slot_operations
        )
    )


def _hard_constraint_relation(
    old: SearchConstraints,
    new: SearchConstraints,
) -> set[Literal["tighten", "relax"]]:
    relation: set[Literal["tighten", "relax"]] = set()
    relation.update(_lower_bound_relation(old.min_price, new.min_price))
    relation.update(_upper_bound_relation(old.max_price, new.max_price))
    relation.update(_allowed_values_relation(old.include_brands, new.include_brands))
    relation.update(_excluded_values_relation(old.exclude_brands, new.exclude_brands))
    relation.update(
        _excluded_values_relation(old.required_features, new.required_features)
    )
    relation.update(
        _excluded_values_relation(old.excluded_features, new.excluded_features)
    )

    sku_keys = set(old.sku_constraints).union(new.sku_constraints)
    for key in sku_keys:
        relation.update(
            _allowed_values_relation(
                old.sku_constraints.get(key, []),
                new.sku_constraints.get(key, []),
            )
        )
    relation.update(
        _excluded_values_relation(
            [item.condition_id() for item in old.numeric_constraints],
            [item.condition_id() for item in new.numeric_constraints],
        )
    )
    return relation


def _lower_bound_relation(
    old: float | None,
    new: float | None,
) -> set[Literal["tighten", "relax"]]:
    if old == new:
        return set()
    if old is None:
        return {"tighten"}
    if new is None:
        return {"relax"}
    return {"tighten"} if new > old else {"relax"}


def _upper_bound_relation(
    old: float | None,
    new: float | None,
) -> set[Literal["tighten", "relax"]]:
    if old == new:
        return set()
    if old is None:
        return {"tighten"}
    if new is None:
        return {"relax"}
    return {"tighten"} if new < old else {"relax"}


def _allowed_values_relation(
    old_values: list[str],
    new_values: list[str],
) -> set[Literal["tighten", "relax"]]:
    old = set(old_values)
    new = set(new_values)
    if old == new:
        return set()
    if not old:
        return {"tighten"}
    if not new:
        return {"relax"}
    relation: set[Literal["tighten", "relax"]] = set()
    if not new.issuperset(old):
        relation.add("tighten")
    if not new.issubset(old):
        relation.add("relax")
    return relation


def _excluded_values_relation(
    old_values: list[str],
    new_values: list[str],
) -> set[Literal["tighten", "relax"]]:
    old = set(old_values)
    new = set(new_values)
    if old == new:
        return set()
    relation: set[Literal["tighten", "relax"]] = set()
    if not new.issubset(old):
        relation.add("tighten")
    if not new.issuperset(old):
        relation.add("relax")
    return relation


def _validated_snapshot(
    snapshot: QuerySnapshot,
    catalog: ProductCatalog,
) -> QuerySnapshot:
    _validate_taxonomy(snapshot, catalog)
    return QuerySnapshot.model_validate(snapshot.model_dump())


def _validate_taxonomy(
    snapshot: QuerySnapshot,
    catalog: ProductCatalog,
) -> None:
    products = catalog.all()
    categories = {product.category for product in products}
    sub_categories = {product.sub_category for product in products}
    category_pairs = {
        (product.category, product.sub_category) for product in products
    }
    if snapshot.category is not None and snapshot.category not in categories:
        raise _ClarificationNeeded(INVALID_CONDITION_MESSAGE)
    if (
        snapshot.sub_category is not None
        and snapshot.sub_category not in sub_categories
    ):
        raise _ClarificationNeeded(INVALID_CONDITION_MESSAGE)
    if (
        snapshot.category is not None
        and snapshot.sub_category is not None
        and (snapshot.category, snapshot.sub_category) not in category_pairs
    ):
        raise _ClarificationNeeded(INVALID_CONDITION_MESSAGE)

    allowed_brands = set(catalog.brands())
    submitted_brands = {
        *snapshot.constraints.include_brands,
        *snapshot.constraints.exclude_brands,
    }
    if submitted_brands.difference(allowed_brands):
        raise _ClarificationNeeded(INVALID_CONDITION_MESSAGE)

    sku_constraints = snapshot.constraints.sku_constraints
    if not sku_constraints:
        return
    if snapshot.category is None or snapshot.sub_category is None:
        raise _ClarificationNeeded(INVALID_CONDITION_MESSAGE)
    pair = f"{snapshot.category}/{snapshot.sub_category}"
    allowed = catalog.sku_taxonomy().get(pair, {})
    if set(sku_constraints).difference(allowed):
        raise _ClarificationNeeded(INVALID_CONDITION_MESSAGE)
    if any(
        set(values).difference(allowed[key])
        for key, values in sku_constraints.items()
    ):
        raise _ClarificationNeeded(INVALID_CONDITION_MESSAGE)


def _stable_add(
    values: list[str],
    value: str,
    *,
    case_insensitive: bool = True,
) -> list[str]:
    expected = value.casefold() if case_insensitive else value
    if any(
        (existing.casefold() if case_insensitive else existing) == expected
        for existing in values
    ):
        return list(values)
    return [*values, value]


def _stable_remove(
    values: list[str],
    value: str,
    *,
    case_insensitive: bool = True,
) -> list[str]:
    expected = value.casefold() if case_insensitive else value
    return [
        existing
        for existing in values
        if (existing.casefold() if case_insensitive else existing) != expected
    ]


def _non_blank_string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _ClarificationNeeded(INVALID_CONDITION_MESSAGE)
    return value.strip()


def _non_negative_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise _ClarificationNeeded(INVALID_CONDITION_MESSAGE)
    return float(value)


def _price_preference(value: object) -> Literal["value"]:
    if value != "value":
        raise _ClarificationNeeded(INVALID_CONDITION_MESSAGE)
    return "value"
