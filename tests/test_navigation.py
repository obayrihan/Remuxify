"""Navigation setup preserves other links and can be reapplied after updates."""
import unittest
from navigation import NATIVE_LINK, native_index, set_menu


class NavigationTests(unittest.TestCase):
    def test_replaces_shortcut_in_place_without_changing_other_links(self):
        before = {'name': 'First', 'url': 'https://first.example.com'}
        after = {'name': 'Last', 'url': 'https://last.example.com'}
        config = {'menuLinks': [before.copy(), {'name': 'Media Forge', 'url': '/web/forge.html'}, after.copy()]}
        set_menu(config, native=True)
        self.assertEqual(config['menuLinks'], [before, NATIVE_LINK, after])
        set_menu(config, native=True)
        self.assertEqual(config['menuLinks'], [before, NATIVE_LINK, after])

    def test_adds_only_one_relative_same_origin_script(self):
        original = '<html><head><script src="other.js"></script></head><body>Unchanged</body></html>'
        changed = native_index(original)
        self.assertIn('<script src="other.js"></script>', changed)
        self.assertIn('<body>Unchanged</body>', changed)
        self.assertEqual(changed.count('src="remuxify-navigation.js"'), 1)
        self.assertEqual(native_index(changed), changed)

    def test_unknown_web_layout_is_rejected(self):
        with self.assertRaises(RuntimeError):
            native_index('<html><body>Unsupported</body></html>')


if __name__ == '__main__':
    unittest.main()
