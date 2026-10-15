
from state import K12State


def format_response(state: K12State) -> dict:
    events = state.get("events", [])

    if not events:
        return {
            "response": (
                "I couldn't find any relevant K12 events "
                "in the available search results."
            )
        }

    lines = [
        f"Found **{len(events)} relevant K12 events**.",
        "",
    ]

    for index, event in enumerate(events, start=1):
        title = event.get("title", "Untitled event")
        lines.append(f"### {index}. {title}")

        organization = event.get("organization")
        event_type = event.get("event_type")
        date = event.get("date")
        location = event.get("location")
        description = event.get("description")
        source_url = event.get("source_url")

        if organization:
            lines.append(f"- **Organization:** {organization}")

        if event_type:
            lines.append(f"- **Type:** {event_type}")

        if date:
            lines.append(f"- **Date:** {date}")

        if location:
            lines.append(f"- **Location:** {location}")

        if description:
            lines.append(f"- **Description:** {description}")

        if source_url:
            lines.append(f"- **Source:** [View source]({source_url})")

        lines.append("")

    return {"response": "\n".join(lines).strip()}
