

K12_KEYWORDS = [
    "school",
    "k12",
    "education",
    "student",
    "teacher",
    "curriculum",
    "learning",
    "academy",
    "district",
    "school district"
]


def filter_results(state):

    results = state["search_results"]

    filtered = []

    for result in results:

        title = result.get("title", "")
        content = result.get("content", "")

        text = f"{title} {content}".lower()

        if any(keyword in text for keyword in K12_KEYWORDS):
            filtered.append({
                "title": title,
                "content": content[:3000],
                "url": result.get("url")
            })

    return {
        "search_results": filtered[:8]
    }