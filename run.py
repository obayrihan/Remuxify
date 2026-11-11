"""Single-instance entry point; launch this file rather than uvicorn directly."""
import os
from pathlib import Path
import sys

base = Path(__file__).resolve().parent
os.chdir(base)
(base / 'data').mkdir(exist_ok=True)
lock = (base / 'data/instance.lock').open('a+b')
if os.name == 'nt':
    import msvcrt
    lock.seek(0)
    if not lock.read(1):
        lock.write(b'0')
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print('Remuxify is already running.', file=sys.stderr)
        sys.exit(0)

from worker import retain_child_job
child_job = retain_child_job()
from app import app, config
import uvicorn
c = config.get()
uvicorn.run(app, host=c['host'], port=c['port'], access_log=False,
            proxy_headers=True, forwarded_allow_ips='127.0.0.1,::1', log_level='info')
