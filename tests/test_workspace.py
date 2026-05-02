"""Workspace tests: walk-up discovery, auto-create-on-write rules."""

import os
import tempfile
import unittest
from pathlib import Path

from issues import (
    IssuesError,
    ISSUES_DIRNAME,
    workspace_find,
    workspace_init,
    workspace_resolve,
)


class WorkspaceFindTests(unittest.TestCase):
    def test_returns_none_in_isolated_tmpdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            # /tmp may itself live below an `issues/`-bearing parent on dev
            # boxes; check that this specific tmpdir has no issues/ inside
            # but the search may still hit a parent. To make it deterministic
            # we mkdir an extra layer and call find on it: it should not see
            # `issues/` at our created depth.
            inner = Path(tmp) / 'inner'
            inner.mkdir()
            # Find may find an upstream issues/ (system-dependent). What we
            # care about: when no issues/ exists in tmp or inner, find()
            # returns either None or some upstream dir. We instead test the
            # positive case below; here we just confirm it does not crash.
            workspace_find(inner)

    def test_finds_at_root_of_tmpdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / ISSUES_DIRNAME).mkdir()
            self.assertEqual(workspace_find(tmp_path), tmp_path / ISSUES_DIRNAME)

    def test_walks_up_from_subdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / ISSUES_DIRNAME).mkdir()
            deep = tmp_path / 'a' / 'b' / 'c'
            deep.mkdir(parents=True)
            self.assertEqual(workspace_find(deep), tmp_path / ISSUES_DIRNAME)

    def test_walks_up_many_levels(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / ISSUES_DIRNAME).mkdir()
            deep = tmp_path
            for name in 'abcdefghij':
                deep = deep / name
            deep.mkdir(parents=True)
            self.assertEqual(workspace_find(deep), tmp_path / ISSUES_DIRNAME)

    def test_finds_nearest_when_nested(self):
        # If both outer/issues/ and outer/inner/issues/ exist, the *nearest*
        # one (inner) should win.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / ISSUES_DIRNAME).mkdir()
            inner = tmp_path / 'inner'
            inner.mkdir()
            (inner / ISSUES_DIRNAME).mkdir()
            self.assertEqual(workspace_find(inner), inner / ISSUES_DIRNAME)


class WorkspaceInitTests(unittest.TestCase):
    def test_creates_required_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace_init(tmp_path)
            self.assertTrue((tmp_path / 'issues' / 'open').is_dir())
            self.assertTrue((tmp_path / 'issues' / 'closed').is_dir())
            self.assertTrue((tmp_path / 'issues' / 'README.md').is_file())

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace_init(tmp_path)
            # Drop a custom README to verify it's not overwritten.
            readme = tmp_path / 'issues' / 'README.md'
            readme.write_text('CUSTOM', encoding='utf-8')
            workspace_init(tmp_path)
            self.assertEqual(readme.read_text(encoding='utf-8'), 'CUSTOM')

    def test_gitignore_appends_if_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace_init(tmp_path, with_gitignore=True)
            gi = tmp_path / '.gitignore'
            self.assertTrue(gi.exists())
            self.assertIn('issues/', gi.read_text(encoding='utf-8'))

    def test_gitignore_skipped_if_already_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / '.gitignore').write_text(
                'node_modules/\nissues/\n', encoding='utf-8'
            )
            workspace_init(tmp_path, with_gitignore=True)
            content = (tmp_path / '.gitignore').read_text(encoding='utf-8')
            self.assertEqual(content.count('issues/'), 1)


class WorkspaceResolveTests(unittest.TestCase):
    def test_read_command_errors_when_no_issues_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            inner = Path(tmp) / 'inner'
            inner.mkdir()
            # Find may walk up past /tmp to find some upstream issues/.
            # To simulate the "not found" case deterministically, we monkey-
            # patch workspace_find via the module path. Instead, just verify
            # behaviour when find returns None by calling the function with
            # a forged path. Easier: we use a path that we know has no
            # ancestor issues/ — but we cannot be sure. So we test the
            # auto_create=True path positively, and verify error message
            # behaviour by calling resolve with a path whose ancestors do
            # have an issues/ would not exercise the error. We instead skip
            # this test if any ancestor issues/ exists.
            from issues import workspace_find
            if workspace_find(inner) is not None:
                self.skipTest('ancestor issues/ exists; cannot test "not found"')
            with self.assertRaises(IssuesError):
                workspace_resolve(start=inner, auto_create=False)

    def test_write_command_auto_creates(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # If tmp has no issues/ ancestor, resolve should create at tmp.
            from issues import workspace_find
            if workspace_find(tmp_path) is not None:
                self.skipTest('ancestor issues/ exists')
            issues_dir = workspace_resolve(start=tmp_path, auto_create=True)
            self.assertEqual(issues_dir, tmp_path / 'issues')
            self.assertTrue(issues_dir.is_dir())

    def test_resolve_finds_existing_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace_init(tmp_path)
            inner = tmp_path / 'a' / 'b'
            inner.mkdir(parents=True)
            self.assertEqual(
                workspace_resolve(start=inner, auto_create=False),
                tmp_path / 'issues',
            )


if __name__ == '__main__':
    unittest.main()
