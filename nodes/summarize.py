from pathlib import Path
from dotenv import load_dotenv 
from langchain_google_genai import ChatGoogleGenerativeAI


path = Path(__file__).parents[1] / ".env"
load_dotenv(path)


llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0
)


def summarize(state):

    query = state["query"]

    events = state["events"]

    prompt = f"""
You are a K12 intelligence analyst.

User request:

{query}

The following K12 findings were collected:

{events}

Create a concise intelligence report.

For each relevant finding include:

- Title
- Organization
- Event type
- Date
- Location
- Why it matters
- Source URL

Do not invent information.

If information is unavailable, say "Not available".
"""

    response = llm.invoke(prompt)

    return {
        "final_answer": response.content
    }