"""Regression coverage for Mac resources flattened by browser uploads."""
from contextlib import redirect_stdout
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from build_macos import ensure_ui_assets, UI_ASSET_FILES


class MacBuildAssetsTests(unittest.TestCase):
    def test_flattened_upload_is_restored(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)
            for name in UI_ASSET_FILES:
                (root/name).write_bytes(('test-'+name).encode())
            with redirect_stdout(io.StringIO()):
                folder=ensure_ui_assets(root)
            for name in UI_ASSET_FILES:
                self.assertEqual((folder/name).read_bytes(),(root/name).read_bytes())
                self.assertTrue((root/name).is_file())
            self.assertEqual(ensure_ui_assets(root),folder)

    def test_existing_resources_are_never_overwritten(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);assets=root/'assets';assets.mkdir()
            for name in UI_ASSET_FILES:
                (assets/name).write_bytes(b'original')
                (root/name).write_bytes(b'flat-copy')
            ensure_ui_assets(root)
            for name in UI_ASSET_FILES:self.assertEqual((assets/name).read_bytes(),b'original')

    def test_incomplete_upload_fails_before_copying(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);(root/'tailwind.css').write_text('test')
            with self.assertRaisesRegex(SystemExit,'Missing UI resources:'):
                ensure_ui_assets(root)
            self.assertFalse((root/'assets').exists())


if __name__=='__main__':unittest.main()
