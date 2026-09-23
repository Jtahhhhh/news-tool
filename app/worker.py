import logging
import multiprocessing
import signal
import sys
import time
from pathlib import Path
from app.config import get_settings
from app.database import session_scope
from app.models import ScriptJob
from app.services.jobs import claim_job, execute_job, fail_owned, recover_expired, schedule_due
from app.services.script_service import claim_script, execute_script, fail_script_owned, recover_scripts
from app.services.llm_control import sync_credentials

HEARTBEAT = Path('/tmp/news-tool-worker-heartbeat')
stopping = False


def stop(signum, frame):
    global stopping
    stopping = True


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    settings = get_settings()
    context = multiprocessing.get_context('spawn')
    prefer_script = True
    next_secret_sync = 0
    while not stopping:
        HEARTBEAT.touch()
        try:
            if time.monotonic() >= next_secret_sync:
                with session_scope() as session:
                    sync_credentials(session)
                next_secret_sync = time.monotonic() + 30
            with session_scope() as session:
                recover_expired(session)
                recover_scripts(session)
                schedule_due(session)
                script_claim = claim_script(session) if prefer_script else None
                claim = None if script_claim else claim_job(session)
                if not claim and not script_claim:
                    script_claim = claim_script(session)
                is_script = script_claim is not None
                claim = script_claim or claim
                script_timeout = session.get(ScriptJob, script_claim[0]).timeout_seconds if script_claim else 0
                prefer_script = not is_script
            if claim:
                job_id, owner = claim
                target = execute_script if is_script else execute_job
                failure = fail_script_owned if is_script else fail_owned
                process = context.Process(target=target, args=(job_id, owner), daemon=True)
                process.start()
                deadline = time.monotonic() + (script_timeout + 20 if is_script else settings.job_timeout_seconds)
                while process.is_alive() and not stopping and time.monotonic() < deadline:
                    process.join(timeout=0.5)
                    HEARTBEAT.touch()
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=3)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=3)
                    failure(job_id, owner, 'Worker dừng; yêu cầu được giữ lại' if stopping else 'Job vượt thời hạn xử lý')
                else:
                    # Covers a crashed child or a lease that expired just before commit.
                    failure(job_id, owner, f'Tiến trình kết thúc mà chưa hoàn tất job (exit={process.exitcode})')
            else:
                for _ in range(max(1, int(settings.poll_seconds * 10))):
                    if stopping:
                        break
                    time.sleep(0.1)
        except Exception:
            logging.exception('Worker loop failed; retrying')
            time.sleep(min(settings.poll_seconds, 5))


if __name__ == '__main__':
    if '--healthcheck' in sys.argv:
        sys.exit(0 if HEARTBEAT.exists() and time.time() - HEARTBEAT.stat().st_mtime < 45 else 1)
    main()
