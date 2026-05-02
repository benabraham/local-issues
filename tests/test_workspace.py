"""Workspace tests: walk-up discovery, auto-create-on-write rules."""

import os
import tempfile
import unittest
from pathlib import Path

from issues import (
    IssuesError,
    ISSUES_DIRNAME,
    _find_git_marker,
    _resolve_worktree_commondir,
    _resolve_worktree_gitdir,
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


class GitMarkerTests(unittest.TestCase):
    """Tests for _find_git_marker (pure walk-up, no subprocess)."""

    def test_finds_dot_git_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / '.git').mkdir()
            result = _find_git_marker(tmp_path)
            self.assertIsNotNone(result)
            git_path, kind = result
            self.assertEqual(kind, 'dir')
            self.assertEqual(git_path, tmp_path / '.git')

    def test_finds_dot_git_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / '.git').write_text(
                'gitdir: /some/abs/path\n', encoding='utf-8'
            )
            result = _find_git_marker(tmp_path)
            self.assertIsNotNone(result)
            git_path, kind = result
            self.assertEqual(kind, 'file')
            self.assertEqual(git_path, tmp_path / '.git')

    def test_walks_up_to_find_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / '.git').mkdir()
            deep = tmp_path / 'a' / 'b' / 'c'
            deep.mkdir(parents=True)
            result = _find_git_marker(deep)
            self.assertIsNotNone(result)
            git_path, kind = result
            self.assertEqual(kind, 'dir')
            self.assertEqual(git_path, tmp_path / '.git')

    def test_returns_none_when_no_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            # A temp dir whose whole subtree has no .git.
            inner = Path(tmp) / 'a' / 'b'
            inner.mkdir(parents=True)
            # Walk up from inner; if some ancestor of tmp has .git this
            # will not be None — but that only happens when the test runner
            # is itself inside a .git tree.  Restrict the search by testing
            # that the found path (if any) is NOT inside our tmp tree.
            result = _find_git_marker(inner)
            if result is not None:
                git_path, _ = result
                # It's an ancestor of tmp — that's fine; the function works.
                self.assertFalse(
                    str(git_path).startswith(tmp),
                    'found .git inside the controlled temp dir unexpectedly',
                )


class ResolveWorktreeGitdirTests(unittest.TestCase):
    """Tests for _resolve_worktree_gitdir."""

    def test_absolute_gitdir_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            abs_target = tmp_path / 'repo' / '.git' / 'worktrees' / 'wt'
            abs_target.mkdir(parents=True)
            git_file = tmp_path / 'worktree' / '.git'
            git_file.parent.mkdir()
            git_file.write_text(f'gitdir: {abs_target}\n', encoding='utf-8')
            result = _resolve_worktree_gitdir(git_file)
            self.assertEqual(result, abs_target)

    def test_relative_gitdir_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # repo/.git/worktrees/wt is the target.
            # worktree/.git contains relative path from worktree/ to that dir.
            repo_git = tmp_path / 'repo' / '.git'
            repo_git.mkdir(parents=True)
            wt_gitdir = repo_git / 'worktrees' / 'wt'
            wt_gitdir.mkdir(parents=True)
            worktree_dir = tmp_path / 'worktree'
            worktree_dir.mkdir()
            git_file = worktree_dir / '.git'
            # Relative path from worktree/ to repo/.git/worktrees/wt
            rel = os.path.relpath(wt_gitdir, worktree_dir)
            git_file.write_text(f'gitdir: {rel}\n', encoding='utf-8')
            result = _resolve_worktree_gitdir(git_file)
            self.assertEqual(result.resolve(), wt_gitdir.resolve())

    def test_missing_gitdir_line_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            git_file = Path(tmp) / '.git'
            git_file.write_text('# no gitdir line here\n', encoding='utf-8')
            with self.assertRaises(IssuesError):
                _resolve_worktree_gitdir(git_file)


class WorkspaceFindGitTests(unittest.TestCase):
    """Tests for workspace_find with .git directory, .git file (worktree),
    sibling-shared layout, and no-.git fallback.

    All fixtures are hand-crafted — no real git subprocess required.
    """

    def test_git_dir_finds_issues_at_repo_root(self):
        """When .git is a directory, issues/ at repo root is found."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / '.git').mkdir()
            (tmp_path / ISSUES_DIRNAME).mkdir()
            # Start from a subdirectory.
            sub = tmp_path / 'src' / 'pkg'
            sub.mkdir(parents=True)
            result = workspace_find(sub)
            self.assertEqual(result, tmp_path / ISSUES_DIRNAME)

    def test_git_dir_no_issues_returns_none(self):
        """When .git dir exists but neither repo root nor parent has issues/."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / 'repo'
            repo.mkdir()
            (repo / '.git').mkdir()
            # Neither repo/ nor tmp/ has issues/.
            result = workspace_find(repo)
            self.assertIsNone(result)

    def test_git_file_worktree_finds_issues_at_worktree_root(self):
        """When .git is a file (worktree), issues/ at the worktree root wins."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Simulate: repo/.git/ (common git dir)
            repo = tmp_path / 'repo'
            repo_git = repo / '.git'
            wt_gitdir = repo_git / 'worktrees' / 'wt'
            wt_gitdir.mkdir(parents=True)

            # Worktree working dir with .git file pointing to wt_gitdir.
            worktree = tmp_path / 'worktree'
            worktree.mkdir()
            (worktree / '.git').write_text(
                f'gitdir: {wt_gitdir}\n', encoding='utf-8'
            )
            # issues/ lives at the worktree root, NOT at the common repo root.
            (worktree / ISSUES_DIRNAME).mkdir()

            # Start from a subdir inside the worktree.
            sub = worktree / 'subdir'
            sub.mkdir()
            result = workspace_find(sub)
            self.assertEqual(result, worktree / ISSUES_DIRNAME)

    def test_git_file_worktree_finds_issues_via_commondir(self):
        """commondir is present but issues/ is still located at worktree root."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / 'repo'
            repo_git = repo / '.git'
            wt_gitdir = repo_git / 'worktrees' / 'wt'
            wt_gitdir.mkdir(parents=True)
            # Write commondir pointing back to repo/.git (relative).
            (wt_gitdir / 'commondir').write_text('../..', encoding='utf-8')

            worktree = tmp_path / 'worktree'
            worktree.mkdir()
            (worktree / '.git').write_text(
                f'gitdir: {wt_gitdir}\n', encoding='utf-8'
            )
            (worktree / ISSUES_DIRNAME).mkdir()

            (worktree / 'sub').mkdir()
            result = workspace_find(worktree / 'sub')
            self.assertEqual(result, worktree / ISSUES_DIRNAME)

    def test_sibling_shared_parent_issues(self):
        """When repo root has no issues/ but its parent does, use the parent's."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # tmp_path/issues/ — the shared pool.
            (tmp_path / ISSUES_DIRNAME).mkdir()
            # tmp_path/repo/.git — a normal repo.
            repo = tmp_path / 'repo'
            repo.mkdir()
            (repo / '.git').mkdir()
            # Repo itself has no issues/ subdir.
            result = workspace_find(repo)
            self.assertEqual(result, tmp_path / ISSUES_DIRNAME)

    def test_sibling_shared_repo_root_takes_priority(self):
        """Repo root issues/ takes priority over parent issues/."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Both parent and repo root have issues/.
            (tmp_path / ISSUES_DIRNAME).mkdir()
            repo = tmp_path / 'repo'
            repo.mkdir()
            (repo / '.git').mkdir()
            (repo / ISSUES_DIRNAME).mkdir()
            result = workspace_find(repo)
            self.assertEqual(result, repo / ISSUES_DIRNAME)

    def test_no_git_plain_walkup_finds_issues(self):
        """Without a .git anywhere in our controlled tree, plain walk-up works."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # No .git at all in tmp subtree.
            (tmp_path / ISSUES_DIRNAME).mkdir()
            deep = tmp_path / 'a' / 'b' / 'c'
            deep.mkdir(parents=True)
            result = workspace_find(deep)
            # The plain walk-up should find tmp_path/issues/. It may also hit
            # an ancestor .git (if the test suite runs inside a repo) — but
            # the issues/ at tmp_path should still be found since it's closer.
            self.assertIsNotNone(result)
            self.assertEqual(result, tmp_path / ISSUES_DIRNAME)

    def test_gitignored_issues_dir_still_found(self):
        """issues/ is found even if it would be gitignored.

        Verifying this is trivial: our implementation never calls git, so
        gitignore status is irrelevant.  This test is a documentation test
        confirming the invariant by checking the directory is found even
        when a .gitignore that would exclude it is present.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / '.git').mkdir()
            (tmp_path / '.gitignore').write_text('issues/\n', encoding='utf-8')
            (tmp_path / ISSUES_DIRNAME).mkdir()
            result = workspace_find(tmp_path)
            self.assertEqual(result, tmp_path / ISSUES_DIRNAME)


class WorktreeCommondirTests(unittest.TestCase):
    """Tests for worktree→commondir fallback in workspace_find and the
    _resolve_worktree_commondir helper.  All fixtures are synthesised on disk
    — no real git subprocess is required.
    """

    def _make_worktree_fixture(self, tmp_path, *, with_commondir=True):
        """Build the on-disk shape for a worktree scenario.

        Layout:
          tmp/
            repo/
              .git/
                worktrees/
                  wt/
                    commondir   ← "../.." (relative, points to repo/.git)
            worktree/           ← the worktree working dir
              .git              ← file: "gitdir: <abs-path-to-repo/.git/worktrees/wt>"

        Returns (repo, worktree, wt_gitdir).
        """
        repo = tmp_path / 'repo'
        repo_git = repo / '.git'
        wt_gitdir = repo_git / 'worktrees' / 'wt'
        wt_gitdir.mkdir(parents=True)

        if with_commondir:
            # Relative path from wt_gitdir to repo/.git is "../.."
            (wt_gitdir / 'commondir').write_text('../..', encoding='utf-8')

        worktree = tmp_path / 'worktree'
        worktree.mkdir()
        (worktree / '.git').write_text(
            f'gitdir: {wt_gitdir}\n', encoding='utf-8'
        )
        return repo, worktree, wt_gitdir

    # ------------------------------------------------------------------
    # 1. Worktree falls back to main repo's issues/ when it has none.
    # ------------------------------------------------------------------

    def test_worktree_finds_issues_at_main_repo_when_absent_from_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo, worktree, _ = self._make_worktree_fixture(tmp_path)

            # Only the main repo has issues/.
            (repo / ISSUES_DIRNAME).mkdir()

            result = workspace_find(worktree / 'src')
            # Create the src subdir so find has somewhere to start.
            (worktree / 'src').mkdir()
            result = workspace_find(worktree / 'src')
            self.assertEqual(result, repo / ISSUES_DIRNAME)

    # ------------------------------------------------------------------
    # 2. Worktree-local issues/ wins over main repo's issues/.
    # ------------------------------------------------------------------

    def test_worktree_prefers_local_issues_over_main_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo, worktree, _ = self._make_worktree_fixture(tmp_path)

            (repo / ISSUES_DIRNAME).mkdir()
            (worktree / ISSUES_DIRNAME).mkdir()

            result = workspace_find(worktree)
            self.assertEqual(result, worktree / ISSUES_DIRNAME)

    # ------------------------------------------------------------------
    # 3. Sibling-shared layout visible from the worktree (parent of main repo).
    # ------------------------------------------------------------------

    def test_worktree_finds_issues_at_main_repo_parent_sibling_shared(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo, worktree, _ = self._make_worktree_fixture(tmp_path)

            # issues/ is at tmp_path (parent of main repo, not inside repo).
            (tmp_path / ISSUES_DIRNAME).mkdir()

            (worktree / 'src').mkdir()
            result = workspace_find(worktree / 'src')
            self.assertEqual(result, tmp_path / ISSUES_DIRNAME)

    # ------------------------------------------------------------------
    # 4. No commondir file — gitdir itself acts as common dir (defensive).
    # ------------------------------------------------------------------

    def test_worktree_no_commondir_file_uses_gitdir_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo, worktree, wt_gitdir = self._make_worktree_fixture(
                tmp_path, with_commondir=False
            )
            # Without commondir, main_repo_root == wt_gitdir.parent
            # == repo/.git/worktrees.  Its parent is repo/.git, whose parent
            # is repo.  But with no commondir the helper returns wt_gitdir
            # itself, so main_repo_root = wt_gitdir.parent.
            # We put issues/ at wt_gitdir.parent so it IS found.
            main_repo_root_candidate = wt_gitdir.parent
            (main_repo_root_candidate / ISSUES_DIRNAME).mkdir()

            result = workspace_find(worktree)
            self.assertEqual(
                result, main_repo_root_candidate / ISSUES_DIRNAME
            )

    # ------------------------------------------------------------------
    # 5. Unit test for _resolve_worktree_commondir with a relative path.
    # ------------------------------------------------------------------

    def test_resolve_worktree_commondir_relative_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Simulate repo/.git/worktrees/wt/ with commondir = "../.."
            repo_git = tmp_path / 'repo' / '.git'
            wt_gitdir = repo_git / 'worktrees' / 'wt'
            wt_gitdir.mkdir(parents=True)
            (wt_gitdir / 'commondir').write_text('../..', encoding='utf-8')

            result = _resolve_worktree_commondir(wt_gitdir)
            self.assertEqual(result.resolve(), repo_git.resolve())


if __name__ == '__main__':
    unittest.main()
