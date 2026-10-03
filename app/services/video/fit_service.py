"""Deterministic auto-fit; manual crop always has priority over suggestions."""
from copy import deepcopy

TARGETS={'vertical':(1080,1920),'youtube':(1920,1080),'square':(1080,1080),'portrait':(1080,1350)}


def audit_target(source,target):
    sr=source.get('aspect_ratio') or source.get('width',0)/max(1,source.get('height',1))
    tr=target['width']/target['height']
    mismatch=bool(sr and abs(sr-tr)/tr>.02)
    mode='blur_background' if mismatch else 'fill'
    return dict(source_ratio=sr,target_ratio=tr,requires_fit=mismatch,
                issues=['ASPECT_RATIO_MISMATCH'] if mismatch else [],recommended_mode=mode,
                alternatives=['fill','smart_crop','blur_background'] if mismatch else ['fill','fit'])


def crop_window(source,target,x=.5,y=.5,zoom=1):
    w,h=source['width'],source['height'];ratio=target['width']/target['height']
    cw,ch=min(w,h*ratio),min(h,w/ratio)
    cw/=zoom;ch/=zoom
    return dict(x=(w-cw)*x,y=(h-ch)*y,width=cw,height=ch)


def auto_fit(clip,source,target):
    c=deepcopy(clip)
    if not c['crop'].get('manual'):
        c['crop'].update(mode=audit_target(source,target)['recommended_mode'],x=.5,y=.5,zoom=1)
    return c


def apply_preset(composition,media,preset):
    c=deepcopy(composition);c['animation_preset']=preset
    motions=['zoom_in','pan_right','zoom_out','pan_left'];image_index=0
    for track in c['tracks']:
        for clip in track['clips']:
            clip['animation']={'in':None,'out':None,'loop':'static'}
            clip['subtitle_animation']='static'
            if preset=='NONE': continue
            if track['type']=='video' and media.get(clip.get('asset_id'),{}).get('kind')=='image':
                clip['animation']['loop']=motions[image_index%len(motions)];image_index+=1
            if track['type']=='text':
                clip['animation']['in']={'type':'pop' if preset in ('MODERN','DRAMA') else 'fade','duration':min(.3,clip['duration']/2),'easing':'ease-out'}
            if track['type']=='subtitle': clip['subtitle_animation']='pop' if preset in ('STORY','DRAMA') else 'fade'
    return c
