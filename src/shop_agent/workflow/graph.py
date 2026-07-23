from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from shop_agent.models.state import ShoppingState
from shop_agent.workflow.dependencies import WorkflowDependencies
from shop_agent.workflow.nodes import (
    build_nodes,
    route_intent,
    route_retrieval,
    route_validation,
)


def build_graph(dependencies: WorkflowDependencies) -> CompiledStateGraph:
    nodes = build_nodes(dependencies)
    builder = StateGraph(ShoppingState)
    builder.add_node("structure_intent", nodes.structure_intent)
    builder.add_node("retrieve_chunks", nodes.retrieve_chunks)
    builder.add_node("aggregate_products", nodes.aggregate_products)
    builder.add_node("semantic_rerank", nodes.semantic_rerank)
    builder.add_node("validate_evidence", nodes.validate_evidence)
    builder.add_node("decide_candidates", nodes.decide_candidates)
    builder.add_node("generate_response", nodes.generate_response)

    builder.add_edge(START, "structure_intent")
    builder.add_conditional_edges(
        "structure_intent",
        route_intent,
        {
            "product_search": "retrieve_chunks",
            "non_shopping": "generate_response",
        },
    )
    builder.add_conditional_edges(
        "retrieve_chunks",
        route_retrieval,
        {"has_results": "aggregate_products", "no_results": "generate_response"},
    )
    builder.add_edge("aggregate_products", "semantic_rerank")
    builder.add_edge("semantic_rerank", "validate_evidence")
    builder.add_conditional_edges(
        "validate_evidence",
        route_validation,
        {"has_candidates": "decide_candidates", "no_candidates": "generate_response"},
    )
    builder.add_edge("decide_candidates", "generate_response")
    builder.add_edge("generate_response", END)
    return builder.compile()
