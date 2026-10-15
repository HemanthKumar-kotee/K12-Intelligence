
from typing import Optional
from pydantic import BaseModel, Field

from langchain_google_genai import ChatGoogleGenerativeAI

import logging

logger = logging.getLogger(__name__)

class K12Event(BaseModel):

    title: str

    organization: Optional[str] = None

    event_type: Optional[str] = None

    date: Optional[str] = None

    location: Optional[str] = None

    description: Optional[str] = None

    source_url: str


class K12Events(BaseModel):

    events: list[K12Event] = Field(default_factory=list)


llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0,
)

extractor = llm.with_structured_output(K12Events)


def extract_events(state):

    results = state.get("search_results", [])

    if not results:
        return {"events": []}

    documents = []

    for i, result in enumerate(results):

        documents.append(
            f"""
                RESULT {i + 1}

                TITLE:
                {result["title"]}

                URL:
                {result["url"]}

                CONTENT:
                {result["content"]}
            """
        )

    combined = "\n".join(documents)

    prompt = f"""
        Extract relevant K12 events and developments from these
        web search results.

        Only include information explicitly present in the results.

        Do not invent information.

        Relevant items can include:
        - K12 partnerships
        - school partnerships
        - education conferences
        - school events
        - education programs
        - announcements
        - K12 organizations
        - important K12 developments

        WEB RESULTS:

        {combined}
    """

    try:
        response = extractor.invoke(prompt)

        logger.info("Events extracted successfully")
        return {
            "events": [
                event.model_dump()
                for event in response.events
            ]
        }
    except Exception:
        logger.exception("Event extraction failed")
        raise