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
    repo_delete,
    repo_edit,
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


class RepoEditTests(IsolatedRepoTestCase):
    """repo_edit: field preservation, composition, atomic write."""

    def _make_task(self, **kwargs):
        defaults = dict(title='Original', body='Body.\n', labels=['a', 'b'],
                        blocked_by=[5, 6], priority=3)
        defaults.update(kwargs)
        task, _ = repo_create(self.issues_dir, **defaults)
        return task

    def test_edit_title_only(self):
        task = self._make_task()
        repo_edit(self.issues_dir, task['number'], title='Renamed')
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertEqual(loaded['title'], 'Renamed')
        # Everything else unchanged.
        self.assertEqual(loaded['body'], 'Body.\n')
        self.assertEqual(loaded['labels'], ['a', 'b'])
        self.assertEqual(loaded['blocked_by'], [5, 6])
        self.assertEqual(loaded['priority'], 3)

    def test_edit_body_replaces_body(self):
        task = self._make_task()
        repo_edit(self.issues_dir, task['number'], body='New body.')
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertIn('New body', loaded['body'])
        # Other frontmatter preserved.
        self.assertEqual(loaded['labels'], ['a', 'b'])

    def test_edit_body_preserves_comments_section(self):
        task = self._make_task()
        # Append a comment first.
        repo_append_comment(self.issues_dir, task['number'],
                            body='Keep this comment', author='alice')
        # Now edit the body.
        repo_edit(self.issues_dir, task['number'], body='Wholly new body.')
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertIn('Wholly new body', loaded['body'])
        self.assertIn('Keep this comment', loaded['comments_raw'])
        self.assertIn('## Comments', loaded['comments_raw'])
        comments = task_parse_comments(loaded.get('comments_raw', ''))
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]['body'], 'Keep this comment')

    def test_edit_add_label(self):
        task = self._make_task()
        repo_edit(self.issues_dir, task['number'], add_labels=['c'])
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertIn('c', loaded['labels'])
        self.assertIn('a', loaded['labels'])

    def test_edit_remove_label(self):
        task = self._make_task()
        repo_edit(self.issues_dir, task['number'], remove_labels=['a'])
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertNotIn('a', loaded['labels'])
        self.assertIn('b', loaded['labels'])

    def test_edit_add_then_remove_same_label_is_noop(self):
        """add-label X then remove-label X in one call leaves labels unchanged."""
        task = self._make_task(labels=['x'])
        repo_edit(self.issues_dir, task['number'],
                  add_labels=['y'], remove_labels=['y'])
        loaded, _ = repo_read(self.issues_dir, task['number'])
        # 'y' was added then removed: should not be present.
        self.assertNotIn('y', loaded['labels'])
        self.assertIn('x', loaded['labels'])

    def test_edit_remove_absent_label_is_noop(self):
        task = self._make_task(labels=['a'])
        # Removing a label that doesn't exist is silently ignored.
        repo_edit(self.issues_dir, task['number'], remove_labels=['nonexistent'])
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertEqual(loaded['labels'], ['a'])

    def test_edit_add_blocked_by(self):
        task = self._make_task(blocked_by=[1])
        repo_edit(self.issues_dir, task['number'], add_blocked_by=[2])
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertIn(1, loaded['blocked_by'])
        self.assertIn(2, loaded['blocked_by'])

    def test_edit_remove_blocked_by(self):
        task = self._make_task(blocked_by=[1, 2])
        repo_edit(self.issues_dir, task['number'], remove_blocked_by=[1])
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertNotIn(1, loaded['blocked_by'])
        self.assertIn(2, loaded['blocked_by'])

    def test_edit_remove_absent_blocked_by_is_noop(self):
        task = self._make_task(blocked_by=[1])
        repo_edit(self.issues_dir, task['number'], remove_blocked_by=[99])
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertEqual(loaded['blocked_by'], [1])

    def test_edit_set_priority(self):
        task = self._make_task(priority=5)
        repo_edit(self.issues_dir, task['number'], priority=0)
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertEqual(loaded['priority'], 0)

    def test_edit_priority_none_clears_priority(self):
        task = self._make_task(priority=3)
        repo_edit(self.issues_dir, task['number'], priority=None)
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertIsNone(loaded['priority'])

    def test_edit_priority_unset_leaves_priority_unchanged(self):
        """Omitting priority (using _UNSET default) preserves existing value."""
        task = self._make_task(priority=7)
        repo_edit(self.issues_dir, task['number'], title='Renamed')
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertEqual(loaded['priority'], 7)

    def test_edit_multiple_flags_compose(self):
        """All flags in one call apply atomically."""
        task = self._make_task(title='Old', labels=['a'], priority=5,
                               blocked_by=[10])
        repo_edit(self.issues_dir, task['number'],
                  title='New',
                  add_labels=['b'], remove_labels=['a'],
                  add_blocked_by=[20], remove_blocked_by=[10],
                  priority=1)
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertEqual(loaded['title'], 'New')
        self.assertEqual(loaded['labels'], ['b'])
        self.assertEqual(loaded['blocked_by'], [20])
        self.assertEqual(loaded['priority'], 1)

    def test_edit_preserves_state_and_metadata(self):
        """State, created_at, closed_at, assignees, type are not touched."""
        task, _ = repo_create(self.issues_dir, title='Meta', body='b',
                               task_type='prd', assignees=['bob'])
        repo_close(self.issues_dir, task['number'])
        repo_edit(self.issues_dir, task['number'], title='Meta renamed')
        loaded, _ = repo_read(self.issues_dir, task['number'])
        self.assertEqual(loaded['state'], 'closed')
        self.assertEqual(loaded['type'], 'prd')
        self.assertEqual(loaded['assignees'], ['bob'])
        self.assertEqual(loaded['created_at'], task['created_at'])

    def test_edit_uses_atomic_write(self):
        """Simulated rename failure leaves no partial file."""
        task = self._make_task()
        _, path = repo_read(self.issues_dir, task['number'])
        original_text = path.read_text(encoding='utf-8')

        def boom(*_args, **_kwargs):
            raise OSError('simulated rename failure')

        with mock.patch('issues.os.rename', side_effect=boom):
            with self.assertRaises(OSError):
                repo_edit(self.issues_dir, task['number'], title='Should not land')

        # File must still contain the original content.
        self.assertEqual(path.read_text(encoding='utf-8'), original_text)
        # No temp files left in the directory.
        parent = path.parent
        leftovers = [f for f in os.listdir(parent) if f.endswith('.tmp')]
        self.assertEqual(leftovers, [])

    def test_edit_missing_task_raises(self):
        with self.assertRaises(IssuesError):
            repo_edit(self.issues_dir, 999, title='Ghost')


class RepoDeleteTests(IsolatedRepoTestCase):
    """repo_delete: unlink, IssuesError on missing, .next-id untouched."""

    def test_delete_removes_file(self):
        task, _ = repo_create(self.issues_dir, title='To delete', body='b')
        path = repo_find_path(self.issues_dir, task['number'])
        self.assertIsNotNone(path)
        repo_delete(self.issues_dir, task['number'])
        self.assertFalse(path.exists())

    def test_delete_then_read_raises(self):
        task, _ = repo_create(self.issues_dir, title='Gone', body='b')
        repo_delete(self.issues_dir, task['number'])
        with self.assertRaises(IssuesError):
            repo_read(self.issues_dir, task['number'])

    def test_delete_missing_task_raises(self):
        with self.assertRaises(IssuesError):
            repo_delete(self.issues_dir, 999)

    def test_delete_does_not_decrement_next_id(self):
        task, _ = repo_create(self.issues_dir, title='Will be deleted', body='b')
        next_id_after_create = repo_read_next_id(self.issues_dir)
        repo_delete(self.issues_dir, task['number'])
        next_id_after_delete = repo_read_next_id(self.issues_dir)
        self.assertEqual(next_id_after_create, next_id_after_delete)

    def test_delete_id_not_reused(self):
        """After deleting task #1, the next created task should get #2."""
        task1, _ = repo_create(self.issues_dir, title='First', body='b')
        self.assertEqual(task1['number'], 1)
        repo_delete(self.issues_dir, 1)
        task2, _ = repo_create(self.issues_dir, title='Second', body='b')
        self.assertEqual(task2['number'], 2)

    def test_delete_works_on_closed_task(self):
        task, _ = repo_create(self.issues_dir, title='Close then delete', body='b')
        repo_close(self.issues_dir, task['number'])
        path = repo_find_path(self.issues_dir, task['number'])
        self.assertIsNotNone(path)
        repo_delete(self.issues_dir, task['number'])
        self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
