import os
import signal
import subprocess
import sys
import time

import pytest

from app.worker_all import commands, supervise

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Linux supervisor')


def test_all_roles_and_web_port(monkeypatch):
    monkeypatch.setenv('PORT', '10000')
    assert [name for name, _ in commands()] == ['collect', 'llm', 'video', 'publish']
    assert '10000' in dict(commands(True))['web']


def test_prepare_failure_does_not_start_workers(tmp_path):
    marker = tmp_path / 'started'
    assert supervise([('worker', [sys.executable, '-c',
        f'from pathlib import Path; Path({str(marker)!r}).touch()'])],
        prepare=[sys.executable, '-c', 'raise SystemExit(2)'], grace=.2) == 1
    assert not marker.exists()


def test_child_exit_stops_sibling_and_collect_role(tmp_path):
    pidfile = tmp_path / 'pid'
    sleeper = [sys.executable, '-c',
        f'import os,time; from pathlib import Path; '
        f'assert os.environ["WORKER_ROLE"] == "collect"; '
        f'Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(60)']
    failure = [sys.executable, '-c', 'import time; time.sleep(.5); raise SystemExit(3)']
    assert supervise([('collect', sleeper), ('failed', failure)], grace=.2) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


def test_sigterm_stops_supervisor_and_child(tmp_path):
    pidfile = tmp_path / 'pid'
    code = ('import os,time; from pathlib import Path; '
            f'Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(60)')
    runner = subprocess.Popen([sys.executable, '-c',
        'from app.worker_all import supervise; import sys; '
        f'sys.exit(supervise([("child", [sys.executable, "-c", {code!r}])], grace=.2))'])
    try:
        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert pidfile.exists()
        runner.send_signal(signal.SIGTERM)
        assert runner.wait(timeout=5) == 0
        with pytest.raises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)
    finally:
        if runner.poll() is None:
            runner.kill()
        runner.wait()
