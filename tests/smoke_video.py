"""Create a TEST-labelled two-scene video through the durable production queue."""
import json,time,uuid
from sqlalchemy import select
from app.database import session_scope
from app.models import Event,ScriptSourceSnapshot,ScriptVersion,VideoJob,VideoVersion
from app.services.video_service import DATA,enqueue


def main():
    marker='video-smoke-'+uuid.uuid4().hex[:10]
    with session_scope() as session:
        event=Event(title='Kiểm thử video local hai cảnh '+marker,decision='selected');session.add(event);session.flush()
        snap=ScriptSourceSnapshot(event_id=event.id,payload={'sources':[],'test_only':True},digest=marker);session.add(snap);session.flush()
        data={'title':'Bản tin kiểm thử VieNeu-TTS','hook':'Kiểm tra dựng video local.', 'claims':[],
              'scenes':[
                {'scene_id':'1','narration':'VieNeu đọc số 120 và tên riêng Nguyễn Ánh.','on_screen_text':'KIỂM TRA SỐ 120','visual_brief':'Nền tin tức','seconds':4,'claim_ids':[]},
                {'scene_id':'2','narration':'API và FFmpeg hoạt động cùng tiếng Việt.','on_screen_text':'AUDIO VÀ VIDEO ĐỒNG BỘ','visual_brief':'Nền tin tức','seconds':4,'claim_ids':[]}
              ],'caption':'Dữ liệu kiểm thử','hashtags':['#test'],'target_seconds':8}
        script=ScriptVersion(event_id=event.id,version=1,snapshot_id=snap.id,provider='fake',model='fixture',origin='manual',outcome='draft',status='approved',data=data,raw_output='',validation_errors=[],usage={},elapsed_seconds=0,prompt_version='smoke',schema_version='1.0',prompt_hash='0'*64,schema_hash='0'*64)
        session.add(script);session.flush()
        job=enqueue(session,script.id,'smoke-'+uuid.uuid4().hex,{'test_only':True,'pronunciation':{'120':'một trăm hai mươi','API':'ây pi ai','FFmpeg':'ép ép em peg'}});job_id=job.id
    deadline=time.monotonic()+900
    while time.monotonic()<deadline:
        with session_scope() as session:
            job=session.get(VideoJob,job_id)
            if job.status=='succeeded':
                version=session.scalar(select(VideoVersion).where(VideoVersion.job_id==job_id));path=DATA/version.output_key
                print(json.dumps({'job_id':job_id,'video_version_id':version.id,'status':job.status,'duration_seconds':version.duration_seconds,'output':str(path),'bytes':path.stat().st_size,'probe':version.probe},ensure_ascii=False))
                return
            if job.status in ('failed','cancelled'):raise RuntimeError(f'{job.status}: {job.error}')
        time.sleep(2)
    raise TimeoutError(f'Video job {job_id} did not finish')


if __name__=='__main__':main()
