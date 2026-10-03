/* Pure timeline operations, shared by the browser and Node regression tests. */
(function(root,factory){const api=factory();if(typeof module==='object')module.exports=api;else root.EditorModel=api;})(globalThis,()=>{
  const clone=x=>JSON.parse(JSON.stringify(x));
  const frame=1/30;
  const end=c=>c.timeline_start+c.duration;
  const duration=p=>Math.max(0,...p.tracks.flatMap(t=>t.clips.map(end)));
  const find=(p,id)=>{for(const track of p.tracks){const clip=track.clips.find(c=>c.id===id);if(clip)return {track,clip};}return null;};
  function defaults(values={}){
    const c={id:crypto.randomUUID(),name:'',text:'',asset_id:null,audio_id:null,timeline_start:0,duration:5,source_start:0,source_end:5,speed:1,volume:1,muted:false,fade_in:0,fade_out:0,
      transform:{x:0,y:0,width:1,height:1,scale:1,rotation:0,opacity:1},crop:{mode:'fill',x:.5,y:.5,zoom:1,manual:false,background:'#000000',background_asset_id:null,blur:30,darken:.25},
      animation:{in:null,out:null,loop:'static'},keyframes:[],transition:null,animation_offset:0,animation_duration:0,words:[],subtitle_animation:'static',
      style:{font:'DejaVu Sans',size:52,color:'#ffffff',stroke:2,shadow:0,background:false,highlight:'#ffe66d'}};
    return {...c,...values,transform:{...c.transform,...values.transform},crop:{...c.crop,...values.crop},style:{...c.style,...values.style}};
  }
  function split(c,time,id){
    const offset=time-c.timeline_start;
    if(offset<frame-1e-6||c.duration-offset<frame-1e-6)throw Error('Đặt playhead bên trong clip (cách hai đầu ít nhất 1 frame).');
    if(c.transition&&offset<=c.transition.duration)throw Error('Đặt mốc split sau transition đầu clip.');
    const a=clone(c),b=clone(c),source=c.source_start+offset*c.speed;
    Object.assign(a,{duration:offset,source_end:source,fade_out:0,fade_in:Math.min(c.fade_in,offset)});
    Object.assign(b,{id,timeline_start:time,duration:c.duration-offset,source_start:source,fade_in:0,fade_out:Math.min(c.fade_out,c.duration-offset)});
    a.animation_duration=b.animation_duration=c.animation_duration||c.duration;b.animation_offset=(c.animation_offset||0)+offset;b.transition=null;
    return [a,b];
  }
  function trim(c,side,time,maxSource=Infinity){
    const r=clone(c),oldEnd=end(c);
    if(side==='left'){
      const start=Math.max(0,c.timeline_start-c.source_start/c.speed,Math.min(time,oldEnd-frame));
      r.source_start=c.source_start+(start-c.timeline_start)*c.speed;r.timeline_start=start;r.duration=oldEnd-start;r.animation_duration=c.animation_duration||c.duration;r.animation_offset=Math.max(0,(c.animation_offset||0)+(start-c.timeline_start));
    }else{
      const finish=Math.max(c.timeline_start+frame,Math.min(time,c.timeline_start+(maxSource-c.source_start)/c.speed));
      r.duration=finish-c.timeline_start;r.source_end=r.source_start+r.duration*r.speed;
    }
    r.fade_in=Math.min(r.fade_in,r.duration);r.fade_out=Math.min(r.fade_out,r.duration-r.fade_in);
    return r;
  }
  function fits(track,clip,exclude=clip.id){return track.clips.every(c=>{if(c.id===exclude||end(clip)<=c.timeline_start+.0001||clip.timeline_start>=end(c)-.0001)return true;const a=c.timeline_start<=clip.timeline_start?c:clip,b=a===c?clip:c;return b.transition?.from_clip_id===a.id&&Math.abs(end(a)-b.timeline_start-b.transition.duration)<.002;});}
  function snap(p,time,excluded,tolerance,playhead){
    const points=[0,playhead,...p.tracks.flatMap(t=>t.clips.filter(c=>c.id!==excluded).flatMap(c=>[c.timeline_start,end(c)]))];
    let best=time,distance=tolerance;
    for(const point of points)if(Math.abs(point-time)<distance){distance=Math.abs(point-time);best=point;}
    return Math.max(0,best);
  }
  function reorder(track,id,direction){
    const sorted=[...track.clips].sort((a,b)=>a.timeline_start-b.timeline_start),i=sorted.findIndex(c=>c.id===id),other=sorted[i+direction];
    if(i<0||!other)return false;
    const clip=sorted[i],first=direction<0?other:clip,second=direction<0?clip:other;
    const start=first.timeline_start,gap=Math.max(0,second.timeline_start-end(first));
    second.timeline_start=start;first.timeline_start=start+second.duration+gap;return true;
  }
  class History{
    constructor(value){this.current=clone(value);this.history=[];this.future=[];}
    commit(value){if(JSON.stringify(value)===JSON.stringify(this.current))return false;this.history.push(this.current);if(this.history.length>100)this.history.shift();this.current=clone(value);this.future=[];return true;}
    undo(){if(!this.history.length)return false;this.future.push(this.current);this.current=this.history.pop();return true;}
    redo(){if(!this.future.length)return false;this.history.push(this.current);this.current=this.future.pop();return true;}
  }
  return {clone,frame,end,duration,find,defaults,split,trim,fits,snap,reorder,History};
});
