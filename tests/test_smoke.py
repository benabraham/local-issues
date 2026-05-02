"""Smoke tests: invoke the CLI via subprocess against a fixture project."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / 'src'


def run_cli(args, cwd, stdin=None, env=None):
    """Invoke `python -m issues ...` against the given cwd."""
    base_env = os.environ.copy()
    base_env.update(env or {})
    # Make the in-tree package importable.
    base_env['PYTHONPATH'] = str(SRC_DIR) + os.pathsep + base_env.get('PYTHONPATH', '')
    return subprocess.run(
        [sys.executable, '-m', 'issues', *args],
        cwd=str(cwd),
        input=stdin,
        env=base_env,
        capture_output=True,
        text=True,
    )


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = Path(self.tmp.name)

    def _init(self, *extra):
        result = run_cli(['init', *extra], cwd=self.cwd)
        self.assertEqual(result.returncode, 0,
                         f'init failed: {result.stderr}')
        return result

    def test_init_creates_layout(self):
        self._init()
        self.assertTrue((self.cwd / 'issues' / 'open').is_dir())
        self.assertTrue((self.cwd / 'issues' / 'closed').is_dir())
        self.assertTrue((self.cwd / 'issues' / 'README.md').is_file())

    def test_init_gitignore(self):
        self._init('--gitignore')
        gi = (self.cwd / '.gitignore').read_text(encoding='utf-8')
        self.assertIn('issues/', gi)

    def test_create_with_body_flag(self):
        self._init()
        result = run_cli(
            ['create', '--title', 'First task', '--body', 'A body.'],
            cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.cwd / 'issues' / 'open' / '001-first-task.md'
        self.assertTrue(path.exists())
        content = path.read_text(encoding='utf-8')
        self.assertIn('title: First task', content)
        self.assertIn('A body.', content)
        # .next-id written.
        next_id = (self.cwd / 'issues' / '.next-id').read_text(encoding='utf-8').strip()
        self.assertEqual(next_id, '2')

    def test_create_with_all_flags(self):
        self._init()
        result = run_cli(
            ['create', '--title', 'Big task',
             '--body', 'b',
             '--label', 'foo', '--label', 'bar',
             '--priority', '1',
             '--type', 'prd',
             '--blocked-by', '5', '--blocked-by', '7',
             '--assignee', 'alice'],
            cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.cwd / 'issues' / 'open' / '001-big-task.md'
        content = path.read_text(encoding='utf-8')
        self.assertIn('labels: [foo, bar]', content)
        self.assertIn('priority: 1', content)
        self.assertIn('type: prd', content)
        self.assertIn('blockedBy: [5, 7]', content)
        self.assertIn('assignees: [alice]', content)

    def test_create_with_body_file_stdin(self):
        self._init()
        result = run_cli(
            ['create', '--title', 'Stdin task', '--body-file', '-'],
            cwd=self.cwd,
            stdin='Body from stdin.\n',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.cwd / 'issues' / 'open' / '001-stdin-task.md'
        self.assertIn('Body from stdin.', path.read_text(encoding='utf-8'))

    def test_create_with_body_file_path(self):
        self._init()
        body_path = self.cwd / 'body.txt'
        body_path.write_text('Hello from a file.\n', encoding='utf-8')
        result = run_cli(
            ['create', '--title', 'File task', '--body-file', str(body_path)],
            cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.cwd / 'issues' / 'open' / '001-file-task.md'
        self.assertIn('Hello from a file.', path.read_text(encoding='utf-8'))

    def test_create_no_body_non_tty_errors(self):
        self._init()
        result = run_cli(
            ['create', '--title', 'No body'],
            cwd=self.cwd,
            stdin='',
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('body', result.stderr.lower())

    def test_create_auto_creates_workspace(self):
        # No init beforehand. Create should bootstrap the workspace.
        result = run_cli(
            ['create', '--title', 'Auto bootstrap', '--body', 'b'],
            cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.cwd / 'issues' / 'open' / '001-auto-bootstrap.md').is_file())

    def test_create_from_subdirectory_walks_up(self):
        self._init()
        sub = self.cwd / 'a' / 'b'
        sub.mkdir(parents=True)
        result = run_cli(
            ['create', '--title', 'From subdir', '--body', 'b'],
            cwd=sub,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        # File lands in the *root* issues/, not in the subdir.
        self.assertTrue((self.cwd / 'issues' / 'open' / '001-from-subdir.md').is_file())
        self.assertFalse((sub / 'issues').exists())

    def test_view_text(self):
        self._init()
        run_cli(
            ['create', '--title', 'Viewable', '--body', 'A body.',
             '--label', 'a', '--priority', '2'],
            cwd=self.cwd,
        )
        result = run_cli(['view', '1'], cwd=self.cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertIn('#1', out)
        self.assertIn('Viewable', out)
        self.assertIn('A body.', out)
        self.assertIn('priority', out)

    def test_view_json(self):
        self._init()
        run_cli(
            ['create', '--title', 'Json task', '--body', 'JSON body',
             '--label', 'l1', '--label', 'l2',
             '--priority', '0', '--type', 'task',
             '--blocked-by', '5'],
            cwd=self.cwd,
        )
        result = run_cli(['view', '1', '--json'], cwd=self.cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        d = json.loads(result.stdout)
        self.assertEqual(d['number'], 1)
        self.assertEqual(d['title'], 'Json task')
        self.assertEqual(d['state'], 'OPEN')  # uppercase
        self.assertEqual(d['labels'], ['l1', 'l2'])
        self.assertEqual(d['priority'], 0)
        self.assertEqual(d['blockedBy'], [5])
        self.assertEqual(d['type'], 'task')
        self.assertEqual(d['comments'], [])
        # camelCase keys.
        for key in ('createdAt', 'closedAt', 'stateReason', 'blockedBy'):
            self.assertIn(key, d)
        for key in d:
            self.assertNotIn('_', key)

    def test_view_missing_errors(self):
        self._init()
        result = run_cli(['view', '999'], cwd=self.cwd)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not found', result.stderr.lower())

    def test_view_without_workspace_errors(self):
        result = run_cli(['view', '1'], cwd=self.cwd)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('issues/', result.stderr)


class ListSmokeTests(unittest.TestCase):
    """End-to-end smoke tests for `issues list` and `issues status`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = Path(self.tmp.name)
        # Bootstrap and create a fixture set.
        run_cli(['init'], cwd=self.cwd)
        run_cli(
            ['create', '--title', 'Task one',
             '--body', 'b', '--priority', '2', '--label', 'bug'],
            cwd=self.cwd,
        )
        run_cli(
            ['create', '--title', 'Task two',
             '--body', 'b', '--priority', '0', '--label', 'bug',
             '--label', 'feature'],
            cwd=self.cwd,
        )
        run_cli(
            ['create', '--title', 'The PRD',
             '--body', 'b', '--type', 'prd'],
            cwd=self.cwd,
        )
        run_cli(
            ['create', '--title', 'Task three',
             '--body', 'b', '--label', 'feature'],
            cwd=self.cwd,
        )

    def _run(self, *args):
        return run_cli(list(args), cwd=self.cwd)

    # --- list (text) ---

    def test_list_default_shows_open_tasks_only(self):
        result = self._run('list')
        self.assertEqual(result.returncode, 0, result.stderr)
        # Default excludes PRDs.
        self.assertNotIn('The PRD', result.stdout)
        # Has both open tasks.
        self.assertIn('Task one', result.stdout)
        self.assertIn('Task two', result.stdout)
        self.assertIn('Task three', result.stdout)

    def test_list_default_sort_priority_then_id(self):
        result = self._run('list')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        # First data row (after header) should be Task two (priority=0).
        data_rows = lines[1:]  # skip header
        self.assertIn('Task two', data_rows[0])

    def test_list_type_prd_shows_only_prds(self):
        result = self._run('list', '--type', 'prd')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('The PRD', result.stdout)
        self.assertNotIn('Task one', result.stdout)
        self.assertNotIn('Task two', result.stdout)

    def test_list_type_all_shows_everything(self):
        result = self._run('list', '--type', 'all')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('The PRD', result.stdout)
        self.assertIn('Task one', result.stdout)

    def test_list_label_single_filter(self):
        result = self._run('list', '--label', 'bug')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Task one', result.stdout)
        self.assertIn('Task two', result.stdout)
        # Task three has only 'feature', not 'bug'.
        self.assertNotIn('Task three', result.stdout)

    def test_list_label_and_semantics(self):
        # Only Task two has both 'bug' AND 'feature'.
        result = self._run('list', '--label', 'bug', '--label', 'feature')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Task two', result.stdout)
        self.assertNotIn('Task one', result.stdout)
        self.assertNotIn('Task three', result.stdout)

    def test_list_state_closed_empty_no_error(self):
        result = self._run('list', '--state', 'closed')
        self.assertEqual(result.returncode, 0, result.stderr)
        # No tasks closed, but should still output the header.
        self.assertIn('TITLE', result.stdout)
        self.assertNotIn('Task one', result.stdout)

    def test_list_without_workspace_errors(self):
        import tempfile as _tmpfile
        with _tmpfile.TemporaryDirectory() as empty:
            result = run_cli(['list'], cwd=Path(empty))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('issues/', result.stderr)

    # --- list (json) ---

    def test_list_json_is_array(self):
        result = self._run('list', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)

    def test_list_json_default_excludes_prd(self):
        result = self._run('list', '--json')
        data = json.loads(result.stdout)
        types = {item['type'] for item in data}
        self.assertNotIn('prd', types)

    def test_list_json_fields_camel_case(self):
        result = self._run('list', '--json')
        data = json.loads(result.stdout)
        self.assertTrue(len(data) > 0, 'expected at least one task')
        item = data[0]
        for key in ('number', 'title', 'state', 'stateReason', 'createdAt',
                    'closedAt', 'labels', 'assignees', 'body',
                    'priority', 'parent', 'type', 'blockedBy', 'comments'):
            self.assertIn(key, item, f'missing key {key!r}')
        for key in item:
            self.assertNotIn('_', key, f'underscore in key {key!r}')

    def test_list_json_state_uppercase(self):
        result = self._run('list', '--json')
        data = json.loads(result.stdout)
        for item in data:
            self.assertIn(item['state'], ('OPEN', 'CLOSED'))

    def test_list_json_type_all(self):
        result = self._run('list', '--type', 'all', '--json')
        data = json.loads(result.stdout)
        types = {item['type'] for item in data}
        self.assertIn('prd', types)
        self.assertIn('task', types)

    def test_list_json_sort_order_priority_then_id(self):
        """First element should be the highest-priority task (Task two, p=0)."""
        result = self._run('list', '--json')
        data = json.loads(result.stdout)
        # Filter by priority not None, first should be priority=0 task.
        with_priority = [item for item in data if item['priority'] is not None]
        if with_priority:
            self.assertEqual(with_priority[0]['priority'], 0)

    # --- status ---

    def test_status_shows_counts(self):
        result = self._run('status')
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        # Should mention open tasks count.
        self.assertIn('Open tasks:', out)
        self.assertIn('Closed:', out)
        self.assertIn('Total:', out)

    def test_status_counts_are_correct(self):
        result = self._run('status')
        out = result.stdout
        # We created 3 tasks + 1 prd, all open.
        self.assertIn('Open tasks: 3', out)
        self.assertIn('Open PRDs:  1', out)
        self.assertIn('Closed:     0', out)
        self.assertIn('Total:      4', out)

    def test_status_without_workspace_errors(self):
        import tempfile as _tmpfile
        with _tmpfile.TemporaryDirectory() as empty:
            result = run_cli(['status'], cwd=Path(empty))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('issues/', result.stderr)


class CloseReopenSmokeTests(unittest.TestCase):
    """End-to-end smoke tests for `issues close` and `issues reopen`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = Path(self.tmp.name)
        run_cli(['init'], cwd=self.cwd)
        run_cli(
            ['create', '--title', 'Close me', '--body', 'A task.'],
            cwd=self.cwd,
        )

    def _run(self, *args):
        return run_cli(list(args), cwd=self.cwd)

    def test_close_moves_file_to_closed(self):
        result = self._run('close', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        # File should now be in closed/.
        self.assertTrue(
            (self.cwd / 'issues' / 'closed' / '001-close-me.md').exists()
        )
        self.assertFalse(
            (self.cwd / 'issues' / 'open' / '001-close-me.md').exists()
        )

    def test_close_updates_state_in_file(self):
        self._run('close', '1')
        content = (
            self.cwd / 'issues' / 'closed' / '001-close-me.md'
        ).read_text(encoding='utf-8')
        self.assertIn('state: closed', content)
        self.assertIn('stateReason: completed', content)

    def test_close_default_reason_completed(self):
        self._run('close', '1')
        content = (
            self.cwd / 'issues' / 'closed' / '001-close-me.md'
        ).read_text(encoding='utf-8')
        self.assertIn('stateReason: completed', content)

    def test_close_reason_not_planned(self):
        result = self._run('close', '1', '--reason', 'not_planned')
        self.assertEqual(result.returncode, 0, result.stderr)
        content = (
            self.cwd / 'issues' / 'closed' / '001-close-me.md'
        ).read_text(encoding='utf-8')
        self.assertIn('stateReason: not_planned', content)

    def test_close_with_comment(self):
        result = self._run('close', '1', '--comment', 'all done')
        self.assertEqual(result.returncode, 0, result.stderr)
        content = (
            self.cwd / 'issues' / 'closed' / '001-close-me.md'
        ).read_text(encoding='utf-8')
        self.assertIn('## Comments', content)
        self.assertIn('all done', content)

    def test_close_missing_task_errors(self):
        result = self._run('close', '999')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not found', result.stderr.lower())

    def test_reopen_moves_file_to_open(self):
        self._run('close', '1')
        result = self._run('reopen', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(
            (self.cwd / 'issues' / 'open' / '001-close-me.md').exists()
        )
        self.assertFalse(
            (self.cwd / 'issues' / 'closed' / '001-close-me.md').exists()
        )

    def test_reopen_updates_state_in_file(self):
        self._run('close', '1')
        self._run('reopen', '1')
        content = (
            self.cwd / 'issues' / 'open' / '001-close-me.md'
        ).read_text(encoding='utf-8')
        self.assertIn('state: open', content)
        self.assertIn('stateReason: reopened', content)
        self.assertIn('closedAt: null', content)

    def test_reopen_missing_task_errors(self):
        result = self._run('reopen', '999')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not found', result.stderr.lower())

    def test_view_shows_closed_state(self):
        self._run('close', '1', '--reason', 'completed')
        result = self._run('view', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('closed', result.stdout)

    def test_view_json_shows_closed_state(self):
        self._run('close', '1')
        result = self._run('view', '1', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        d = json.loads(result.stdout)
        self.assertEqual(d['state'], 'CLOSED')
        self.assertIsNotNone(d['closedAt'])
        self.assertEqual(d['stateReason'], 'completed')


class CommentSmokeTests(unittest.TestCase):
    """End-to-end smoke tests for `issues comment`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = Path(self.tmp.name)
        run_cli(['init'], cwd=self.cwd)
        run_cli(
            ['create', '--title', 'Comment target', '--body', 'Task body.'],
            cwd=self.cwd,
        )

    def _run(self, *args, stdin=None):
        return run_cli(list(args), cwd=self.cwd, stdin=stdin)

    def test_comment_body_flag(self):
        result = self._run('comment', '1', '--body', 'First comment')
        self.assertEqual(result.returncode, 0, result.stderr)
        content = (
            self.cwd / 'issues' / 'open' / '001-comment-target.md'
        ).read_text(encoding='utf-8')
        self.assertIn('## Comments', content)
        self.assertIn('First comment', content)

    def test_comment_body_file_stdin(self):
        result = self._run('comment', '1', '--body-file', '-',
                           stdin='From stdin.\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        content = (
            self.cwd / 'issues' / 'open' / '001-comment-target.md'
        ).read_text(encoding='utf-8')
        self.assertIn('From stdin.', content)

    def test_comment_body_file_path(self):
        body_path = self.cwd / 'comment.txt'
        body_path.write_text('From a file.\n', encoding='utf-8')
        result = self._run('comment', '1', '--body-file', str(body_path))
        self.assertEqual(result.returncode, 0, result.stderr)
        content = (
            self.cwd / 'issues' / 'open' / '001-comment-target.md'
        ).read_text(encoding='utf-8')
        self.assertIn('From a file.', content)

    def test_multiple_comments_append(self):
        self._run('comment', '1', '--body', 'Comment one')
        self._run('comment', '1', '--body', 'Comment two')
        content = (
            self.cwd / 'issues' / 'open' / '001-comment-target.md'
        ).read_text(encoding='utf-8')
        self.assertIn('Comment one', content)
        self.assertIn('Comment two', content)

    def test_view_json_includes_parsed_comments(self):
        self._run('comment', '1', '--body', 'JSON comment')
        result = self._run('view', '1', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        d = json.loads(result.stdout)
        self.assertEqual(len(d['comments']), 1)
        self.assertEqual(d['comments'][0]['body'], 'JSON comment')

    def test_view_json_comment_has_required_fields(self):
        self._run('comment', '1', '--body', 'Check fields')
        result = self._run('view', '1', '--json')
        d = json.loads(result.stdout)
        c = d['comments'][0]
        self.assertIn('timestamp', c)
        self.assertIn('author', c)
        self.assertIn('body', c)

    def test_view_json_two_comments_count(self):
        self._run('comment', '1', '--body', 'One')
        self._run('comment', '1', '--body', 'Two')
        result = self._run('view', '1', '--json')
        d = json.loads(result.stdout)
        self.assertEqual(len(d['comments']), 2)

    def test_comment_missing_task_errors(self):
        result = self._run('comment', '999', '--body', 'Ghost comment')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not found', result.stderr.lower())

    def test_comment_no_body_non_tty_errors(self):
        result = self._run('comment', '1', stdin='')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('body', result.stderr.lower())


if __name__ == '__main__':
    unittest.main()
