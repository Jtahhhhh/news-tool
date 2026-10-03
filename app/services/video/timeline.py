"""Pure metadata conversion; never cuts or concatenates source files."""
from copy import deepcopy
from .validator import validate


def duration(composition):
    return max((c['timeline_start']+c['duration'] for t in composition['tracks'] for c in t['clips']), default=0)


def from_scenes(scenes):
    tracks = [dict(id=i, type=t, name=n, clips=[]) for i,t,n in (
        ('video-main','video','Video 1'), ('video-overlay','video','Video 2'),
        ('original-audio','audio','Original audio'), ('voice','audio','Voice'),
        ('subtitle','subtitle','Subtitle'), ('text','text','Text'))]
    cursor = 0
    for i,s in enumerate(scenes):
        d = float(s['duration'])
        base = dict(timeline_start=float(s.get('start',cursor)), duration=d, source_start=0, source_end=d)
        if s.get('asset_id'):
            crop = s.get('crop') or {}
            tracks[0]['clips'].append(dict(base, id=f'video-{i}', asset_id=s['asset_id'], muted=True,
                crop=dict(x=crop.get('x',.5), y=crop.get('y',.5), zoom=crop.get('scale',1))))
        if s.get('audio_id'):
            tracks[3]['clips'].append(dict(base, id=f'voice-{i}', audio_id=s['audio_id'], name=f'Scene {s.get("scene_id",i+1)}'))
        tracks[4]['clips'].append(dict(base, id=f'sub-{i}', text=s.get('narration',''),
                                      transform=dict(y=.32,width=.9,height=.18)))
        if s.get('on_screen_text'):
            tracks[5]['clips'].append(dict(base, id=f'text-{i}', text=s['on_screen_text'],
                                          transform=dict(y=-.32,width=.9,height=.2)))
        cursor = base['timeline_start']+d
    return validate(dict(version=1, tracks=tracks))


def migrate(value):
    return validate(value) if 'tracks' in value or 'version' in value or 'schema_version' in value else from_scenes(value.get('scenes',[]))


def split_clip(clip, time, new_id):
    offset = time-clip['timeline_start']
    if offset < 1/30 or clip['duration']-offset < 1/30:
        raise ValueError('Split must leave at least one frame on both sides')
    if clip.get('transition') and offset<=clip['transition']['duration']:
        raise ValueError('Split must follow the incoming transition')
    a,b = deepcopy(clip),deepcopy(clip)
    a['animation_duration']=b['animation_duration']=clip.get('animation_duration') or clip['duration']
    b['animation_offset']=clip.get('animation_offset',0)+offset
    b['transition']=None
    source = clip['source_start']+offset*clip.get('speed',1)
    a.update(duration=offset, source_end=source, fade_out=0)
    a['fade_in'] = min(a.get('fade_in',0),offset)
    b.update(id=new_id, timeline_start=time, duration=clip['duration']-offset, source_start=source, fade_in=0)
    b['fade_out'] = min(b.get('fade_out',0),b['duration'])
    return a,b
