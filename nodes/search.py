from tools.web_search import search_web


def search_k12(state):

    params = state["search_params"]

    keywords = " ".join(params.get("keywords", []))

    location = params.get("location")

    time_range = params.get("time_range")

    query = f"""
            K12 education
                {keywords}
                {location or ""}
                {time_range or ""}
            events partnerships announcements
    """

    results = search_web(query)

    return {
        "search_results": results
    }