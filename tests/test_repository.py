"""Repository tests: O_EXCL retry, atomic write, .next-id maintenance."""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from issues import (
    IssuesError,
    NEXT_ID_FILENAME,
    repo_create,
    repo_existing_numbers,
    repo_find_path,
    repo_format_filename,
    repo_initial_next_id,
    repo_open_dir,
    repo_read,
    repo_read_next_id,
    repo_write_next_id,
    workspace_init,
    _atomic_write_text,
    _excl_write_text,
)


class IsolatedRepoMixin:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.issues_dir = workspace_init(self.base)


class RepoBasicsTests(IsolatedRepoMixin, unittest.TestCase):
    def test_create_writes_file_and_next_id(self):
        task, path = repo_create(self.issues_dir, title='Hello world', body='b')
        self.assertEqual(task['number'], 1)
        self.assertEqual(path.name, '001-hello-world.md')
        self.assertTrue(path.exists())
        self.assertEqual(repo_read_next_id(self.issues_dir), 2)

    def test_create_increments_id(self):
        for n in range(1, 6):
            task, path = repo_create(self.issues_dir, title=f'Task {n}', body='')
            self.assertEqual(task['number'], n)
        self.assertEqual(repo_read_next_id(self.issues_dir), 6)

    def test_existing_numbers_scans_both_dirs(self):
        repo_create(self.issues_dir, title='one', body='')
        repo_create(self.issues_dir, title='two', body='')
        # Manually move one to closed/ to verify scanning both dirs.
        path = repo_find_path(self.issues_dir, 1)
        target = self.issues_dir / 'closed' / path.name
        os.rename(path, target)
        self.assertEqual(
            repo_existing_numbers(self.issues_dir), {1, 2},
        )

    def test_initial_next_id_respects_dotfile(self):
        # If .next-id is ahead of existing files, use it.
        repo_create(self.issues_dir, title='one', body='')
        repo_write_next_id(self.issues_dir, 100)
        self.assertEqual(repo_initial_next_id(self.issues_dir), 100)

    def test_initial_next_id_respects_existing_files(self):
        # If existing files exceed .next-id, files win.
        repo_create(self.issues_dir, title='one', body='')
        repo_write_next_id(self.issues_dir, 1)  # stale
        self.assertEqual(repo_initial_next_id(self.issues_dir), 2)

    def test_create_then_read_round_trip(self):
        task, _ = repo_create(
            self.issues_dir,
            title='Round-trip me',
            body='Body content here.\n',
            labels=['x', 'y'],
            blocked_by=[1],
            priority=1,
            task_type='task',
        )
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertEqual(loaded['title'], 'Round-trip me')
        self.assertEqual(loaded['labels'], ['x', 'y'])
        self.assertEqual(loaded['blocked_by'], [1])
        self.assertEqual(loaded['priority'], 1)

    def test_read_missing_raises(self):
        with self.assertRaises(IssuesError):
            repo_read(self.issues_dir, 999)


class ConcurrentCreateTests(IsolatedRepoMixin, unittest.TestCase):
    def test_o_excl_collision_retries_with_next_id(self):
        # Pre-create a file at slot 1 with a *different* slug than what the
        # next create would produce, so the candidate-bump path is exercised.
        open_dir = repo_open_dir(self.issues_dir)
        squatter = open_dir / repo_format_filename(1, 'squatter')
        squatter.write_text(
            '---\nnumber: 1\ntitle: squatter\ntype: task\nparent: null\n'
            'labels: []\nblockedBy: []\npriority: null\nstate: open\n'
            'stateReason: null\ncreatedAt: x\nclosedAt: null\nassignees: []\n'
            '---\n',
            encoding='utf-8',
        )
        task, path = repo_create(self.issues_dir, title='New thing', body='')
        # Candidate started at 2 (because file 1 is in use).
        self.assertEqual(task['number'], 2)
        self.assertEqual(path.name, '002-new-thing.md')

    def test_concurrent_creates_unique_ids(self):
        # Spawn N threads doing repo_create simultaneously. Verify all IDs
        # are unique and equal to range(1, N+1).
        results = []
        errors = []
        n_threads = 16
        barrier = threading.Barrier(n_threads)

        def worker(i):
            try:
                barrier.wait(timeout=5)
                task, _ = repo_create(
                    self.issues_dir, title=f'Concurrent {i}', body='',
                )
                results.append(task['number'])
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), list(range(1, n_threads + 1)))
        self.assertEqual(len(set(results)), n_threads)

    def test_o_excl_raises_file_exists_error(self):
        open_dir = repo_open_dir(self.issues_dir)
        target = open_dir / 'foo.md'
        _excl_write_text(target, 'first')
        with self.assertRaises(FileExistsError):
            _excl_write_text(target, 'second')
        # First file untouched.
        self.assertEqual(target.read_text(encoding='utf-8'), 'first')


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_writes_full_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'out.txt'
            _atomic_write_text(target, 'hello world\n')
            self.assertEqual(target.read_text(encoding='utf-8'), 'hello world\n')

    def test_atomic_write_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'out.txt'
            target.write_text('old', encoding='utf-8')
            _atomic_write_text(target, 'new')
            self.assertEqual(target.read_text(encoding='utf-8'), 'new')

    def test_atomic_write_no_partial_file_on_failure(self):
        # Force os.rename to fail; verify (a) target is not partially written
        # and (b) no temp file is left in the directory.
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'out.txt'

            def boom(*args, **kwargs):
                raise OSError('simulated rename failure')

            with mock.patch('issues.os.rename', side_effect=boom):
                with self.assertRaises(OSError):
                    _atomic_write_text(target, 'should not land')

            self.assertFalse(target.exists(),
                             'target should not exist on failure')
            leftovers = list(Path(tmp).iterdir())
            self.assertEqual(leftovers, [],
                             f'unexpected files left in tmp: {leftovers}')

    def test_atomic_write_creates_parent_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'a' / 'b' / 'out.txt'
            _atomic_write_text(target, 'x')
            self.assertTrue(target.exists())


class MissingIssuesDirTests(unittest.TestCase):
    def test_existing_numbers_empty_when_dirs_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            issues_dir = Path(tmp) / 'issues'
            issues_dir.mkdir()
            # No open/ or closed/ subdirs.
            self.assertEqual(repo_existing_numbers(issues_dir), set())

    def test_create_bootstraps_subdirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            issues_dir = Path(tmp) / 'issues'
            issues_dir.mkdir()
            task, path = repo_create(issues_dir, title='first', body='')
            self.assertEqual(task['number'], 1)
            self.assertTrue((issues_dir / 'open').is_dir())
            self.assertTrue((issues_dir / 'closed').is_dir())


if __name__ == '__main__':
    unittest.main()
