
from typing import Any
from langgraph.graph import MessagesState


class K12State(MessagesState, total=False):
    
    """State shared across the K12 research graph."""

    query: str
    search_results: list[dict[str, Any]]
    events: list[dict[str, Any]]
    response: str
    file_context: str
