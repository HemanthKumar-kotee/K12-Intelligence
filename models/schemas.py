from pydantic import BaseModel, Field
from typing import Optional


class SearchParams(BaseModel):

    domain: str = "K12"

    topic: Optional[str] = None

    event_type: Optional[str] = None

    location: Optional[str] = None

    time_range: Optional[str] = None

    keywords: list[str] = Field(default_factory=list)


class K12Event(BaseModel):

    title: str

    organization: Optional[str] = None

    event_type: Optional[str] = None

    date: Optional[str] = None

    location: Optional[str] = None

    description: Optional[str] = None

    source_url: str