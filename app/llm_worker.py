"""Dedicated bounded process pool; a slow LLM never blocks collection."""
import logging
import multiprocessing
import signal
import sys
import time
from pathlib import Path
from app.config import get_settings
from app.database import session_scope
from app.models import ScriptJob
from app.services.script_service import claim_script, execute_script, fail_script_owned, recover_scripts, source_fetch_budget
from app.services.llm_control import sync_credentials

HEARTBEAT = Path('/tmp/news-tool-llm-heartbeat')
stopping = False


def stop(*_):
    global stopping
    stopping = True


def main():
    logging.basicConfig(level=logging.INFO)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    settings = get_settings()
    context = multiprocessing.get_context('spawn')
    running = []
    next_sync = 0
    try:
        while not stopping:
            HEARTBEAT.touch()
            claim = None
            for entry in running[:]:
                process, claim, deadline = entry
                if not process.is_alive() or time.monotonic() >= deadline:
                    if process.is_alive():
                        process.terminate();process.join(3)
                        if process.is_alive():process.kill()
                    process.join()
                    fail_script_owned(*claim, 'Tiến trình LLM kết thúc hoặc vượt thời hạn')
                    running.remove(entry)
            try:
                with session_scope() as session:
                    recover_scripts(session)
                    if time.monotonic() >= next_sync:
                        sync_credentials(session);next_sync = time.monotonic()+30
                    claim = claim_script(session) if len(running) < settings.llm_max_concurrency else None
                    job = session.get(ScriptJob,claim[0]) if claim else None
                    timeout = job.timeout_seconds+source_fetch_budget(job)+20 if job else 0
                if claim:
                    process = context.Process(target=execute_script,args=claim,daemon=True)
                    process.start()
                    running.append((process,claim,time.monotonic()+timeout))
            except Exception:
                logging.exception('LLM queue failed')
            time.sleep(.25 if claim else 1)
    finally:
        for process,claim,_ in running:
            if process.is_alive():process.terminate()
            process.join(3)
            if process.is_alive():process.kill();process.join()
            fail_script_owned(*claim,'LLM worker dừng')


if __name__ == '__main__':
    if '--healthcheck' in sys.argv:
        sys.exit(0 if HEARTBEAT.exists() and time.time()-HEARTBEAT.stat().st_mtime<45 else 1)
    main()
