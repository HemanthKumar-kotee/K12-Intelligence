from typing import TypedDict, List, Dict, Any


class K12State(TypedDict, total=False):
    
    query: str
    
    search_params: Dict[str, Any]
    
    search_results: List[Dict[str, Any]]
    
    events: List[Dict[str, Any]]