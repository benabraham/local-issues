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

    def test_init_first_run_prints_initialised(self):
        result = self._init()
        self.assertIn('Initialised', result.stdout)
        self.assertNotIn('Already', result.stdout)

    def test_init_second_run_prints_already_initialised(self):
        self._init()
        result = self._init()
        self.assertIn('Already initialised', result.stdout)

    def test_init_gitignore(self):
        self._init('--gitignore')
        gi = (self.cwd / '.gitignore').read_text(encoding='utf-8')
        self.assertIn('issues/', gi)

    def test_init_gitignore_first_run_prints_updated(self):
        result = self._init('--gitignore')
        self.assertIn('Updated', result.stdout)

    def test_init_gitignore_second_run_no_spurious_write(self):
        self._init('--gitignore')
        gi_before = (self.cwd / '.gitignore').read_text(encoding='utf-8')
        result = self._init('--gitignore')
        gi_after = (self.cwd / '.gitignore').read_text(encoding='utf-8')
        # Content unchanged (no duplicate entry).
        self.assertEqual(gi_before, gi_after)
        # Message indicates already present.
        self.assertIn("already contains 'issues/'", result.stdout)

    def test_init_exit_code_always_zero(self):
        self._init()
        result = self._init()
        self.assertEqual(result.returncode, 0)

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

    def test_create_short_p_flag_errors(self):
        self._init()
        result = run_cli(
            ['create', '--title', 'p flag test', '-p', '3'],
            cwd=self.cwd,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_create_empty_title_errors(self):
        self._init()
        result = run_cli(
            ['create', '--title', '', '--body', 'b'],
            cwd=self.cwd,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--title', result.stderr)

    def test_create_whitespace_title_errors(self):
        self._init()
        result = run_cli(
            ['create', '--title', '   ', '--body', 'b'],
            cwd=self.cwd,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--title', result.stderr)

    def test_create_tab_title_errors(self):
        self._init()
        result = run_cli(
            ['create', '--title', '\t', '--body', 'b'],
            cwd=self.cwd,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--title', result.stderr)

    def test_create_empty_title_no_file_created(self):
        self._init()
        run_cli(['create', '--title', '', '--body', 'b'], cwd=self.cwd)
        open_dir = self.cwd / 'issues' / 'open'
        self.assertEqual(list(open_dir.iterdir()), [])

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

    def test_list_state_short_flag_s_open(self):
        result = self._run('list', '-s', 'open')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Task one', result.stdout)

    def test_list_state_short_flag_s_closed(self):
        result = self._run('list', '-s', 'closed')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('TITLE', result.stdout)
        self.assertNotIn('Task one', result.stdout)

    def test_list_state_short_flag_s_all(self):
        result = self._run('list', '-s', 'all')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Task one', result.stdout)

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

    def test_close_reason_not_planned_with_space(self):
        result = self._run('close', '1', '--reason', 'not planned')
        self.assertEqual(result.returncode, 0, result.stderr)
        content = (
            self.cwd / 'issues' / 'closed' / '001-close-me.md'
        ).read_text(encoding='utf-8')
        self.assertIn('stateReason: not_planned', content)

    def test_close_reason_not_planned_underscore_still_works(self):
        result = self._run('close', '1', '--reason', 'not_planned')
        self.assertEqual(result.returncode, 0, result.stderr)
        content = (
            self.cwd / 'issues' / 'closed' / '001-close-me.md'
        ).read_text(encoding='utf-8')
        self.assertIn('stateReason: not_planned', content)

    def test_close_reason_invalid_errors(self):
        result = self._run('close', '1', '--reason', 'wat')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invalid --reason', result.stderr)

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


class EditSmokeTests(unittest.TestCase):
    """End-to-end smoke tests for `issues edit`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = Path(self.tmp.name)
        run_cli(['init'], cwd=self.cwd)
        run_cli(
            ['create', '--title', 'Edit target',
             '--body', 'Original body.',
             '--label', 'foo', '--priority', '5',
             '--blocked-by', '9'],
            cwd=self.cwd,
        )

    def _run(self, *args, stdin=None):
        return run_cli(list(args), cwd=self.cwd, stdin=stdin)

    def _read_file(self):
        return (self.cwd / 'issues' / 'open' / '001-edit-target.md').read_text(
            encoding='utf-8')

    def test_edit_title(self):
        result = self._run('edit', '1', '--title', 'New title')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('title: New title', self._read_file())

    def test_edit_title_add_label_priority_together(self):
        result = self._run(
            'edit', '1', '--title', 'Multi', '--add-label', 'bar', '--priority', '2',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        content = self._read_file()
        self.assertIn('title: Multi', content)
        self.assertIn('bar', content)
        self.assertIn('priority: 2', content)

    def test_edit_add_and_remove_label(self):
        self._run('edit', '1', '--add-label', 'new')
        result = self._run('edit', '1', '--remove-label', 'foo')
        self.assertEqual(result.returncode, 0, result.stderr)
        content = self._read_file()
        self.assertNotIn('foo', content)
        self.assertIn('new', content)

    def test_edit_body_replaces_body(self):
        result = self._run('edit', '1', '--body', 'Replaced body.')
        self.assertEqual(result.returncode, 0, result.stderr)
        content = self._read_file()
        self.assertIn('Replaced body.', content)
        self.assertNotIn('Original body.', content)

    def test_edit_body_preserves_comments(self):
        # First append a comment.
        self._run('comment', '1', '--body', 'Existing comment')
        # Now replace the body.
        result = self._run('edit', '1', '--body', 'Brand new body.')
        self.assertEqual(result.returncode, 0, result.stderr)
        # View with JSON to check comments survived.
        view = self._run('view', '1', '--json')
        d = json.loads(view.stdout)
        self.assertEqual(len(d['comments']), 1)
        self.assertEqual(d['comments'][0]['body'], 'Existing comment')
        self.assertIn('Brand new body', d['body'])

    def test_edit_body_file_stdin(self):
        result = self._run('edit', '1', '--body-file', '-', stdin='From stdin.\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('From stdin.', self._read_file())

    def test_edit_priority_none_clears_priority(self):
        result = self._run('edit', '1', '--priority', 'none')
        self.assertEqual(result.returncode, 0, result.stderr)
        view = self._run('view', '1', '--json')
        d = json.loads(view.stdout)
        self.assertIsNone(d['priority'])

    def test_edit_add_blocked_by(self):
        result = self._run('edit', '1', '--add-blocked-by', '10')
        self.assertEqual(result.returncode, 0, result.stderr)
        view = self._run('view', '1', '--json')
        d = json.loads(view.stdout)
        self.assertIn(9, d['blockedBy'])
        self.assertIn(10, d['blockedBy'])

    def test_edit_remove_blocked_by(self):
        result = self._run('edit', '1', '--remove-blocked-by', '9')
        self.assertEqual(result.returncode, 0, result.stderr)
        view = self._run('view', '1', '--json')
        d = json.loads(view.stdout)
        self.assertNotIn(9, d['blockedBy'])

    def test_edit_missing_task_errors(self):
        result = self._run('edit', '999', '--title', 'Ghost')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not found', result.stderr.lower())

    def test_edit_no_flags_is_noop(self):
        """Calling edit with only the id and no flags is a valid no-op."""
        result = self._run('edit', '1')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_edit_invalid_priority_errors(self):
        result = self._run('edit', '1', '--priority', 'bad')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('priority', result.stderr.lower())

    def test_edit_short_p_flag_errors(self):
        result = self._run('edit', '1', '-p', '3')
        self.assertNotEqual(result.returncode, 0)


class DeleteSmokeTests(unittest.TestCase):
    """End-to-end smoke tests for `issues delete`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = Path(self.tmp.name)
        run_cli(['init'], cwd=self.cwd)
        run_cli(
            ['create', '--title', 'To delete', '--body', 'b'],
            cwd=self.cwd,
        )

    def _run(self, *args, stdin=None):
        return run_cli(list(args), cwd=self.cwd, stdin=stdin)

    def test_delete_yes_removes_file(self):
        result = self._run('delete', '1', '--yes')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(
            (self.cwd / 'issues' / 'open' / '001-to-delete.md').exists()
        )

    def test_delete_yes_file_unreadable(self):
        self._run('delete', '1', '--yes')
        result = self._run('view', '1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not found', result.stderr.lower())

    def test_delete_non_tty_without_yes_errors(self):
        """Non-TTY context (piped stdin) without --yes must error out."""
        result = self._run('delete', '1', stdin='')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--yes', result.stderr)

    def test_delete_missing_task_errors(self):
        result = self._run('delete', '999', '--yes')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not found', result.stderr.lower())

    def test_delete_id_not_reused(self):
        """After delete, next created task gets the next fresh ID, not the deleted one."""
        self._run('delete', '1', '--yes')
        result = run_cli(
            ['create', '--title', 'Next task', '--body', 'b'],
            cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        # .next-id must be >= 2; the new task should have number 2 not 1.
        next_id_raw = (self.cwd / 'issues' / '.next-id').read_text(
            encoding='utf-8').strip()
        self.assertGreaterEqual(int(next_id_raw), 3)
        # The file 001-*.md should NOT exist.
        open_files = list((self.cwd / 'issues' / 'open').iterdir())
        names = [f.name for f in open_files]
        self.assertFalse(any(n.startswith('001-') for n in names))
        self.assertTrue(any(n.startswith('002-') for n in names))


class NextSmokeTests(unittest.TestCase):
    """End-to-end smoke tests for `issues next`, `list --ready`, and cycle/cascade."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = Path(self.tmp.name)
        run_cli(['init'], cwd=self.cwd)
        # Create: A (task 1, no blockers), B (task 2, blocked by A),
        # C (task 3, blocked by A).
        run_cli(['create', '--title', 'Task A', '--body', 'b'], cwd=self.cwd)
        run_cli(['create', '--title', 'Task B', '--body', 'b',
                 '--blocked-by', '1'], cwd=self.cwd)
        run_cli(['create', '--title', 'Task C', '--body', 'b',
                 '--blocked-by', '1'], cwd=self.cwd)
        # Create a PRD (task 4) and a child (task 5, parent=4).
        run_cli(['create', '--title', 'The PRD', '--body', 'b',
                 '--type', 'prd'], cwd=self.cwd)
        run_cli(['create', '--title', 'Child of PRD', '--body', 'b',
                 '--parent', '4'], cwd=self.cwd)

    def _run(self, *args, stdin=None):
        return run_cli(list(args), cwd=self.cwd, stdin=stdin)

    # --- next ---

    def test_next_returns_only_ready_task(self):
        """Only A (task 1) is ready; B and C are blocked."""
        result = self._run('next')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '1')

    def test_next_exit_0_on_success(self):
        result = self._run('next')
        self.assertEqual(result.returncode, 0)

    def test_next_exit_1_empty_stdout_when_none(self):
        """With label that no task has, exit 1 with empty stdout."""
        result = self._run('next', '--label', 'nonexistent')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), '')

    def test_next_after_close_unblocks_dependents(self):
        """Close A; next should return B or C (lowest ID first: B=2)."""
        self._run('close', '1', '--reason', 'completed')
        result = self._run('next')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '2')

    def test_next_label_filter(self):
        """Label filter constrains candidate set."""
        # Add label to task A only.
        self._run('edit', '1', '--add-label', 'afk')
        result = self._run('next', '--label', 'afk')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '1')

    def test_next_excludes_prd(self):
        """PRD task 4 must never be returned by next."""
        # Close everything that would block to ensure PRD is not returned.
        # PRDs are always excluded regardless.
        # Create an unblocked task that is a prd.
        run_cli(['create', '--title', 'Another PRD', '--body', 'b',
                 '--type', 'prd'], cwd=self.cwd)
        result = self._run('next')
        self.assertEqual(result.returncode, 0)
        # Returned task must be task 1 (the only ready non-PRD).
        self.assertEqual(result.stdout.strip(), '1')

    # --- list --ready ---

    def test_list_ready_shows_only_ready_tasks(self):
        result = self._run('list', '--ready')
        self.assertEqual(result.returncode, 0, result.stderr)
        # Only A (task 1) is ready.
        self.assertIn('Task A', result.stdout)
        self.assertNotIn('Task B', result.stdout)
        self.assertNotIn('Task C', result.stdout)
        self.assertNotIn('The PRD', result.stdout)

    def test_list_ready_after_close_shows_more(self):
        """After closing A, both B and C appear in --ready."""
        self._run('close', '1', '--reason', 'completed')
        result = self._run('list', '--ready')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Task B', result.stdout)
        self.assertIn('Task C', result.stdout)

    def test_list_ready_json_length(self):
        """JSON ready set: A (task 1) and Child of PRD (task 5) are ready."""
        result = self._run('list', '--ready', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        # Task 1 (A) and task 5 (Child of PRD) are both open non-PRD with no blockers.
        numbers = [item['number'] for item in data]
        self.assertIn(1, numbers)
        self.assertIn(5, numbers)

    def test_list_ready_with_label_filter(self):
        self._run('edit', '1', '--add-label', 'tagged')
        result = self._run('list', '--ready', '--label', 'tagged')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Task A', result.stdout)

    def test_list_ready_excludes_prd_type(self):
        """PRD-type tasks are excluded; task-type children of PRDs are included."""
        result = self._run('list', '--ready')
        # PRD (type=prd) is excluded.
        self.assertNotIn('The PRD', result.stdout)
        # Child of PRD (type=task, no blockers) IS ready and should appear.
        self.assertIn('Child of PRD', result.stdout)

    # --- edit --add-blocked-by cycle rejection ---

    def test_edit_add_blocked_by_cycle_rejected(self):
        """A is blocked by B, B blocked by A — second edit must fail."""
        # Currently A=1 is unblocked, B=2 is blocked by A=1.
        # Add A blocked_by B=2 — that would create 1->2->1.
        result = self._run('edit', '1', '--add-blocked-by', '2')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('cycle', result.stderr.lower())

    def test_edit_add_blocked_by_no_cycle_succeeds(self):
        """Adding a non-cycling edge is accepted."""
        # Create task 6 with no blockers, then make it block C=3.
        # That means 3->6: no cycle (6 doesn't depend on anything).
        run_cli(['create', '--title', 'Task D', '--body', 'b'], cwd=self.cwd)
        # D is task 6; add C blocked_by D (3->6): safe.
        result = self._run('edit', '3', '--add-blocked-by', '6')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_edit_add_blocked_by_no_write_on_cycle(self):
        """File must not be modified when cycle is rejected."""
        view_before = self._run('view', '1', '--json')
        d_before = json.loads(view_before.stdout)
        # Attempt to create cycle (should fail).
        self._run('edit', '1', '--add-blocked-by', '2')
        view_after = self._run('view', '1', '--json')
        d_after = json.loads(view_after.stdout)
        # blockedBy must be unchanged.
        self.assertEqual(d_before['blockedBy'], d_after['blockedBy'])

    # --- delete --cascade ---

    def test_cascade_delete_requires_yes(self):
        """--cascade without --yes must error regardless of TTY."""
        result = self._run('delete', '4', '--cascade', stdin='')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--yes', result.stderr)

    def test_cascade_delete_with_yes_removes_parent_and_child(self):
        """delete PRD (4) --cascade --yes removes task 4 and task 5."""
        result = self._run('delete', '4', '--cascade', '--yes')
        self.assertEqual(result.returncode, 0, result.stderr)
        # Verify both are gone.
        view4 = self._run('view', '4')
        self.assertNotEqual(view4.returncode, 0)
        view5 = self._run('view', '5')
        self.assertNotEqual(view5.returncode, 0)

    def test_cascade_delete_output_lists_deleted_ids(self):
        result = self._run('delete', '4', '--cascade', '--yes')
        self.assertEqual(result.returncode, 0, result.stderr)
        # Output should mention task 4 and task 5.
        self.assertIn('#4', result.stdout)
        self.assertIn('#5', result.stdout)

    def test_cascade_delete_no_children_succeeds(self):
        """Cascade on a task with no children just deletes the task."""
        # Task 1 has no children (B/C are blocked by it, but parent != 1).
        result = self._run('delete', '1', '--cascade', '--yes')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('#1', result.stdout)

    def test_cascade_delete_missing_task_errors(self):
        result = self._run('delete', '999', '--cascade', '--yes')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not found', result.stderr.lower())


class GhCompatNoOpTests(unittest.TestCase):
    """Smoke tests: --web and --repo R are accepted as no-ops on every verb."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = Path(self.tmp.name)
        # Bootstrap a workspace and create one task so read-verbs don't error.
        run_cli(['init'], cwd=self.cwd)
        run_cli(
            ['create', '--title', 'Compat task', '--body', 'B'],
            cwd=self.cwd,
        )

    def _run(self, *args):
        return run_cli(list(args), cwd=self.cwd)

    # --- list ---

    def test_list_web_flag_no_error(self):
        result = self._run('list', '--web')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Compat task', result.stdout)

    def test_list_repo_flag_no_error(self):
        result = self._run('list', '--repo', 'foo/bar')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Compat task', result.stdout)

    def test_list_web_and_repo_together(self):
        result = self._run('list', '--web', '--repo', 'foo/bar')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Compat task', result.stdout)

    # --- view ---

    def test_view_web_flag_no_error(self):
        result = self._run('view', '1', '--web')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Compat task', result.stdout)

    def test_view_repo_flag_no_error(self):
        result = self._run('view', '1', '--repo', 'owner/repo')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Compat task', result.stdout)

    # --- create ---

    def test_create_web_flag_no_error(self):
        result = self._run(
            'create', '--title', 'Web task', '--body', 'body', '--web'
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_create_repo_flag_no_error(self):
        result = self._run(
            'create', '--title', 'Repo task', '--body', 'body',
            '--repo', 'x/y',
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    # --- status ---

    def test_status_web_flag_no_error(self):
        result = self._run('status', '--web')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Open tasks:', result.stdout)

    def test_status_repo_flag_no_error(self):
        result = self._run('status', '--repo', 'some/repo')
        self.assertEqual(result.returncode, 0, result.stderr)

    # --- close / reopen ---

    def test_close_web_flag_no_error(self):
        result = self._run('close', '1', '--web')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_close_repo_flag_no_error(self):
        # Re-create since previous test may have closed it.
        run_cli(
            ['create', '--title', 'Close compat', '--body', 'B'],
            cwd=self.cwd,
        )
        # Get a fresh task number — could be 2, 3, etc. Just use the first
        # created task that's still open; close 1 might already be closed.
        result = run_cli(
            ['create', '--title', 'To close', '--body', 'B'],
            cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        # Extract task number from output "Created task #N at ..."
        import re as _re
        m = _re.search(r'#(\d+)', result.stdout)
        num = m.group(1) if m else '1'
        result = self._run('close', num, '--repo', 'r/r')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reopen_web_flag_no_error(self):
        self._run('close', '1')
        result = self._run('reopen', '1', '--web')
        self.assertEqual(result.returncode, 0, result.stderr)

    # --- comment ---

    def test_comment_web_flag_no_error(self):
        result = self._run('comment', '1', '--body', 'hi', '--web')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_comment_repo_flag_no_error(self):
        result = self._run('comment', '1', '--body', 'hi2', '--repo', 'a/b')
        self.assertEqual(result.returncode, 0, result.stderr)

    # --- init ---

    def test_init_web_flag_no_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli(['init', '--web'], cwd=Path(tmp))
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_init_repo_flag_no_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli(['init', '--repo', 'foo/bar'], cwd=Path(tmp))
            self.assertEqual(result.returncode, 0, result.stderr)

    # --- no output change ---

    def test_web_flag_does_not_affect_output(self):
        """--web must not change list output."""
        plain = self._run('list')
        with_web = self._run('list', '--web')
        self.assertEqual(plain.stdout, with_web.stdout)

    def test_repo_flag_does_not_affect_output(self):
        """--repo must not change list output."""
        plain = self._run('list')
        with_repo = self._run('list', '--repo', 'anything/here')
        self.assertEqual(plain.stdout, with_repo.stdout)


if __name__ == '__main__':
    unittest.main()
