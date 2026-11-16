import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import build_demo


class ProductExportTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is required for invitation UI regression checks')
    def test_invitation_refresh_and_used_state(self):
        subprocess.run(['node', '--test', str(Path(__file__).with_name('test_install_ui.cjs'))], check=True)

    def test_static_export_is_explicit_simulation_and_contains_only_frontend_files(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'demo'
            with patch('sys.argv', ['build_demo.py', '--output', str(output)]):
                build_demo.main()
            self.assertEqual({p.name for p in output.iterdir()}, {*build_demo.FILES, '.nojekyll'})
            for name in ('index.html', 'install.html'):
                html = (output / name).read_text()
                self.assertIn('data-mode="demo"', html)
                self.assertIn('data-static="true"', html)
                self.assertNotIn('data-mode="live"', html)
            self.assertIn('不连接真实设备', (output / 'index.html').read_text())
            self.assertIn('不会安装软件', (output / 'install.html').read_text())

    def test_export_cannot_overwrite_frontend_source(self):
        with patch('sys.argv', ['build_demo.py', '--output', str(build_demo.ROOT / 'web')]):
            with self.assertRaises(SystemExit):
                build_demo.main()
