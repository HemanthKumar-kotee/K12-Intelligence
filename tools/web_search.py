import os
import time 
import requests
import logging 

from pathlib import Path 
from dotenv import load_dotenv
from tavily import TavilyClient


path = Path(__file__).parents[1] / ".env"
load_dotenv(path)

logger = logging.getLogger(__name__)

tavily = TavilyClient(
    api_key=os.getenv("TAVILY_API_KEY")
)


def search_web(query: str):
    
    '''Search the web for relevant information matching the query'''
    
    last_error = None
    logger.info("Calling tavily search")

    for attempt in range(3):
        try:
            response = tavily.search(
                query=query,
                search_depth="advanced",
                time_range='month'
            )

            logger.info("Tavily search completed")
            return response["results"]
        
        except requests.exceptions.ConnectionError as exc:
            last_error = exc

            if attempt < 2:
                time.sleep(1 * (2 ** attempt))

    logger.exception("Tavily web request failed")
    raise RuntimeError(
        "Web search failed after 3 connection attempts"
    ) from last_error


if __name__ == "__main__":
    print(search_web('Find K12 partnership events in United States this month'))