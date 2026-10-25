"""Host-facing models. Tool inputs cannot select files, databases, goals or credentials."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator,field_validator,field_serializer
from info_agent.text_evidence import clean_terms


class Contract(BaseModel):
    model_config=ConfigDict(extra='forbid')


class Scoped(Contract):
    profile_id:str=Field(min_length=1,max_length=100)


class TimeRange(Contract):
    start:str|None=Field(None,max_length=64)
    end:str|None=Field(None,max_length=64)
    period:Literal['today']|None=None
    timezone:str=Field('Asia/Shanghai',max_length=80)

    @model_validator(mode='after')
    def exclusive(self):
        if self.period and (self.start or self.end):raise ValueError('period conflicts with bounds')
        return self


class SourceSelector(Contract):
    kind:Literal['keyword_search','up_uploads','following_videos','up_dynamics','following_dynamics','temporary_keyword']
    key:str|None=Field(None,max_length=100)


class SearchItemsRequest(Scoped):
    terms:list[str]=Field(default_factory=list,max_length=8)
    _terms=field_validator('terms',mode='before')(clean_terms)
    time_range:TimeRange=Field(default_factory=TimeRange)
    sources:list[SourceSelector]=Field(default_factory=list,max_length=40)
    content_types:list[Literal['video','text','image','article','audio','live','unknown']]=Field(default_factory=list,max_length=7)
    limit:int=Field(20,ge=1,le=100)
    page:int=Field(1,ge=1,le=10000)
    collection_mode:Literal['existing_only','temporary_keywords']='existing_only'
    temporary_keywords:list[str]=Field(default_factory=list,max_length=5)
    max_duration_seconds:int|None=Field(None,gt=0)
    timeout_seconds:float|None=Field(None,gt=0,le=120)
    request_id:str|None=Field(None,min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_.:-]+$')
    snapshot_version:str|None=Field(None,max_length=64)


class QueryUpdatesRequest(SearchItemsRequest):
    question:str=Field(min_length=1,max_length=2000)
    limit:int=Field(8,ge=1,le=8)
    page:Literal[1]=1


class GetItemRequest(Scoped):
    item_id:str=Field(min_length=1,max_length=150)
    max_body_chars:int=Field(4000,ge=1,le=20000)
    sources:list[SourceSelector]=Field(default_factory=list,max_length=40)


class SourceStatusRequest(Scoped):
    include_disabled:bool=False


class PreferencesRequest(Scoped):pass


class UpdatePreferencesRequest(Scoped):
    expected_version:str=Field(min_length=1,max_length=100)
    request_id:str=Field(min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_.:-]+$')
    add_keywords:list[str]=Field(default_factory=list,max_length=5)
    remove_keywords:list[str]=Field(default_factory=list,max_length=5)
    interests:str|None=Field(None,max_length=10000)
    interest_mode:Literal['same_goal','switch_goal']|None=None

    @model_validator(mode='after')
    def mode(self):
        if self.interests is not None and self.interest_mode is None:raise ValueError('interest_mode required')
        return self


class UndoPreferenceChangeRequest(Scoped):
    change_id:str=Field(min_length=1,max_length=100)


class SourceEvidence(Contract):
    kind:str
    key:str
    label:str
    collected_at:str|None=None
    published_at:str|None=None
    dynamic_id:str|None=None
    query_id:str|None=None
    temporary:bool=False
    url:str|None=None
    author:str|None=None
    author_uid:str|None=None


class TextSpan(Contract):
    id:str
    field:str
    start:int
    end:int
    text:str
    source:SourceEvidence


class BodySpan(Contract):
    field:str
    start:int
    end:int
    body_start:int
    body_end:int
    source:SourceEvidence


class Item(Contract):
    id:str
    title:str
    snippet:str
    author:str
    author_uid:str|None=None
    published_at:str|None=None
    matched_published_at:str|None=None
    time_basis:Literal['publication','missing']='publication'
    url:str
    content_type:str
    duration_seconds:int|None=None
    sources:list[SourceEvidence]
    dynamic_contexts:list[dict]=Field(default_factory=list)
    snippet_truncated:bool=False
    source_text_limited:bool=False
    sources_truncated:bool=False
    contexts_truncated:bool=False
    snippet_evidence:TextSpan|None=None


class ItemDetail(Item):
    body:str
    truncated:bool
    body_chars:int
    images:list[str]=Field(default_factory=list)
    images_analyzed:Literal[False]=False
    body_spans:list[BodySpan]=Field(default_factory=list)


class SourceStatus(Contract):
    kind:str
    key:str
    label:str
    availability:Literal['ready','partial','error','unknown','disabled']
    last_checked_at:str|None=None
    last_success_at:str|None=None
    retained_count:int=0
    error_code:str|None=None
    error_message:str|None=None
    temporary:bool=False


class Coverage(Contract):
    scope:Literal['retained_allowed_data']='retained_allowed_data'
    timezone:str='Asia/Shanghai'
    start:str|None=None
    end:str|None=None
    boundary:Literal['start_inclusive_end_exclusive']='start_inclusive_end_exclusive'
    as_of:str
    limited_sampling:Literal[True]=True
    insufficient:bool=False
    reasons:list[str]=Field(default_factory=list)
    missing_publication_count:int=0
    sources:list[SourceStatus]=Field(default_factory=list)
    sources_truncated:bool=False


class Usage(Contract):
    cache_hit:bool=False
    replayed:bool=False
    logical_model_calls:int=0
    http_attempts:int|None=0
    rss_requests:int=0
    actual_requests:int|None=0
    elapsed_seconds:float=0
    tokens:dict|None=None
    origin_metrics:dict|None=None

    @field_serializer('elapsed_seconds')
    def elapsed_precision(self,value):return round(value,6)


class SearchResult(Contract):
    request_id:str
    outcome:Literal['found','no_match','collection_failed']
    items:list[Item]=Field(default_factory=list)
    total_matches:int=0
    page:int=1
    has_more:bool=False
    truncated:bool=False
    retrieval_terms:list[str]=Field(default_factory=list)
    coverage:Coverage
    metrics:Usage=Field(default_factory=Usage)
    collection_failures:list[dict]=Field(default_factory=list)
    temporary_video_search_only:bool=False
    snapshot_version:str=''
    next_page:int|None=None


class Evidence(Contract):
    item:Item
    reason:str
    quote:str
    quote_verified:Literal[True]=True
    quote_evidence:TextSpan|None=None


class QueryResult(Contract):
    request_id:str
    outcome:Literal['found','no_match','insufficient_evidence','collection_failed','model_failed']
    evidence:list[Evidence]=Field(default_factory=list)
    total_matches:int=0
    candidates_sent:int=0
    input_truncated:bool=False
    duplicates_removed:int=Field(0,ge=0)
    output_truncated:bool=False
    evidence_omitted_count:int=Field(0,ge=0)
    retrieval_terms:list[str]=Field(default_factory=list)
    coverage:Coverage
    metrics:Usage=Field(default_factory=Usage)
    collection_failures:list[dict]=Field(default_factory=list)
    temporary_video_search_only:bool=False
    error_code:str|None=None
    error_message:str|None=None
    error_details:dict|None=None


class EvidenceChoice(Contract):
    item_ref:str=Field(min_length=1,max_length=4)
    evidence_ref:str=Field(min_length=1,max_length=6)
    reason:str=Field(min_length=1,max_length=200)


class EvidenceSelection(Contract):
    # Unique-result count is checked after all raw rows have passed validation.
    # Field sizes, total raw text budget and the model token cap remain bounded.
    evidence:list[EvidenceChoice]=Field(default_factory=list)


class PreferenceView(Contract):
    profile_id:str
    version:str
    keywords:list[str]
    interests:str
    goal_id:str
    learning_context:str
    keyword_search_enabled:bool


class PreferenceChange(Contract):
    change_id:str
    before:PreferenceView
    after:PreferenceView
    changed_fields:list[str]
    needs_collection:bool=False
    data_ready:bool=False
    next_action:Literal['none','refresh_collection','enable_keyword_search']='none'
    promoted_items:int=0
    replayed:bool=False
    undo_of:str|None=None


class SourceStatusResult(Contract):
    profile_id:str
    sources:list[SourceStatus]
    truncated:bool=False
    limited_sampling:Literal[True]=True
