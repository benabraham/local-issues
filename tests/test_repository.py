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
    repo_append_comment,
    repo_close,
    repo_create,
    repo_existing_numbers,
    repo_find_path,
    repo_format_filename,
    repo_initial_next_id,
    repo_open_dir,
    repo_read,
    repo_read_next_id,
    repo_reopen,
    repo_write_next_id,
    task_parse_comments,
    workspace_init,
    _atomic_write_text,
    _excl_write_text,
)


class IsolatedRepoTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.issues_dir = workspace_init(self.base)


class RepoBasicsTests(IsolatedRepoTestCase):
    def test_create_writes_file_and_next_id(self):
        task, path = repo_create(self.issues_dir, title='Hello world', body='b')
        self.assertEqual(task['number'], 1)
        self.assertEqual(path.name, '001-hello-world.md')
        self.assertTrue(path.exists())
        self.assertEqual(repo_read_next_id(self.issues_dir), 2)

    def test_create_increments_id(self):
        for n in range(1, 6):
            task, _ = repo_create(self.issues_dir, title=f'Task {n}', body='')
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


class ConcurrentCreateTests(IsolatedRepoTestCase):
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

            def boom(*_args, **_kwargs):
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


class RepoStateTransitionTests(IsolatedRepoTestCase):
    """repo_close and repo_reopen state transitions."""

    def test_close_moves_file_to_closed_dir(self):
        task, open_path = repo_create(self.issues_dir, title='Close me', body='b')
        self.assertTrue(open_path.exists())
        _, closed_path = repo_close(self.issues_dir, task['number'], reason='completed')
        self.assertTrue(closed_path.exists())
        self.assertIn('closed', str(closed_path))
        self.assertFalse(open_path.exists())

    def test_close_updates_frontmatter_state(self):
        task, _ = repo_create(self.issues_dir, title='State test', body='b')
        repo_close(self.issues_dir, task['number'], reason='completed')
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertEqual(loaded['state'], 'closed')
        self.assertEqual(loaded['state_reason'], 'completed')
        self.assertIsNotNone(loaded['closed_at'])

    def test_close_default_reason_is_completed(self):
        task, _ = repo_create(self.issues_dir, title='Default reason', body='b')
        repo_close(self.issues_dir, task['number'])
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertEqual(loaded['state_reason'], 'completed')

    def test_close_reason_not_planned(self):
        task, _ = repo_create(self.issues_dir, title='Not planned', body='b')
        repo_close(self.issues_dir, task['number'], reason='not_planned')
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertEqual(loaded['state_reason'], 'not_planned')

    def test_close_preserves_other_fields(self):
        task, _ = repo_create(
            self.issues_dir, title='Preserve fields',
            body='Body.\n', labels=['x'], priority=2,
        )
        repo_close(self.issues_dir, task['number'], reason='completed')
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertEqual(loaded['title'], 'Preserve fields')
        self.assertEqual(loaded['labels'], ['x'])
        self.assertEqual(loaded['priority'], 2)
        self.assertEqual(loaded['body'], 'Body.\n')

    def test_close_already_closed_raises(self):
        task, _ = repo_create(self.issues_dir, title='Already closed', body='b')
        repo_close(self.issues_dir, task['number'], reason='completed')
        with self.assertRaises(IssuesError):
            repo_close(self.issues_dir, task['number'], reason='completed')

    def test_close_with_comment_appends_comment(self):
        task, _ = repo_create(self.issues_dir, title='Comment on close', body='b')
        repo_close(
            self.issues_dir, task['number'],
            reason='completed',
            comment_body='Done!',
            comment_author='alice',
        )
        loaded, _ = repo_read(self.issues_dir, task['number'])
        comments = task_parse_comments(loaded.get('comments_raw', ''))
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]['body'], 'Done!')
        self.assertEqual(comments[0]['author'], 'alice')

    def test_close_with_comment_timestamp_matches_closed_at(self):
        task, _ = repo_create(self.issues_dir, title='Timestamp match', body='b')
        repo_close(
            self.issues_dir, task['number'],
            reason='completed',
            comment_body='Checking timestamps.',
            comment_author='alice',
        )
        loaded, _ = repo_read(self.issues_dir, task['number'])
        comments = task_parse_comments(loaded.get('comments_raw', ''))
        self.assertEqual(comments[0]['timestamp'], loaded['closed_at'])

    def test_reopen_moves_file_to_open_dir(self):
        task, _ = repo_create(self.issues_dir, title='Reopen me', body='b')
        repo_close(self.issues_dir, task['number'], reason='completed')
        _, closed_path = repo_read(self.issues_dir, task['number'])
        self.assertIn('closed', str(closed_path))
        _, open_path = repo_reopen(self.issues_dir, task['number'])
        self.assertTrue(open_path.exists())
        self.assertIn('open', str(open_path))
        self.assertFalse(closed_path.exists())

    def test_reopen_updates_frontmatter(self):
        task, _ = repo_create(self.issues_dir, title='Reopen state', body='b')
        repo_close(self.issues_dir, task['number'], reason='completed')
        repo_reopen(self.issues_dir, task['number'])
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertEqual(loaded['state'], 'open')
        self.assertEqual(loaded['state_reason'], 'reopened')
        self.assertIsNone(loaded['closed_at'])

    def test_reopen_already_open_raises(self):
        task, _ = repo_create(self.issues_dir, title='Already open', body='b')
        with self.assertRaises(IssuesError):
            repo_reopen(self.issues_dir, task['number'])

    def test_close_reopen_close_cycle(self):
        """Can close, reopen, and close again."""
        task, _ = repo_create(self.issues_dir, title='Cycle test', body='b')
        repo_close(self.issues_dir, task['number'], reason='completed')
        repo_reopen(self.issues_dir, task['number'])
        repo_close(self.issues_dir, task['number'], reason='not_planned')
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertEqual(loaded['state'], 'closed')
        self.assertEqual(loaded['state_reason'], 'not_planned')


class RepoCommentTests(IsolatedRepoTestCase):
    """repo_append_comment."""

    def test_append_comment_creates_comments_section(self):
        task, _ = repo_create(self.issues_dir, title='No comments yet', body='b')
        repo_append_comment(
            self.issues_dir, task['number'], body='First comment', author='alice',
        )
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertIn('## Comments', loaded.get('comments_raw', ''))

    def test_append_comment_has_correct_author_and_body(self):
        task, _ = repo_create(self.issues_dir, title='t', body='b')
        repo_append_comment(
            self.issues_dir, task['number'], body='My comment', author='bob',
        )
        loaded, _ = repo_read(self.issues_dir, task['number'])
        comments = task_parse_comments(loaded.get('comments_raw', ''))
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]['author'], 'bob')
        self.assertEqual(comments[0]['body'], 'My comment')

    def test_append_comment_appends_to_existing(self):
        task, _ = repo_create(self.issues_dir, title='t', body='b')
        repo_append_comment(
            self.issues_dir, task['number'], body='First', author='alice',
        )
        repo_append_comment(
            self.issues_dir, task['number'], body='Second', author='bob',
        )
        loaded, _ = repo_read(self.issues_dir, task['number'])
        comments = task_parse_comments(loaded.get('comments_raw', ''))
        self.assertEqual(len(comments), 2)
        self.assertEqual(comments[0]['body'], 'First')
        self.assertEqual(comments[1]['body'], 'Second')

    def test_append_comment_preserves_frontmatter(self):
        task, _ = repo_create(
            self.issues_dir, title='Preserve FM', body='Body.\n',
            labels=['a'], priority=3,
        )
        repo_append_comment(
            self.issues_dir, task['number'], body='Comment', author='alice',
        )
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertEqual(loaded['title'], 'Preserve FM')
        self.assertEqual(loaded['labels'], ['a'])
        self.assertEqual(loaded['priority'], 3)
        self.assertEqual(loaded['body'], 'Body.\n')

    def test_append_comment_works_on_closed_task(self):
        task, _ = repo_create(self.issues_dir, title='Closed task', body='b')
        repo_close(self.issues_dir, task['number'], reason='completed')
        repo_append_comment(
            self.issues_dir, task['number'], body='Post-close comment', author='alice',
        )
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertEqual(loaded['state'], 'closed')
        comments = task_parse_comments(loaded.get('comments_raw', ''))
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]['body'], 'Post-close comment')

    def test_append_comment_file_stays_in_same_dir(self):
        task, _ = repo_create(self.issues_dir, title='Stay put', body='b')
        _, path_before = repo_read(self.issues_dir, task['number'])
        repo_append_comment(
            self.issues_dir, task['number'], body='Comment', author='alice',
        )
        _, path_after = repo_read(self.issues_dir, task['number'])
        self.assertEqual(path_before, path_after)


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
            task, _ = repo_create(issues_dir, title='first', body='')
            self.assertEqual(task['number'], 1)
            self.assertTrue((issues_dir / 'open').is_dir())
            self.assertTrue((issues_dir / 'closed').is_dir())


if __name__ == '__main__':
    unittest.main()
