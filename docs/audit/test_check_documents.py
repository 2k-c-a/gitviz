"""Regression checks for relocation, byte preservation, and document navigation."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import check_documents as checker


class OrganizedDocuments(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='gitviz-doc-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.docs = self.root / 'docs'
        self.audit = self.docs / 'audit'
        self.audit.mkdir(parents=True)
        self.original = self.docs / 'archive' / 'originals' / 'original.md'
        self.original.parent.mkdir(parents=True)
        self.original.write_bytes(b'# Original\r\n')
        self.baseline = self.audit / 'preservation-baseline.json'
        self.baseline.write_text(json.dumps({'schema_version': 1, 'files': {
            'original.md': hashlib.sha256(self.original.read_bytes()).hexdigest()}}), encoding='utf-8')
        self.relocations = self.audit / 'relocation-map.json'
        self.records = {'original.md': {'archive': 'docs/archive/originals/original.md', 'current': None}}
        self.save_map()
        self.index = self.docs / 'index.md'
        self.index.write_text('# Index\n[Inventory](audit/inventory.md)\n', encoding='utf-8')
        self.inventory = self.audit / 'inventory.md'
        self.inventory.write_text('# Inventory\n[Original](../archive/originals/original.md)\n', encoding='utf-8')
        for name, value in (('ROOT', self.root), ('DOCS', self.docs), ('BASELINE', self.baseline),
                            ('RELOCATIONS', self.relocations)):
            temporary_patch = patch.object(checker, name, value)
            temporary_patch.start()
            self.addCleanup(temporary_patch.stop)

    def save_map(self):
        self.relocations.write_text(json.dumps({'schema_version': 1, 'files': self.records}), encoding='utf-8')

    def expect(self, result, message):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            actual = checker.check()
        self.assertEqual(actual, result, output.getvalue())
        self.assertIn(message, output.getvalue())

    def test_valid_relocated_archive(self):
        self.expect(0, 'PASS relocated original hashes: 1')

    def test_byte_change_including_line_endings_is_detected(self):
        self.original.write_bytes(b'# Original\n')
        self.expect(1, 'changed archived original')

    def test_missing_archive_is_detected(self):
        self.original.unlink()
        self.expect(1, 'missing archived original')

    def test_map_must_cover_the_original_baseline(self):
        self.records.clear()
        self.save_map()
        self.expect(1, 'relocation map must cover exactly')

    def test_map_cannot_redirect_archive_to_other_content(self):
        self.records['original.md']['archive'] = 'README.md'
        self.save_map()
        self.expect(1, 'invalid archive mapping')

    def test_missing_inventory_entry_is_detected(self):
        self.inventory.write_text('# Inventory\n', encoding='utf-8')
        self.expect(1, 'original absent from inventory')

    def test_missing_link_and_anchor_are_detected(self):
        self.index.write_text('# Index\n[Inventory](audit/inventory.md#missing)\n[Missing](absent.md)\n', encoding='utf-8')
        self.expect(1, 'missing anchor')
        self.expect(1, 'missing link')

    def test_unreachable_page_is_detected(self):
        (self.docs / 'orphan.md').write_text('# Orphan\n', encoding='utf-8')
        self.expect(1, 'unreachable from docs/index.md')

    def test_old_path_must_be_removed_unless_it_is_the_current_document(self):
        (self.root / 'original.md').write_text('# Current\n', encoding='utf-8')
        self.expect(1, 'stale original path remains')
        self.records['original.md']['current'] = 'original.md'
        self.save_map()
        self.expect(0, 'PASS relocated original hashes: 1')

    def test_current_mapping_must_exist(self):
        self.records['original.md']['current'] = 'docs/current.md'
        self.save_map()
        self.expect(1, 'missing or unsafe current mapping')


if __name__ == '__main__':
    unittest.main()
