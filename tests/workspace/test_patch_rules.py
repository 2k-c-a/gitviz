from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.workspace.patches import apply_patch_bytes, writable_path
from app.workspace.scoped_files import ScopeError
from app.workspace.write_files import LockedFile


class PatchRules(unittest.TestCase):
    def test_exact_patch_preserves_crlf_bom_and_final_newline(self) -> None:
        patch = '--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n-x = 1\n+x = 2\n print(x)\n'
        for bom in [b'', b'\xef\xbb\xbf']:
            for eol in [b'\n', b'\r\n']:
                for final in [b'', eol]:
                    with self.subTest(bom=bom, eol=eol, final=final):
                        original = bom + b'x = 1' + eol + b'print(x)' + final
                        self.assertEqual(apply_patch_bytes('app.py', original, patch), bom + b'x = 2' + eol + b'print(x)' + final)

    def test_fuzzy_header_scope_delete_and_configuration_changes_rejected(self) -> None:
        for path in ['../x.py', '/x.py', '.git/config', '.github/workflows/x.yml', 'package.json',
                     'a.ps1', '.gitviz-workspace-forged.pending', 'a/CON.txt']:
            with self.subTest(path=path), self.assertRaises(ScopeError):
                writable_path(path)
        for patch in ['--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 9\n+x = 2\n',
                      '--- a/a.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n',
                      '--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n\\ No newline at end of file\n']:
            with self.subTest(patch=patch), self.assertRaises(ScopeError):
                apply_patch_bytes('a.py', b'x = 1\n', patch)


@unittest.skipUnless(os.name == 'nt', 'Windows handles required')
class NativePublication(unittest.TestCase):
    def test_hardlinks_and_alternate_streams_are_not_silently_replaced(self) -> None:
        with tempfile.TemporaryDirectory(prefix='gitviz-p06-link-') as temp:
            root = Path(temp)
            source = root / 'a.txt'
            source.write_bytes(b'original')
            os.link(source, root / 'alias.txt')
            with self.assertRaises(ScopeError):
                LockedFile(source)
            self.assertEqual(source.read_bytes(), b'original')
            (root / 'alias.txt').unlink()
            Path(str(source) + ':fixture').write_bytes(b'preserve-stream')
            with self.assertRaises(ScopeError):
                LockedFile(source)
            self.assertEqual(Path(str(source) + ':fixture').read_bytes(), b'preserve-stream')
    def test_handle_rename_without_replacement_and_acl_preservation(self) -> None:
        with tempfile.TemporaryDirectory(prefix='gitviz-p06-native-') as temp:
            root = Path(temp)
            path, old, pending = root / '한글.txt', root / '.gitviz-workspace-old', root / '.gitviz-workspace-new'
            path.write_bytes(b'old\r\n')
            with LockedFile(path) as original:
                self.assertEqual(original.read(), b'old\r\n')
                with self.assertRaises(PermissionError):
                    path.write_bytes(b'outside write')
                with self.assertRaises(PermissionError):
                    path.rename(root / 'outside-rename')
                with LockedFile(pending, create=True, security=original.security, attributes=original.attributes) as target:
                    target.write(b'new\r\n')
                    self.assertEqual(target.security, original.security)
                    original.rename(old)
                    self.assertFalse(path.exists())
                    target.rename(path)
                    self.assertEqual(target.read(), b'new\r\n')
                    self.assertEqual(original.read(), b'old\r\n')
                    original.delete_owned()
            self.assertEqual(path.read_bytes(), b'new\r\n')
            self.assertFalse(old.exists())

    def test_external_file_created_in_publish_gap_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory(prefix='gitviz-p06-race-') as temp:
            root = Path(temp)
            path = root / 'a.txt'
            path.write_bytes(b'original')
            with LockedFile(path) as original, LockedFile(root / '.gitviz-workspace-new', create=True,
                    security=original.security, attributes=original.attributes) as target:
                target.write(b'new')
                original.rename(root / '.gitviz-workspace-old')
                path.write_bytes(b'external creator')
                with self.assertRaises(ScopeError):
                    target.rename(path)
                self.assertEqual(original.read(), b'original')
            self.assertEqual(path.read_bytes(), b'external creator')
