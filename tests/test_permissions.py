from pathlib import Path
import tempfile
import unittest
from permissions import secure_tree, set_dacl_only


class PermissionTests(unittest.TestCase):
    def test_empty_child_dacl_is_repaired_without_losing_read_access(self):
        with tempfile.TemporaryDirectory(prefix='forge-acl-test-') as folder:
            root=Path(folder)
            child=root/'child';child.mkdir()
            file=child/'disposable.txt';file.write_text('permission repair fixture')
            set_dacl_only(file,'D:P')
            with self.assertRaises(PermissionError):file.read_text()
            self.assertEqual(secure_tree(root),3)
            self.assertEqual(file.read_text(),'permission repair fixture')
            file.write_text('updated')
            self.assertEqual(file.read_text(),'updated')


if __name__=='__main__':unittest.main(verbosity=2)
