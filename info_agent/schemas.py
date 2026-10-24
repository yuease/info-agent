"""Shared contracts; legacy profiles and Python entry points remain supported."""
import hashlib
import json
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Learning(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = True
    half_life_days: float = Field(60, gt=0)
    max_score_adjustment: float = Field(5, ge=0, le=5)
    max_rank_movement: int = Field(1, ge=0, le=1)
    activation_min_videos: int = Field(3, ge=2)
    activation_min_days: int = Field(3, ge=2)
    activation_net: float = Field(1.5, gt=0)
    activation_consistency: float = Field(.7, gt=.5, le=1)
    daily_cap: float = Field(1, gt=0, le=1)
    max_tags: int = Field(5, ge=1, le=20)
    duration_buckets: list[int] = Field(default_factory=lambda: [300, 1200, 3600])

    @field_validator('duration_buckets')
    @classmethod
    def buckets(cls, v):
        if v != sorted(set(v)) or any(x <= 0 for x in v):
            raise ValueError('duration_buckets must be positive and increasing')
        return v


class Profile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: int = Field(2, ge=1, le=2)
    profile_id: str = Field('default', min_length=1, max_length=100)
    rsshub_url: str
    keywords: list[str] = Field(min_length=1, max_length=5)
    interests: str
    max_picks: int = Field(5, ge=1, le=10)
    max_candidates: int = Field(60, ge=1, le=200)
    max_duration_seconds: int | None = Field(None, gt=0)
    exploration_ratio: float = Field(.2, ge=0, le=1)
    history_cooldown_days: float = Field(14, ge=0)
    snooze_days: float = Field(3, ge=0)
    learning: Learning = Field(default_factory=Learning)

    @field_validator('rsshub_url')
    @classmethod
    def url(cls, v):
        u = urlsplit(v)
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('invalid RSSHub URL')
        return v.rstrip('/')

    @field_validator('keywords')
    @classmethod
    def keywords_valid(cls, v):
        v = list(dict.fromkeys(x.strip() for x in v))
        if not all(v):
            raise ValueError('empty keyword')
        return v

    @property
    def context(self):
        # Policy changes must not reinterpret old duration evidence.
        value = [self.profile_id, sorted(self.keywords), self.interests.strip(), self.max_duration_seconds,
                 self.learning.duration_buckets]
        return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


class Article(BaseModel):
    id: str
    title: str
    url: str
    author: str = '未知'
    published: str = '未知'
    published_at: str | None = None
    excerpt: str = ''
    duration_seconds: int | None = None
    tags: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    matched_keywords: list[str] = Field(default_factory=list)
    parse_warnings: list[str] = Field(default_factory=list)
    fetched_at: str | None = None


class Choice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    summary: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=300)
    action: str = Field(min_length=1, max_length=300)
    base_score: float = Field(50, ge=0, le=100, allow_inf_nan=False)
    eligible_regular: bool = True
    eligible_explore: bool = False
    exploration_reason: str = Field('', max_length=300)

    @model_validator(mode='after')
    def explore_reason(self):
        if self.eligible_explore and not self.exploration_reason.strip():
            raise ValueError('exploration requires evidence')
        return self


class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    picks: list[Choice] = Field(default_factory=list, max_length=30)
