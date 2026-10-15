
from tools.web_search import search_web


def search_k12(state):

    query = state["query"]

    results = search_web(query)

    return {
        "search_results": results
    }