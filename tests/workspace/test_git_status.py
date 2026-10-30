from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.workspace.git_service import LocalGit
from app.workspace.scoped_files import ScopeError, discover


class GitStatusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "한글 repo"
        self.root.mkdir()
        self.executable = Path(shutil.which("git") or "missing").resolve()
        self.service = LocalGit(self.executable)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "P02 Fixture")
        self.git("config", "user.email", "fixture@example.invalid")

    def git(self, *arguments: str):
        env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        return subprocess.check_output([str(self.executable), "-C", str(self.root), *arguments], env=env, stderr=subprocess.PIPE)

    def snapshot(self):
        return {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.root.rglob("*") if path.is_file()}

    def test_unborn_and_staged_unstaged_are_distinct_and_read_only(self):
        self.assertTrue(self.service.status(discover(self.root))["unborn"])
        file = self.root / "한글 file.txt"
        file.write_bytes(b"first\r\n")
        self.git("add", "--", file.name)
        self.git("commit", "-m", "initial <script>untrusted</script>")
        file.write_bytes(b"staged\r\n")
        self.git("add", "--", file.name)
        file.write_bytes(b"unstaged\r\n")
        (self.root / "untracked.txt").write_text("untracked")
        before = self.snapshot()
        state = self.service.status(discover(self.root))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(state["branch"], "main")
        self.assertEqual(state["staged"][0]["path"], file.name)
        self.assertEqual(state["unstaged"][0]["path"], file.name)
        self.assertEqual(state["untracked"][0]["path"], "untracked.txt")
        self.assertEqual(state["recentCommits"][0]["subject"], "initial <script>untrusted</script>")
        self.git("checkout", "--detach", "HEAD")
        detached = self.service.status(discover(self.root))
        self.assertTrue(detached["detached"])
        self.assertIsNone(detached["branch"])

    def test_repository_filter_is_not_executed(self):
        (self.root / ".gitattributes").write_text("*.txt filter=fixture\n")
        (self.root / "file.txt").write_text("base\n")
        self.git("add", ".")
        self.git("commit", "-m", "fixture")
        self.git("config", "filter.fixture.clean", "echo unsafe > executed.txt")
        (self.root / "file.txt").write_text("changed\n")
        state = self.service.status(discover(self.root))
        self.assertFalse((self.root / "executed.txt").exists())
        self.assertEqual(len(state["unstaged"]), 1)
        self.git("config", "include.path", "../outside.config")
        with self.assertRaisesRegex(ScopeError, "UNSUPPORTED_REPOSITORY"):
            self.service.status(discover(self.root))

    def test_missing_git_and_limits(self):
        with self.assertRaisesRegex(ScopeError, "GIT_NOT_FOUND"):
            LocalGit(self.root / "absent.exe").status(discover(self.root))
        for limit in (0, 101, True):
            with self.assertRaisesRegex(ScopeError, "INVALID_REQUEST"):
                self.service.status(discover(self.root), limit)

    def test_junction_is_listed_without_reading_target_or_hiding_other_status(self):
        outside = self.root.parent / 'outside'
        outside.mkdir()
        (outside / 'SECRET_CANARY.txt').write_text('DO_NOT_READ_CANARY')
        (self.root / 'normal.py').write_text('x = 1\n')
        link = self.root / 'jdir'
        subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/cmd.exe'), '/d', '/c',
            'mklink', '/J', str(link), str(outside)], check=True, capture_output=True)
        try:
            grant = discover(self.root)
            state = self.service.status(grant)
            self.assertEqual(state['unsupported'], [{'path': 'jdir', 'status': '--', 'unsupported': 'reparse-point'}])
            self.assertIn('normal.py', [item['path'] for item in state['untracked']])
            self.assertNotIn('CANARY', str(state))
            from app.workspace.scoped_files import pinned, read_scoped
            with self.assertRaisesRegex(ScopeError, 'PATH_OUTSIDE_SCOPE'):
                read_scoped(grant, 'jdir/SECRET_CANARY.txt')
            with self.assertRaisesRegex(ScopeError, 'PATH_OUTSIDE_SCOPE'):
                with pinned(grant):
                    self.fail('capture/write pins must remain strict')
        finally:
            os.rmdir(link)

    def test_file_and_directory_symlinks_are_unsupported_and_metadata_links_rejected(self):
        outside = self.root.parent / 'symlink-target'
        outside.mkdir()
        target = outside / 'target.txt'
        target.write_text('EXTERNAL_CANARY')
        created: list[Path] = []
        try:
            for name, destination, directory in [('dir-link', outside, True), ('file-link', target, False)]:
                link = self.root / name
                try:
                    os.symlink(destination, link, target_is_directory=directory)
                except OSError as error:
                    if getattr(error, 'winerror', None) == 1314:
                        continue  # Developer mode / symbolic-link privilege is optional; junction test is mandatory.
                    raise
                created.append(link)
            if created:
                state = self.service.status(discover(self.root))
                self.assertEqual({item['path'] for item in state['unsupported']}, {p.name for p in created})
                self.assertNotIn('EXTERNAL_CANARY', str(state))
            git_dir = self.root / '.git'
            moved = self.root.parent / 'real-git'
            git_dir.rename(moved)
            subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/cmd.exe'), '/d', '/c',
                'mklink', '/J', str(git_dir), str(moved)], check=True, capture_output=True)
            try:
                with self.assertRaisesRegex(ScopeError, 'PATH_OUTSIDE_SCOPE'):
                    self.service.status(discover(self.root))
            finally:
                os.rmdir(git_dir)
                moved.rename(git_dir)
        finally:
            for link in created:
                if link.is_dir():
                    os.rmdir(link)
                else:
                    link.unlink()

    def test_sha256_object_format_and_sparse_checkout_limitation(self):
        self.root = self.root.parent / "sha256"
        self.root.mkdir()
        self.git("init", "--object-format=sha256", "-b", "main")
        self.git("config", "user.name", "P02 Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.root / "inside").mkdir()
        (self.root / "outside").mkdir()
        (self.root / "inside/file.txt").write_text("inside")
        (self.root / "outside/file.txt").write_text("outside")
        self.git("add", ".")
        self.git("commit", "-m", "sha256")
        self.git("sparse-checkout", "init", "--cone")
        self.git("sparse-checkout", "set", "inside")
        before = self.snapshot()
        state = self.service.status(discover(self.root))
        self.assertEqual(state["objectFormat"], "sha256")
        self.assertEqual(len(state["head"]), 64)
        self.assertIn("sparse-checkout-metadata-present", state["limitations"])
        self.assertEqual(self.snapshot(), before)

    def test_conflict_rename_and_linked_worktree_metadata_grant(self):
        file = self.root / "file.txt"
        file.write_text("base\n")
        self.git("add", ".")
        self.git("commit", "-m", "base")
        self.git("checkout", "-b", "other")
        file.write_text("other\n")
        self.git("commit", "-am", "other")
        self.git("checkout", "main")
        file.write_text("main\n")
        self.git("commit", "-am", "main")
        with self.assertRaises(subprocess.CalledProcessError):
            self.git("merge", "other")
        before = self.snapshot()
        self.assertEqual(len(self.service.status(discover(self.root))["conflicts"]), 1)
        self.assertEqual(before, self.snapshot())
        self.git("merge", "--abort")
        self.git("mv", "file.txt", "renamed file.txt")
        state = self.service.status(discover(self.root))
        self.assertEqual(state["staged"][0]["oldPath"], "file.txt")
        worktree = self.root.parent / "linked worktree"
        self.git("worktree", "add", "-b", "linked", str(worktree))
        with self.assertRaisesRegex(ScopeError, "METADATA_GRANT_REQUIRED") as raised:
            discover(worktree)
        metadata = {raised.exception.requested_path or ""}
        with self.assertRaisesRegex(ScopeError, "METADATA_GRANT_REQUIRED") as common:
            discover(worktree, metadata)
        metadata.add(common.exception.requested_path or "")
        grant = discover(worktree, metadata)
        state = self.service.status(grant)
        self.assertEqual(state["branch"], "linked")
        subdirectory = self.root / "subfolder"
        subdirectory.mkdir()
        with self.assertRaisesRegex(ScopeError, "ROOT_GRANT_REQUIRED"):
            discover(subdirectory)
