import json
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from core import BASE, CREATE_FLAGS


class RestartGuardTests(unittest.TestCase):
    def test_paused_queue_can_preserve_waiting_jobs_but_never_interrupt_active_work(self):
        source = (BASE / 'restart-forge.ps1').read_text(encoding='utf-8-sig')
        code = re.search(r'-B -c "([^"]+)" \$forgeAllowPausedValue', source).group(1)
        with tempfile.TemporaryDirectory(prefix='forge-restart-guard-') as folder:
            root = Path(folder)
            database = root / 'test.db'
            with closing(sqlite3.connect(database)) as db, db:
                db.execute('CREATE TABLE jobs(state TEXT)')
                db.execute('INSERT INTO jobs VALUES (?)', ('waiting',))
            code = code.replace('file:data/forge.db?mode=ro',
                                'file:' + database.as_posix() + '?mode=ro')
            def guard(allow, paused):
                (root / 'config.json').write_text(json.dumps({'queue_paused': paused}))
                return subprocess.run([sys.executable, '-B', '-c', code, str(allow)], cwd=root,
                                      capture_output=True, creationflags=CREATE_FLAGS).returncode
            self.assertEqual(guard(0, True), 2)
            self.assertEqual(guard(1, True), 0)
            self.assertEqual(guard(1, False), 3)
            for state in ('encoding', 'validating', 'committing'):
                with closing(sqlite3.connect(database)) as db, db:
                    db.execute('UPDATE jobs SET state=?', (state,))
                self.assertEqual(guard(1, True), 2)
