import os

from pathlib import Path 
from dotenv import load_dotenv
from tavily import TavilyClient


path = Path(__file__).parents[1] / ".env"
load_dotenv(path)

tavily = TavilyClient(
    api_key=os.getenv("TAVILY_API_KEY")
)


def search_web(query: str):

    response = tavily.search(
        query=query,
        search_depth="advanced",
        max_results=10
    )

    return response["results"]


if __name__ == "__main__":

    print(search_web('Find K12 partnership events in United States this month'))