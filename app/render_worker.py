import multiprocessing,signal,sys,time
from pathlib import Path
from app.database import session_scope
from app.services.video_service import claim,execute,recover
HEARTBEAT=Path('/tmp/news-tool-render-heartbeat');stopping=False
def stop(*_):
    global stopping;stopping=True
def main():
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop);ctx=multiprocessing.get_context('spawn')
    while not stopping:
        HEARTBEAT.touch()
        with session_scope() as session:recover(session);item=claim(session)
        if not item:time.sleep(1);continue
        process=ctx.Process(target=execute,args=item);process.start()
        while process.is_alive() and not stopping:process.join(.5);HEARTBEAT.touch()
        if stopping and process.is_alive():process.terminate();process.join(5)
if __name__=='__main__':
    if '--healthcheck' in sys.argv:sys.exit(0 if HEARTBEAT.exists() and time.time()-HEARTBEAT.stat().st_mtime<45 else 1)
    main()
