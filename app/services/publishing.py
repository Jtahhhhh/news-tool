"""Server-side review gate and immutable consent for a specific output file."""
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from app.models import (VideoVersion, VideoReview, ScriptVersion, MediaAsset,
                        TikTokAccount, PublishJob, PublishAttempt, utcnow)
from app.services.media_integrity import video_path, sha256_file, inspect_video
from app.tiktok.api import chunks

LABELS = {'queued':'Chờ gửi', 'initializing':'Đang khởi tạo upload', 'uploading':'Đang gửi video',
          'transferring':'Đang gửi một phần video', 'polling':'Đang xử lý tại TikTok',
          'reconcile':'Đang đối chiếu trạng thái', 'inbox':'Đã chuyển sang TikTok — cần hoàn tất đăng',
          'published':'Đã đăng — TikTok xác nhận', 'failed':'Thất bại',
          'unknown_outcome':'Chưa rõ kết quả — không tự gửi lại',
          'cancelled':'Đã hủy trước khi gửi', 'stopped':'Đã dừng local — không xóa nội dung tại TikTok'}


class CreatePublish(BaseModel):
    model_config = ConfigDict(extra='forbid')
    video_version_id: int
    account_id: int
    video_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    idempotency_key: str = Field(min_length=8,max_length=128)
    caption: str = Field(default='',max_length=4000)
    confirmed: Literal[True]
    mode: Literal['upload'] = 'upload'


def eligible(session, version_id, expected=None, verify_file=True):
    version=session.get(VideoVersion,version_id)
    if not version or version.status!='approved' or version.test_only or version.config.get('test_only'):
        raise HTTPException(409,'Chỉ gửi video thật đã được duyệt')
    script=session.get(ScriptVersion,version.script_version_id)
    if not script or script.provider=='fake':
        raise HTTPException(409,'Kịch bản giả lập không được xuất bản')
    for scene in version.timeline.get('scenes',[]):
        if scene.get('asset_id'):
            asset=session.get(MediaAsset,scene['asset_id'])
            if not asset or asset.test_only: raise HTTPException(409,'Video chứa asset TEST hoặc thiếu asset')
    review=session.scalar(select(VideoReview).where(VideoReview.video_version_id==version.id).order_by(VideoReview.id.desc()).limit(1))
    if (not version.output_sha256 or not review or review.decision!='approved'
            or review.output_sha256!=version.output_sha256):
        raise HTTPException(409,'Cần duyệt lại video để gắn checksum với quyết định duyệt')
    if expected and version.output_sha256!=expected:
        raise HTTPException(409,'Checksum xác nhận không khớp phiên bản')
    path=video_path(version.output_key)
    if verify_file and sha256_file(path)!=version.output_sha256:
        raise HTTPException(409,'File đã thay đổi sau khi duyệt; không được gửi')
    chunks(path.stat().st_size)
    return version,review,path


def enqueue(session, payload):
    session.execute(text('SELECT pg_advisory_xact_lock(721009)'))
    request_hash=hashlib.sha256(json.dumps(payload.model_dump(exclude={'idempotency_key'}),sort_keys=True).encode()).hexdigest()
    old=session.scalar(select(PublishJob).where(PublishJob.idempotency_key==payload.idempotency_key))
    if old:
        if old.request_hash!=request_hash: raise HTTPException(409,'Mã yêu cầu đã dùng cho nội dung khác')
        return old
    if session.scalar(select(PublishJob.id).where(PublishJob.video_version_id==payload.video_version_id,PublishJob.account_id==payload.account_id)):
        raise HTTPException(409,'Phiên bản này đã có yêu cầu gửi đến tài khoản; mở job cũ để đối chiếu')
    account=session.get(TikTokAccount,payload.account_id)
    if not account or account.state!='connected' or 'video.upload' not in account.scopes:
        raise HTTPException(409,'Tài khoản chưa kết nối hoặc chưa cấp quyền video.upload')
    version,review,path=eligible(session,payload.video_version_id,payload.video_sha256)
    job=PublishJob(video_version_id=version.id,account_id=account.id,idempotency_key=payload.idempotency_key,
        request_hash=request_hash,video_sha256=version.output_sha256,snapshot={
            'account_open_id':account.open_id,'account_name':account.display_name,'version':version.version,
            'review_id':review.id,'output_key':version.output_key,'size':path.stat().st_size,
            'caption':payload.caption,'mode':'upload','consent':True,'consented_at':utcnow().isoformat()})
    session.add(job);session.flush();return job


@contextmanager
def verified_copy(session,job):
    _,review,path=eligible(session,job.video_version_id,job.video_sha256,verify_file=False)
    if review.id!=job.snapshot['review_id']:
        raise HTTPException(409,'Quyết định duyệt đã thay đổi')
    # Send the checked private copy, not a mutable render path (TOCTOU).
    with tempfile.TemporaryDirectory(prefix='tiktok-upload-') as folder:
        copy=Path(folder)/'video.mp4'
        shutil.copyfile(path,copy)
        if sha256_file(copy)!=job.video_sha256 or copy.stat().st_size!=job.snapshot['size']:
            raise HTTPException(409,'File thay đổi sau duyệt')
        meta=inspect_video(copy)
        video=next(s for s in meta['streams'] if s.get('codec_type')=='video')
        from fractions import Fraction
        try: fps=float(Fraction(video.get('avg_frame_rate','0')))
        except (ValueError,ZeroDivisionError): fps=0
        if (video.get('codec_name') not in ('h264','hevc','vp8','vp9') or not 23<=fps<=60
                or not all(360<=int(video.get(k,0))<=4096 for k in ('width','height'))
                or not 3<=float(meta['format']['duration'])<=600):
            raise HTTPException(409,'Video không đạt giới hạn upload của ứng dụng (MP4, 3–600 giây, 23–60 fps, 360–4096 px)')
        yield copy


def public_job(job):
    return {**{k:getattr(job,k) for k in ('id','video_version_id','account_id','status','sent_bytes','error','remote_status','post_ids')},
            'label':LABELS.get(job.status,job.status),'snapshot':job.snapshot,
            'has_publish_id':bool(job.publish_id),'next_run_at':job.next_run_at.isoformat() if job.next_run_at else None}


def cancel(session,job):
    job.status='cancelled' if job.status=='queued' and not job.init_attempts else 'stopped'
    job.next_run_at=None
    job.owner=None;job.lease_until=None
    session.add(PublishAttempt(job_id=job.id,operation='local_stop',outcome=job.status,finished_at=utcnow()))


def reconcile(session,job):
    if not job.publish_id:
        raise HTTPException(409,'Không có publish_id; không thể đối chiếu tự động và không được khởi tạo lại')
    if job.status in ('published','cancelled'):
        raise HTTPException(409,'Job đã kết thúc')
    if job.owner and job.lease_until and job.lease_until>utcnow():
        raise HTTPException(409,'Worker đang xử lý; chờ hoàn tất thao tác hiện tại')
    # Manual recovery is status-only. Never resumes sending a cancelled upload.
    job.status='polling';job.failures=0;job.poll_count=0
    job.next_run_at=max(utcnow(),job.next_run_at) if job.next_run_at else utcnow()
    session.add(PublishAttempt(job_id=job.id,operation='manual_status',outcome='queued',finished_at=utcnow()))
