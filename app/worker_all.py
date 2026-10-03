"""Run all workers, optionally with the web server, under one Linux supervisor."""
import argparse
import os
import signal
import subprocess
import sys
import time


def commands(with_web=False):
    result = [(name, [sys.executable, '-m', module]) for name, module in (
        ('collect', 'app.worker'), ('llm', 'app.llm_worker'),
        ('video', 'app.render_worker'), ('publish', 'app.publish_worker'))]
    if with_web:
        result.append(('web', [sys.executable, '-m', 'uvicorn', 'app.main:app',
                              '--host', '0.0.0.0', '--port', os.getenv('PORT', '8000'),
                              '--no-access-log']))
    return result


def supervise(services, *, prepare=None, grace=20):
    children = []
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    def launch(name, command):
        env = dict(os.environ, PYTHONUNBUFFERED='1')
        if name == 'collect':
            env['WORKER_ROLE'] = 'collect'
        child = subprocess.Popen(command, env=env, start_new_session=True)
        children.append((name, child))
        print(f'Supervisor: {name} started pid={child.pid}', flush=True)
        return child

    def signal_group(child, sig):
        try:
            os.killpg(child.pid, sig)
        except ProcessLookupError:
            pass

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        if prepare:
            child = launch('database-prepare', prepare)
            while child.poll() is None and not stopping:
                time.sleep(.1)
            if stopping:
                return 0
            if child.returncode != 0:
                print('Supervisor: database preparation FAILED', flush=True)
                return 1
            children.remove(('database-prepare', child))
        for name, command in services:
            if stopping:
                return 0
            launch(name, command)
        while not stopping:
            for name, child in children:
                code = child.poll()
                if code is not None:
                    print(f'Supervisor: {name} exited code={code}; stopping service', flush=True)
                    return 1
            time.sleep(.2)
        return 0
    finally:
        # Signal whole groups so multiprocessing children cannot survive a deploy.
        for _, child in children:
            signal_group(child, signal.SIGTERM)
        deadline = time.monotonic() + grace
        while any(child.poll() is None for _, child in children) and time.monotonic() < deadline:
            time.sleep(.1)
        for _, child in children:
            signal_group(child, signal.SIGKILL)
            child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-web', action='store_true', help='Also serve the web UI on PORT')
    args = parser.parse_args()
    if os.name != 'posix':
        parser.error('Run this supervisor in the Linux Docker image')
    return supervise(commands(args.with_web),
                     prepare=[sys.executable, '-m', 'app.start', '--prepare-only'])


if __name__ == '__main__':
    raise SystemExit(main())
