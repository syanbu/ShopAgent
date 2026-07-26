from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from shop_agent.models.state import ShoppingState
from shop_agent.workflow.dependencies import WorkflowDependencies
from shop_agent.workflow.nodes import (
    build_nodes,
    route_compilation,
    route_pending_action,
    route_product_question,
    route_reference_resolution,
    route_resumed_action,
    route_retrieval,
    route_turn,
    route_validation,
)


def build_graph(dependencies: WorkflowDependencies) -> CompiledStateGraph:
    nodes = build_nodes(dependencies)
    builder = StateGraph(ShoppingState)
    builder.add_node("load_conversation", nodes.load_conversation)
    builder.add_node("parse_turn_query", nodes.parse_turn_query)
    builder.add_node("resume_pending_action", nodes.resume_pending_action)
    builder.add_node("resolve_reference", nodes.resolve_reference)
    builder.add_node("persist_clarification", nodes.persist_clarification)
    builder.add_node("merge_query_snapshot", nodes.merge_query_snapshot)
    builder.add_node("compile_effective_query", nodes.compile_effective_query)
    builder.add_node("retrieve_chunks", nodes.retrieve_chunks)
    builder.add_node("aggregate_products", nodes.aggregate_products)
    builder.add_node("semantic_rerank", nodes.semantic_rerank)
    builder.add_node("validate_evidence", nodes.validate_evidence)
    builder.add_node("decide_candidates", nodes.decide_candidates)
    builder.add_node("persist_search_result", nodes.persist_search_result)
    builder.add_node("persist_no_results", nodes.persist_no_results)
    builder.add_node("load_product_facts", nodes.load_product_facts)
    builder.add_node("fetch_product_knowledge", nodes.fetch_product_knowledge)
    builder.add_node("persist_focus", nodes.persist_focus)
    builder.add_node("generate_product_response", nodes.generate_product_response)
    builder.add_node("emit_product_events", nodes.emit_product_events)
    builder.add_node("generate_response", nodes.generate_response)

    builder.add_edge(START, "load_conversation")
    builder.add_edge("load_conversation", "parse_turn_query")
    builder.add_conditional_edges(
        "parse_turn_query",
        route_pending_action,
        {
            "resume_pending_action": "resume_pending_action",
            "resolve_reference": "resolve_reference",
        },
    )
    builder.add_conditional_edges(
        "resume_pending_action",
        route_resumed_action,
        {"resolve_reference": "resolve_reference", "end": END},
    )
    builder.add_conditional_edges(
        "resolve_reference",
        route_reference_resolution,
        {
            "resolved": "route_turn",
            "needs_clarification": "persist_clarification",
        },
    )
    builder.add_node("route_turn", _route_turn_node)
    builder.add_conditional_edges(
        "route_turn",
        route_turn,
        {
            "search": "merge_query_snapshot",
            "product_question": "load_product_facts",
            "clarification_answer": "generate_response",
            "non_shopping": "generate_response",
        },
    )
    builder.add_conditional_edges(
        "merge_query_snapshot",
        route_compilation,
        {
            "compiled": "compile_effective_query",
            "needs_clarification": "persist_clarification",
        },
    )
    builder.add_conditional_edges(
        "compile_effective_query",
        route_compilation,
        {
            "compiled": "retrieve_chunks",
            "needs_clarification": "persist_clarification",
        },
    )
    builder.add_edge("persist_clarification", END)
    builder.add_conditional_edges(
        "retrieve_chunks",
        route_retrieval,
        {"has_results": "aggregate_products", "no_results": "persist_no_results"},
    )
    builder.add_edge("aggregate_products", "semantic_rerank")
    builder.add_edge("semantic_rerank", "validate_evidence")
    builder.add_conditional_edges(
        "validate_evidence",
        route_validation,
        {
            "has_candidates": "decide_candidates",
            "no_candidates": "persist_no_results",
        },
    )
    builder.add_edge("decide_candidates", "persist_search_result")
    builder.add_edge("persist_search_result", "emit_product_events")
    builder.add_edge("emit_product_events", "generate_response")
    builder.add_edge("persist_no_results", "generate_response")
    builder.add_conditional_edges(
        "load_product_facts",
        route_product_question,
        {
            "structured": "persist_focus",
            "semantic": "fetch_product_knowledge",
        },
    )
    builder.add_edge("fetch_product_knowledge", "persist_focus")
    builder.add_edge("persist_focus", "generate_product_response")
    builder.add_edge("generate_product_response", END)
    builder.add_edge("generate_response", END)
    return builder.compile()


def _route_turn_node(state: ShoppingState) -> dict[str, object]:
    mode = "non_shopping" if state["turn_query"].intent == "non_shopping" else None
    return {"response_mode": mode} if mode is not None else {}
