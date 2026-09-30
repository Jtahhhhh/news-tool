from typing import Literal
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class Source(Strict):
    source_id: str = Field(min_length=1)
    url: HttpUrl
    title: str = Field(min_length=1)
    published_at: datetime | None
    text: str = Field(min_length=1, max_length=20000)

class Input(Strict):
    schema_version: Literal["1.0"]
    story_id: str = Field(min_length=1)
    language: Literal["vi"]
    target_seconds: int = Field(ge=15, le=90)
    tone: Literal["neutral", "conversational"]
    sources: list[Source] = Field(min_length=1, max_length=5)
    @model_validator(mode="after")
    def unique_sources(self):
        ids = [s.source_id for s in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate source_id")
        for s in self.sources:
            if s.published_at and s.published_at.tzinfo is None:
                raise ValueError("published_at requires timezone")
        return self

class Evidence(Strict):
    source_id: str = Field(min_length=1)
    quote: str = Field(min_length=1, max_length=500)

class Claim(Strict):
    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)

class Scene(Strict):
    scene_id: int = Field(ge=1)
    seconds: int = Field(ge=1, le=90)
    narration: str = Field(min_length=1)
    on_screen_text: str
    visual_brief: str
    claim_ids: list[str] = Field(min_length=1)

class Output(Strict):
    schema_version: Literal["1.0"]
    story_id: str
    decision: Literal["draft", "insufficient_evidence"]
    reason: str
    title: str
    hook: str
    caption: str
    claims: list[Claim]
    scenes: list[Scene]
    warnings: list[str]
    @model_validator(mode="after")
    def consistent(self):
        if self.decision == "draft":
            if not self.title or not self.hook or not self.caption or not self.claims or not self.scenes:
                raise ValueError("draft requires title, hook, caption, claims and scenes")
            if self.scenes[0].narration != self.hook:
                raise ValueError("hook must equal first scene narration; do not read hook twice")
        elif self.scenes or self.claims or self.title or self.hook or self.caption or not self.reason:
            raise ValueError("insufficient evidence requires reason and empty publishable content")
        return self

def validate_output(data: Input, raw: str) -> Output:
    out = Output.model_validate_json(raw)
    if out.story_id != data.story_id:
        raise ValueError("story_id mismatch")
    sources = {s.source_id:s for s in data.sources}
    ids = [c.claim_id for c in out.claims]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate claim_id")
    for claim in out.claims:
        for ev in claim.evidence:
            if ev.source_id not in sources:
                raise ValueError("unknown source_id")
            if ev.quote not in sources[ev.source_id].text:
                raise ValueError("evidence quote not found verbatim in source")
    if [s.scene_id for s in out.scenes] != list(range(1,len(out.scenes)+1)):
        raise ValueError("scene_id must be contiguous from 1")
    for scene in out.scenes:
        if not set(scene.claim_ids) <= set(ids):
            raise ValueError("unknown claim_id")
    if out.decision == "draft" and sum(s.seconds for s in out.scenes)>data.target_seconds+3:
        raise ValueError("planned duration exceeds target by more than 3 seconds")
    from .grounding import validate_grounding
    validate_grounding(data, out)
    return out
