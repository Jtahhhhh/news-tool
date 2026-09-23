"""Exercise actual worker processes and HTTP IO against an isolated test database."""
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from sqlalchemy import func, select
import pytest
from app.models import Article, Job, Source
from app.services.jobs import enqueue

pytestmark = [pytest.mark.integration, pytest.mark.skipif(sys.platform == 'win32', reason='Linux container process signals')]
FEED = b'<rss version="2.0"><channel><title>Fixture</title><item><title>Recovery test story</title><link>https://example.com/recovery</link></item></channel></rss>'


def wait_for(predicate, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.2)
    raise AssertionError('Timed out waiting for worker state')


@pytest.mark.parametrize('interruption', ['kill', 'graceful', 'timeout'])
def test_worker_restarts_without_losing_job_or_duplicating_data(db, interruption):
    calls = []
    received = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(1)
            received.set()
            if len(calls) == 1:
                time.sleep(12)
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'application/rss+xml')
                self.end_headers()
                self.wfile.write(FEED)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with db() as session:
        source = Source(name='Recovery fixture', url=f'http://127.0.0.1:{server.server_port}/feed', enabled=False)
        session.add(source)
        session.flush()
        job_id = enqueue(session, source.id).id
    env = dict(os.environ, DATABASE_URL=os.environ['TEST_DATABASE_URL'], ALLOW_PRIVATE_SOURCES='true',
               JOB_TIMEOUT_SECONDS='5', REQUEST_TIMEOUT_SECONDS='20', RETRY_DELAY_SECONDS='1', POLL_SECONDS='0.2')
    def start():
        return subprocess.Popen([sys.executable, '-m', 'app.worker'], env=env, start_new_session=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def status():
        with db() as session:
            return session.get(Job, job_id).status
    worker = start()
    try:
        assert received.wait(10), 'Worker never fetched the fixture feed'
        if interruption != 'timeout':
            os.killpg(worker.pid, signal.SIGKILL if interruption == 'kill' else signal.SIGTERM)
            worker.wait(timeout=10)
            if interruption == 'graceful':
                assert status() == 'retry'
            worker = start()
        wait_for(lambda: status() == 'succeeded')
        with db() as session:
            job = session.get(Job, job_id)
            assert job.attempts == 2 and job.error_count == 1
            assert session.scalar(select(func.count()).select_from(Article)) == 1
            assert len(job.logs) >= 4
    finally:
        if worker.poll() is None:
            os.killpg(worker.pid, signal.SIGTERM)
            worker.wait(timeout=10)
        server.shutdown()
        server.server_close()
