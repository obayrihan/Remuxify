import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest

from core import Config, Inventory, Store, norm


class InventoryTests(unittest.TestCase):
    def test_windows_watcher_indexes_new_folder_and_file(self):
        with tempfile.TemporaryDirectory(prefix='forge-watch-test-') as folder:
            base=Path(folder)
            root=base/'library';root.mkdir()
            c=Config().get();c['stable_seconds']=30
            (base/'config.json').write_text(json.dumps(c))
            config=Config(base/'config.json')
            store=Store(base/'test.db')
            fake_jellyfin=type('Jellyfin',(),{'roots':lambda _: [{'library':'DISPOSABLE TEST','path':str(root)}]})()
            stop=threading.Event()
            inventory=Inventory(config,store,fake_jellyfin,stop)
            thread=threading.Thread(target=inventory.loop,daemon=True);thread.start()
            try:
                for _ in range(50):
                    if inventory.watched:break
                    time.sleep(.1)
                self.assertIn(norm(root),inventory.watched)
                new_folder=root/'new directory';new_folder.mkdir()
                output=new_folder/'generated.mp4'
                result=subprocess.run([c['ffmpeg'],'-v','error','-f','lavfi','-i','color=size=320x180:rate=24',
                                       '-t','1','-c:v','libx264','-threads','1',str(output)],capture_output=True,timeout=20)
                self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
                os.utime(output,(time.time()-300,time.time()-300))
                found=None
                for _ in range(100):
                    found=store.one('SELECT * FROM media WHERE path=?',(str(output),))
                    if found:break
                    time.sleep(.1)
                self.assertIsNotNone(found,'New directory/media was not indexed by the real Windows watcher')
                self.assertIsNone(found['error'])
                self.assertEqual(json.loads(found['info'])['codec'],'h264')
            finally:
                stop.set();inventory.wake.set();thread.join(timeout=10)


if __name__=='__main__':unittest.main(verbosity=2)
