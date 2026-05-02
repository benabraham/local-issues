"""Query module tests: filter composition and sort stability."""

import unittest

from issues import query_filter, query_sort_key, task_new


def _make_task(number, title='t', state='open', task_type='task',
               labels=None, priority=None):
    """Build a minimal task dict for filter/sort testing."""
    task = task_new(
        number=number,
        title=title,
        task_type=task_type,
        labels=list(labels or []),
        priority=priority,
    )
    # Honour the requested state (task_new always sets 'open').
    task['state'] = state
    return task


class FilterStateTests(unittest.TestCase):
    def setUp(self):
        self.tasks = [
            _make_task(1, state='open'),
            _make_task(2, state='closed'),
            _make_task(3, state='open'),
            _make_task(4, state='closed'),
        ]

    def test_default_state_open(self):
        result = query_filter(self.tasks)
        self.assertEqual([t['number'] for t in result], [1, 3])

    def test_state_closed(self):
        result = query_filter(self.tasks, state='closed')
        self.assertEqual([t['number'] for t in result], [2, 4])

    def test_state_all(self):
        result = query_filter(self.tasks, state='all')
        self.assertEqual(len(result), 4)

    def test_empty_input(self):
        result = query_filter([], state='open')
        self.assertEqual(result, [])


class FilterTypeTests(unittest.TestCase):
    def setUp(self):
        self.tasks = [
            _make_task(1, task_type='task'),
            _make_task(2, task_type='prd'),
            _make_task(3, task_type='task'),
            _make_task(4, task_type='prd'),
        ]

    def test_default_type_task_excludes_prd(self):
        result = query_filter(self.tasks)
        types = {t['type'] for t in result}
        self.assertEqual(types, {'task'})
        self.assertEqual(len(result), 2)

    def test_type_prd_only(self):
        result = query_filter(self.tasks, task_type='prd')
        types = {t['type'] for t in result}
        self.assertEqual(types, {'prd'})
        self.assertEqual(len(result), 2)

    def test_type_all(self):
        result = query_filter(self.tasks, task_type='all')
        self.assertEqual(len(result), 4)


class FilterLabelTests(unittest.TestCase):
    def setUp(self):
        self.tasks = [
            _make_task(1, labels=['foo', 'bar']),
            _make_task(2, labels=['foo']),
            _make_task(3, labels=['bar']),
            _make_task(4, labels=[]),
            _make_task(5, labels=['foo', 'bar', 'baz']),
        ]

    def test_single_label_filter(self):
        result = query_filter(self.tasks, labels=['foo'])
        nums = [t['number'] for t in result]
        # Tasks 1, 2, 5 have 'foo'.
        self.assertIn(1, nums)
        self.assertIn(2, nums)
        self.assertIn(5, nums)
        self.assertNotIn(3, nums)
        self.assertNotIn(4, nums)

    def test_two_labels_and_semantics(self):
        """Both labels must be present (AND, not OR)."""
        result = query_filter(self.tasks, labels=['foo', 'bar'])
        nums = [t['number'] for t in result]
        # Only tasks 1 and 5 have both 'foo' and 'bar'.
        self.assertEqual(sorted(nums), [1, 5])

    def test_three_labels_narrows_to_one(self):
        result = query_filter(self.tasks, labels=['foo', 'bar', 'baz'])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['number'], 5)

    def test_no_label_filter_returns_all(self):
        result = query_filter(self.tasks, labels=[])
        self.assertEqual(len(result), len(self.tasks))

    def test_none_labels_returns_all(self):
        result = query_filter(self.tasks, labels=None)
        self.assertEqual(len(result), len(self.tasks))

    def test_label_not_present_returns_empty(self):
        result = query_filter(self.tasks, labels=['nonexistent'])
        self.assertEqual(result, [])


class FilterCombinedTests(unittest.TestCase):
    """State × type × label combinations."""

    def setUp(self):
        self.tasks = [
            _make_task(1, state='open', task_type='task', labels=['bug']),
            _make_task(2, state='open', task_type='prd', labels=['bug']),
            _make_task(3, state='closed', task_type='task', labels=['bug']),
            _make_task(4, state='open', task_type='task', labels=['feature']),
            _make_task(5, state='closed', task_type='prd', labels=[]),
        ]

    def test_open_task_bug(self):
        result = query_filter(self.tasks, state='open',
                              task_type='task', labels=['bug'])
        self.assertEqual([t['number'] for t in result], [1])

    def test_all_states_all_types_bug(self):
        result = query_filter(self.tasks, state='all',
                              task_type='all', labels=['bug'])
        nums = sorted(t['number'] for t in result)
        self.assertEqual(nums, [1, 2, 3])

    def test_closed_prd(self):
        result = query_filter(self.tasks, state='closed', task_type='prd')
        self.assertEqual([t['number'] for t in result], [5])


class SortKeyTests(unittest.TestCase):
    def test_lower_priority_sorts_first(self):
        tasks = [
            _make_task(1, priority=3),
            _make_task(2, priority=1),
            _make_task(3, priority=0),
        ]
        result = sorted(tasks, key=query_sort_key)
        self.assertEqual([t['number'] for t in result], [3, 2, 1])

    def test_none_priority_sorts_last(self):
        tasks = [
            _make_task(1, priority=None),
            _make_task(2, priority=1),
            _make_task(3, priority=0),
        ]
        result = sorted(tasks, key=query_sort_key)
        nums = [t['number'] for t in result]
        # priority=None task must be last.
        self.assertEqual(nums[-1], 1)
        # priority=0 before priority=1.
        self.assertEqual(nums[0], 3)
        self.assertEqual(nums[1], 2)

    def test_multiple_none_priority_sorted_by_id(self):
        """Multiple tasks with no priority should be stable — sorted by ID."""
        tasks = [
            _make_task(5, priority=None),
            _make_task(2, priority=None),
            _make_task(9, priority=None),
        ]
        result = sorted(tasks, key=query_sort_key)
        self.assertEqual([t['number'] for t in result], [2, 5, 9])

    def test_same_priority_sorted_by_id(self):
        tasks = [
            _make_task(10, priority=2),
            _make_task(3, priority=2),
            _make_task(7, priority=2),
        ]
        result = sorted(tasks, key=query_sort_key)
        self.assertEqual([t['number'] for t in result], [3, 7, 10])

    def test_mixed_priority_and_none_stable(self):
        """Full mixed scenario: some priorities, some None, stability on IDs."""
        tasks = [
            _make_task(1, priority=2),
            _make_task(2, priority=None),
            _make_task(3, priority=1),
            _make_task(4, priority=None),
            _make_task(5, priority=0),
        ]
        result = sorted(tasks, key=query_sort_key)
        nums = [t['number'] for t in result]
        # Expected order: priority=0 (#5), priority=1 (#3), priority=2 (#1),
        # then None by ID (#2, #4).
        self.assertEqual(nums, [5, 3, 1, 2, 4])

    def test_sort_key_returns_tuple(self):
        task = _make_task(7, priority=3)
        key = query_sort_key(task)
        self.assertIsInstance(key, tuple)
        self.assertEqual(len(key), 2)
        self.assertEqual(key, (3, 7))

    def test_sort_key_none_priority_is_inf(self):
        task = _make_task(7, priority=None)
        key = query_sort_key(task)
        self.assertEqual(key[0], float('inf'))
        self.assertEqual(key[1], 7)


class DefaultFilterBehaviourTests(unittest.TestCase):
    """The default filter (state='open', task_type='task') matches the spec."""

    def test_default_excludes_prd(self):
        tasks = [
            _make_task(1, task_type='task'),
            _make_task(2, task_type='prd'),
        ]
        result = query_filter(tasks)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['type'], 'task')

    def test_default_excludes_closed(self):
        tasks = [
            _make_task(1, state='open'),
            _make_task(2, state='closed'),
        ]
        result = query_filter(tasks)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['state'], 'open')

    def test_default_open_non_prd(self):
        """Default is open + task — the backlog view."""
        tasks = [
            _make_task(1, state='open', task_type='task'),
            _make_task(2, state='open', task_type='prd'),
            _make_task(3, state='closed', task_type='task'),
            _make_task(4, state='closed', task_type='prd'),
        ]
        result = query_filter(tasks)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['number'], 1)


if __name__ == '__main__':
    unittest.main()
