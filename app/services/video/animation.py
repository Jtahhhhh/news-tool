"""One animation definition evaluated numerically or compiled to FFmpeg expressions."""
import math


class Expr:
    def __init__(self,value): self.value=str(value)
    def __str__(self): return self.value
    def __add__(self,b): return Expr(f'({self}+{b})')
    def __radd__(self,b): return Expr(b)+self
    def __sub__(self,b): return Expr(f'({self}-{b})')
    def __rsub__(self,b): return Expr(b)-self
    def __mul__(self,b): return Expr(f'({self}*{b})')
    def __rmul__(self,b): return Expr(b)*self
    def __truediv__(self,b): return Expr(f'({self}/{b})')
    def __neg__(self): return Expr(f'(-{self})')


def call(name,*args):
    if any(isinstance(a,Expr) for a in args): return Expr(f'{name}('+','.join(map(str,args))+')')
    return {'min':min,'max':max,'sin':math.sin,'lt':lambda a,b:int(a<b),'if':lambda a,b,c:b if a else c}[name](*args)


def clamp(x): return call('min',1,call('max',0,x))


def ease(x,name):
    p=clamp(x)
    if name=='ease-in': return p*p
    if name=='ease-out': return 1-(1-p)*(1-p)
    if name=='ease-in-out': return p*p*(3-2*p)
    return p


def interpolate(keys,field,local,default):
    points=[k for k in keys if k.get(field) is not None]
    if not points: return default
    if points[0]['time']>0: points=[dict(time=0,easing='linear',**{field:default}),*points]
    value=points[-1][field]
    for a,b in reversed(list(zip(points,points[1:]))):
        p=ease((local-a['time'])/(b['time']-a['time']),b.get('easing','linear'))
        part=a[field]+(b[field]-a[field])*p
        value=call('if',call('lt',local,b['time']),part,value)
    return value


def state(clip,local,outgoing=None):
    t=clip['transform'];keys=clip.get('keyframes',[])
    time=local+clip.get('animation_offset',0)
    total=clip.get('animation_duration') or clip['duration']
    state={key:interpolate(keys,key,time,default) for key,default in (
        ('x',t['x']),('y',t['y']),('scale',1),('rotation',0),('opacity',1),('volume',1),
        ('crop_x',clip['crop']['x']),('crop_y',clip['crop']['y']))}
    motion=clip.get('animation',{}).get('loop','static');p=clamp(time/total)
    if motion=='zoom_in': state['scale']=state['scale']*(1+.15*p)
    if motion=='zoom_out': state['scale']=state['scale']*(1.15-.15*p)
    for name,axis,sign in [('pan_left','x',-1),('pan_right','x',1),('pan_up','y',-1),('pan_down','y',1)]:
        if motion==name: state[axis]=state[axis]+sign*.08*p
    animation=clip.get('animation',{})
    if clip.get('subtitle_animation') in ('fade','pop'):
        animation={**animation,'in':dict(type=clip['subtitle_animation'],duration=min(.3,total/2),easing='ease-out')}
    for direction in ('in','out'):
        effect=animation.get(direction)
        if not effect: continue
        progress=ease(time/effect['duration'] if direction=='in' else (total-time)/effect['duration'],effect.get('easing','linear'))
        typ=effect['type']
        if typ!='typewriter': state['opacity']=state['opacity']*progress
        if typ in ('zoom','pop'):
            factor=.85+.15*progress
            if typ=='pop': factor=factor+.12*call('sin',progress*math.pi)
            state['scale']=state['scale']*factor
        for name,axis,sign in [('slide_left','x',-1),('slide_right','x',1),('slide_up','y',-1),('slide_down','y',1)]:
            if typ==name: state[axis]=state[axis]+sign*.15*(1-progress)
    state['scale']=call('min',1.35,state['scale'])*t['scale']
    state['rotation']=state['rotation']+t['rotation']
    state['opacity']=state['opacity']*t['opacity']
    state['volume']=state['volume']*clip['volume']
    transition=clip.get('transition')
    if transition:
        p=clamp(local/transition['duration']);typ=transition['type']
        if typ in ('crossfade','zoom'): state['opacity']=state['opacity']*p
        if typ=='fade_black': state['opacity']=state['opacity']*call('max',0,2*p-1)
        if typ in ('slide','push'): state['x']=state['x']+1-p
        if typ=='zoom': state['scale']=state['scale']*(.85+.15*p)
        state['volume']=state['volume']*p
    if outgoing:
        p=clamp((local-clip['duration']+outgoing['duration'])/outgoing['duration'])
        if outgoing['type']=='push': state['x']=state['x']-p
        if outgoing['type']=='fade_black': state['opacity']=state['opacity']*call('max',0,1-2*p)
        state['volume']=state['volume']*(1-p)
    return state


def visible_text(clip,local):
    text=clip['text'];effect=clip.get('animation',{}).get('in')
    if effect and effect['type']=='typewriter':
        p=min(1,max(0,(local+clip.get('animation_offset',0))/effect['duration']))
        return text[:math.ceil(len(text)*p)]
    return text
