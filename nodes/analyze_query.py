from dotenv import load_dotenv 
from pathlib import Path 
from langchain_google_genai import ChatGoogleGenerativeAI

from models.schemas import SearchParams

path = Path(__file__).parents[1] / ".env"
load_dotenv(path)


llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0
)


structured_llm = llm.with_structured_output(SearchParams)


def analyze_query(state):

    query = state["query"]

    prompt = f"""
            You are a K12 intelligence query analyzer.

            Analyze the user's request and convert it into search parameters.

            The domain is always K12 education.

            User request:

            {query}

            Identify:

            - topic
            - event type
            - location
            - time range
            - useful search keywords
    """

    result = structured_llm.invoke(prompt)

    return {
        "search_params": result.model_dump()
    }