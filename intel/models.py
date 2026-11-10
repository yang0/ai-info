import hashlib
import json
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Settings(BaseModel):
    mode: Literal['demo', 'opencli', 'agent_reach'] = 'opencli'
    queries: list[str] = Field(default_factory=lambda: [
        '("AI agent" OR "coding agent" OR "agentic") -filter:nativeretweets',
        '("open source" OR "model release") (AI OR LLM) -filter:nativeretweets',
        '(OpenAI OR Anthropic OR DeepSeek OR Qwen OR Gemini) -filter:nativeretweets',
    ], min_length=1, max_length=8)
    interval_seconds: int = Field(default=300, ge=60, le=86400)
    auto_scan: bool = False
    result_limit: int = Field(default=50, ge=5, le=100)
    min_authors: int = Field(default=3, ge=2, le=30)
    llm_enabled: bool = False

    def dataset_key(self):
        # Changed query scopes must not reuse posts or snapshots from an old mission.
        scope = [self.mode, sorted(self.queries), self.min_authors, self.llm_enabled]
        return self.mode + ':' + hashlib.sha256(json.dumps(scope).encode()).hexdigest()[:16]

    @field_validator('queries')
    @classmethod
    def queries_valid(cls, value):
        value = list(dict.fromkeys(q.strip() for q in value))
        if any(not q or len(q) > 512 or q.startswith('-') or '\x00' in q for q in value):
            raise ValueError('每条查询须为 1–512 字符，不能以减号开头')
        return value


class Post(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=20000)
    author: str = Field(min_length=1, max_length=200)
    created_at: datetime
    url: str
    likes: int = Field(default=0, ge=0)
    reposts: int = Field(default=0, ge=0)
    replies: int = Field(default=0, ge=0)
    query: str = ''

    @field_validator('created_at')
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError('时间戳必须包含时区')
        return value.astimezone(timezone.utc)
