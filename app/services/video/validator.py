"""Bounded, versioned composition format shared by drafts and render jobs."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Canvas(Strict):
    width: int = Field(1080,ge=320,le=3840)
    height: int = Field(1920,ge=320,le=3840)
    fps: Literal[30] = 30

    @model_validator(mode='after')
    def even_dimensions(self):
        if self.width%2 or self.height%2: raise ValueError('Output dimensions must be even')
        return self


class Target(Canvas):
    aspect_ratio: str = '9:16'
    safe_area: bool = True
    safe_area_preset: Literal['tiktok','reels','shorts','none'] = 'tiktok'
    background: str = Field('#000000',pattern=r'^#[0-9a-fA-F]{6}$')


class Effect(Strict):
    type: Literal['fade','slide_left','slide_right','slide_up','slide_down','zoom','pop','typewriter'] = 'fade'
    duration: float = Field(.3,gt=0,le=.8)
    easing: Literal['linear','ease-in','ease-out','ease-in-out'] = 'ease-in-out'


class Animation(Strict):
    enter: Effect | None = Field(None,alias='in')
    exit: Effect | None = Field(None,alias='out')
    loop: Literal['static','zoom_in','zoom_out','pan_left','pan_right','pan_up','pan_down'] = 'static'

    @model_validator(mode='after')
    def typewriter_in_only(self):
        if self.exit and self.exit.type=='typewriter': raise ValueError('Typewriter is an entrance effect')
        return self


class Keyframe(Strict):
    time: float = Field(ge=0,le=7200)
    x: float | None = Field(None,ge=-2,le=2)
    y: float | None = Field(None,ge=-2,le=2)
    scale: float | None = Field(None,ge=.05,le=1.35)
    rotation: float | None = Field(None,ge=-10,le=10)
    opacity: float | None = Field(None,ge=0,le=1)
    volume: float | None = Field(None,ge=0,le=1)
    crop_x: float | None = Field(None,ge=0,le=1)
    crop_y: float | None = Field(None,ge=0,le=1)
    easing: Literal['linear','ease-in','ease-out','ease-in-out'] = 'linear'


class Transition(Strict):
    type: Literal['crossfade','fade_black','slide','push','zoom']
    duration: float = Field(.4,gt=0,le=1)
    from_clip_id: str = Field(min_length=1,max_length=100)


class Word(Strict):
    text: str = Field(max_length=100)
    start: float = Field(ge=0,le=7200)
    end: float = Field(gt=0,le=7200)

    @model_validator(mode='after')
    def ordered(self):
        if self.end<=self.start: raise ValueError('Word end must follow start')
        return self


class Transform(Strict):
    # x/y are the center relative to the canvas center. Size is canvas-relative.
    x: float = Field(0, ge=-2, le=2)
    y: float = Field(0, ge=-2, le=2)
    width: float = Field(1, ge=.01, le=2)
    height: float = Field(1, ge=.01, le=2)
    scale: float = Field(1, ge=.05, le=4)
    rotation: float = Field(0, ge=-360, le=360)
    opacity: float = Field(1, ge=0, le=1)


class Crop(Strict):
    mode: Literal['fill','fit','crop','blur_background','smart_crop','stretch'] = 'fill'
    x: float = Field(.5, ge=0, le=1)
    y: float = Field(.5, ge=0, le=1)
    zoom: float = Field(1, ge=1, le=4)
    manual: bool = False
    background: str = Field('#000000',pattern=r'^#[0-9a-fA-F]{6}$')
    background_asset_id: int | None = Field(None,gt=0)
    blur: float = Field(30,ge=20,le=40)
    darken: float = Field(.25,ge=0,le=.8)


class TextStyle(Strict):
    font: Literal['DejaVu Sans', 'DejaVu Serif', 'DejaVu Sans Mono'] = 'DejaVu Sans'
    size: float = Field(52, ge=12, le=180)
    color: str = Field('#ffffff', pattern=r'^#[0-9a-fA-F]{6}$')
    stroke: float = Field(2, ge=0, le=10)
    shadow: float = Field(0, ge=0, le=10)
    background: bool = False
    highlight: str = Field('#ffe66d',pattern=r'^#[0-9a-fA-F]{6}$')


class Clip(Strict):
    id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    asset_id: int | None = Field(None, gt=0)
    audio_id: int | None = Field(None, gt=0)
    name: str = Field('', max_length=255)
    text: str = Field('', max_length=4000)
    timeline_start: float = Field(0, ge=0, le=7200)
    duration: float = Field(5, ge=1/30, le=7200)
    source_start: float = Field(0, ge=0, le=86400)
    source_end: float = Field(5, gt=0, le=86400)
    speed: float = Field(1, ge=.5, le=2)
    transform: Transform = Field(default_factory=Transform)
    crop: Crop = Field(default_factory=Crop)
    style: TextStyle = Field(default_factory=TextStyle)
    volume: float = Field(1, ge=0, le=1)
    muted: bool = False
    fade_in: float = Field(0, ge=0, le=60)
    fade_out: float = Field(0, ge=0, le=60)
    animation: Animation = Field(default_factory=Animation)
    keyframes: list[Keyframe] = Field(default_factory=list,max_length=300)
    transition: Transition | None = None
    animation_offset: float = Field(0,ge=0,le=7200)
    animation_duration: float = Field(0,ge=0,le=7200)
    words: list[Word] = Field(default_factory=list,max_length=1000)
    subtitle_animation: Literal['static','fade','pop','word_highlight'] = 'static'

    @model_validator(mode='after')
    def timing(self):
        if abs(self.source_end-self.source_start-self.duration*self.speed) > .002:
            raise ValueError('Source range must equal duration × speed')
        if self.timeline_start+self.duration > 7200:
            raise ValueError('Composition must be at most 2 hours')
        if self.fade_in+self.fade_out > self.duration+.001:
            raise ValueError('Audio fades exceed clip duration')
        if self.transform.width*self.transform.scale > 4 or self.transform.height*self.transform.scale > 4:
            raise ValueError('Transformed layer exceeds maximum dimensions')
        times=[k.time for k in self.keyframes]
        if times!=sorted(set(times)): raise ValueError('Keyframes must have unique ascending times')
        if any(a.end>b.start for a,b in zip(self.words,self.words[1:])): raise ValueError('Word timings overlap')
        return self


class Track(Strict):
    id: str = Field(min_length=1, max_length=100)
    type: Literal['video', 'audio', 'subtitle', 'text']
    name: str = Field('', max_length=100)
    clips: list[Clip] = Field(default_factory=list, max_length=300)

    @model_validator(mode='after')
    def media_types(self):
        for c in self.clips:
            if self.type == 'video' and (not c.asset_id or c.audio_id):
                raise ValueError('Video tracks require a media asset')
            if self.type == 'audio' and bool(c.asset_id) == bool(c.audio_id):
                raise ValueError('Audio tracks require exactly one audio source')
            if self.type in ('text', 'subtitle') and (c.asset_id or c.audio_id):
                raise ValueError('Text tracks cannot contain media references')
        rows = sorted(self.clips, key=lambda c: c.timeline_start)
        for i,b in enumerate(rows):
            a=rows[i-1] if i else None
            overlap=(a.timeline_start+a.duration-b.timeline_start) if a else 0
            if b.transition:
                tr=b.transition
                if self.type!='video' or not a or tr.from_clip_id!=a.id or abs(overlap-tr.duration)>.002 or tr.duration>=min(a.duration,b.duration):
                    raise ValueError('Transition requires an exact overlap with the preceding video clip')
                if i>1 and rows[i-2].timeline_start+rows[i-2].duration>b.timeline_start:
                    raise ValueError('Three-way transitions are not supported')
            elif overlap>.001: raise ValueError('Clips overlap without a transition')
        return self


class Composition(Strict):
    version: Literal[2] = 2
    schema_version: Literal[2] = 2
    canvas: Canvas = Field(default_factory=Canvas)
    target: Target = Field(default_factory=Target)
    animation_preset: Literal['NONE','SUBTLE','MODERN','STORY','NEWS','DRAMA'] = 'NONE'
    tracks: list[Track] = Field(min_length=1, max_length=16)

    @model_validator(mode='before')
    @classmethod
    def migrate_v1(cls,value):
        value=dict(value)
        if value.get('version',1)==1 and value.get('schema_version',1)==1:
            value.update(version=2,schema_version=2)
        target=dict(value.get('target') or value.get('canvas') or {})
        import math
        w,h=target.get('width',1080),target.get('height',1920)
        if not isinstance(w,int) or not isinstance(h,int) or w<=0 or h<=0:
            raise ValueError('Target dimensions must be positive integers')
        divisor=math.gcd(w,h)
        target['aspect_ratio']=f'{w//divisor}:{h//divisor}'
        value['target']=target
        value['canvas']={k:target.get(k,d) for k,d in [('width',1080),('height',1920),('fps',30)]}
        return value

    @model_validator(mode='after')
    def unique_ids(self):
        tracks = [t.id for t in self.tracks]
        clips = [c.id for t in self.tracks for c in t.clips]
        if len(set(tracks)) != len(tracks) or len(set(clips)) != len(clips):
            raise ValueError('Track and clip IDs must be unique')
        if len(clips) > 500:
            raise ValueError('At most 500 clips per project')
        return self


def validate(value):
    return Composition.model_validate(value).model_dump(by_alias=True)
