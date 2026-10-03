const {test}=require('node:test');
const assert=require('node:assert/strict');
const M=require('../app/static/editor-model.js');
const N=require('../app/static/editor-motion.js');
test('animation easing, split continuity and typewriter respect local time',()=>{
 const c=M.defaults({animation:{in:{type:'fade',duration:.4,easing:'linear'},out:null,loop:'zoom_in'},keyframes:[{time:0,x:0},{time:4,x:.4,easing:'ease-in-out'}]});
 assert.equal(N.state(c,.2).opacity,.5);assert.equal(N.state(c,2).x,.2);assert.equal(N.state(c,2).scale,1.06);
 const [a,b]=M.split(c,1,'second');assert.deepEqual(N.state(b,.5),N.state(c,1.5));
 c.text='Xin chào';c.animation.in={type:'typewriter',duration:.8};assert.equal(N.text(c,.4),'Xin ');
});
test('transitions shift adjacent clips, reject triple overlap, and can be removed',()=>{
 const a=M.defaults({id:'a',duration:2,source_end:2}),b=M.defaults({id:'b',timeline_start:2,duration:2,source_end:2}),track={clips:[a,b]};
 N.transition(track,b,'crossfade',.4);assert.equal(b.timeline_start,1.6);assert.ok(M.fits(track,b));assert.equal(N.state(b,.2).opacity,.5);
 N.transition(track,b,'none');assert.equal(b.timeline_start,2);assert.equal(b.transition,null);
 b.timeline_start=3;assert.throws(()=>N.transition(track,b,'push',.4));
});
test('auto-fit and presets alternate image motions within safe bounds',()=>{
 assert.equal(N.audit({width:3840,height:2160},{width:1080,height:1920}).recommended_mode,'blur_background');
 const p={tracks:[{type:'video',clips:[0,1,2,3].map(i=>M.defaults({id:String(i)}))}]};
 N.preset(p,()=>({kind:'image'}),'NEWS');assert.deepEqual(p.tracks[0].clips.map(c=>c.animation.loop),['zoom_in','pan_right','zoom_out','pan_left']);
 N.preset(p,()=>({kind:'image'}),'NONE');assert.ok(p.tracks[0].clips.every(c=>c.animation.loop==='static'));
});
test('split a sped-up source preserves exact source coverage',()=>{
 const c=M.defaults({id:'a',timeline_start:3,duration:6,source_start:35,source_end:47,speed:2,fade_in:1,fade_out:2});
 const [a,b]=M.split(c,5,'b');assert.equal(a.source_end,39);assert.equal(b.source_start,39);assert.equal(b.source_end,47);assert.equal(a.duration+b.duration,6);assert.equal(b.timeline_start,5);assert.equal(a.fade_out,0);assert.equal(b.fade_in,0);assert.equal(c.source_end,47);
 assert.throws(()=>M.split(c,3,'bad'));
});
test('trim respects source bounds, speed and minimum frame',()=>{
 const c=M.defaults({id:'a',timeline_start:10,duration:6,source_start:4,source_end:16,speed:2});
 assert.equal(M.trim(c,'left',0).timeline_start,8);assert.equal(M.trim(c,'left',0).source_start,0);
 const right=M.trim(c,'right',100,20);assert.equal(right.duration,8);assert.equal(right.source_end,20);
 assert.ok(M.trim(c,'right',-5,20).duration>=M.frame-1e-8);
});
test('reorder swaps clips without changing source windows',()=>{
 const a=M.defaults({id:'a',duration:3,source_end:3}),b=M.defaults({id:'b',timeline_start:5,duration:7,source_end:7});
 const track={clips:[a,b]};M.reorder(track,'a',1);assert.equal(b.timeline_start,0);assert.equal(a.timeline_start,9);assert.equal(a.source_start,0);assert.equal(M.end(a),12);assert.ok(M.fits(track,a));
});
test('snap, overlap and history branch semantics',()=>{
 const c=M.defaults({id:'a'}),p={tracks:[{clips:[c]}]},h=new M.History(p);
 assert.equal(M.snap(p,5.08,null,.1,0),5);assert.ok(!M.fits(p.tracks[0],M.defaults({id:'b',timeline_start:2})));
 p.tracks[0].clips[0].duration=3;h.commit(p);assert.ok(h.undo());assert.equal(h.current.tracks[0].clips[0].duration,5);assert.ok(h.redo());
 h.undo();p.tracks[0].clips=[];h.commit(p);assert.equal(h.future.length,0);
});
