from langgraph.graph import StateGraph, START, END

from state import K12State

from nodes.analyze_query import analyze_query
from nodes.search import search_k12
from nodes.extract import extract_events
from nodes.summarize import summarize


builder = StateGraph(K12State)


builder.add_node(
    "analyze_query",
    analyze_query
)

builder.add_node(
    "search_k12",
    search_k12
)

builder.add_node(
    "extract_events",
    extract_events
)

builder.add_node(
    "summarize",
    summarize
)


builder.add_edge(
    START,
    "analyze_query"
)

builder.add_edge(
    "analyze_query",
    "search_k12"
)

builder.add_edge(
    "search_k12",
    "extract_events"
)

builder.add_edge(
    "extract_events",
    "summarize"
)

builder.add_edge(
    "summarize",
    END
)


graph = builder.compile()