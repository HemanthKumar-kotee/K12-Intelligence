from dotenv import load_dotenv 
from pathlib import Path 
from langchain_google_genai import ChatGoogleGenerativeAI

from models.schemas import K12Event


path = Path(__file__).parents[1] / ".env"
load_dotenv(path)

llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0
)

extractor = llm.with_structured_output(
    K12Event
)


def extract_events(state):

    results = state["search_results"]

    events = []

    for result in results:

        content = result.get("content", "")

        url = result.get("url", "")

        if not content:
            continue

        prompt = f"""
You are a K12 intelligence extraction system.

Determine whether the following web result contains
useful information about a K12 event, partnership,
announcement, conference, organization activity, or
other relevant K12 development.

If it is relevant, extract the information.

If it is not relevant, do not create an event.

URL:
{url}

CONTENT:
{content}
"""

        try:

            event = extractor.invoke(prompt)

            event_data = event.model_dump()

            event_data["source_url"] = url

            events.append(event_data)

        except Exception:
            continue

    return {
        "events": events
    }