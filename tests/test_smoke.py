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


if __name__ == '__main__':
    unittest.main()
