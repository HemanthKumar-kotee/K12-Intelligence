
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from dotenv import load_dotenv
import os

from state import K12State
from nodes.search import search_k12
from nodes.filter import filter_results
from nodes.extract import extract_events
from nodes.response import format_response
from nodes.message import remove_message

load_dotenv()

CHECKPOINTER_URL = os.getenv("CHECKPOINTER_URL")


def build_graph(checkpointer: PostgresSaver):
    builder = StateGraph(K12State)

    builder.add_node("search_k12", search_k12)
    builder.add_node("filter_results", filter_results)
    builder.add_node("extract_events", extract_events)
    builder.add_node("format_response", format_response)
    builder.add_node("remove_message", remove_message)

    builder.add_edge(START, "search_k12")
    builder.add_edge("search_k12", "filter_results")
    builder.add_edge("filter_results", "extract_events")
    builder.add_edge("extract_events", "format_response")
    builder.add_edge("format_response", "remove_message")
    builder.add_edge("remove_message", END)

    return builder.compile(checkpointer=checkpointer)
