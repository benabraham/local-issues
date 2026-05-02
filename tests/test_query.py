"""Query module tests: filter composition and sort stability."""

import unittest

from issues import (
    query_detect_cycle,
    query_filter,
    query_is_ready,
    query_next,
    query_ready_set,
    query_sort_key,
    query_would_introduce_cycle,
    task_new,
    IssuesError,
)


def _make_task(number, title='t', state='open', task_type='task',
               labels=None, priority=None, blocked_by=None):
    """Build a minimal task dict for filter/sort testing."""
    task = task_new(
        number=number,
        title=title,
        task_type=task_type,
        labels=list(labels or []),
        priority=priority,
        blocked_by=list(blocked_by or []),
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


# ---------------------------------------------------------------------------
# query_is_ready
# ---------------------------------------------------------------------------


class IsReadyTests(unittest.TestCase):
    """query_is_ready: open + non-PRD + all blockers closed-or-missing."""

    def test_open_task_no_blockers_is_ready(self):
        task = _make_task(1)
        self.assertTrue(query_is_ready(task, {1: task}))

    def test_closed_task_is_not_ready(self):
        task = _make_task(1, state='closed')
        self.assertFalse(query_is_ready(task, {1: task}))

    def test_prd_is_never_ready(self):
        task = _make_task(1, task_type='prd')
        self.assertFalse(query_is_ready(task, {1: task}))

    def test_blocked_by_open_task_is_not_ready(self):
        blocker = _make_task(1, state='open')
        blocked = _make_task(2, blocked_by=[1])
        by_num = {1: blocker, 2: blocked}
        self.assertFalse(query_is_ready(blocked, by_num))

    def test_blocked_by_closed_task_is_ready(self):
        blocker = _make_task(1, state='closed')
        blocked = _make_task(2, blocked_by=[1])
        by_num = {1: blocker, 2: blocked}
        self.assertTrue(query_is_ready(blocked, by_num))

    def test_blocked_by_missing_task_is_ready(self):
        """Dangling blocker reference is treated as satisfied."""
        task = _make_task(2, blocked_by=[99])
        by_num = {2: task}  # task 99 not present
        self.assertTrue(query_is_ready(task, by_num))

    def test_linear_chain_only_tail_is_ready(self):
        """1 -> 2 -> 3: only 3 (no blockers) is ready."""
        t1 = _make_task(1, blocked_by=[2])
        t2 = _make_task(2, blocked_by=[3])
        t3 = _make_task(3)
        by_num = {1: t1, 2: t2, 3: t3}
        self.assertFalse(query_is_ready(t1, by_num))
        self.assertFalse(query_is_ready(t2, by_num))
        self.assertTrue(query_is_ready(t3, by_num))

    def test_multiple_blockers_all_must_be_closed(self):
        """Task blocked by 2 tasks: only ready when both are closed."""
        b1 = _make_task(1, state='closed')
        b2 = _make_task(2, state='open')
        task = _make_task(3, blocked_by=[1, 2])
        by_num = {1: b1, 2: b2, 3: task}
        # b2 still open — not ready.
        self.assertFalse(query_is_ready(task, by_num))
        b2['state'] = 'closed'
        self.assertTrue(query_is_ready(task, by_num))

    def test_branching_graph_both_ready_when_blocker_closed(self):
        """1 blocks both 2 and 3; both become ready when 1 is closed."""
        t1 = _make_task(1, state='closed')
        t2 = _make_task(2, blocked_by=[1])
        t3 = _make_task(3, blocked_by=[1])
        by_num = {1: t1, 2: t2, 3: t3}
        self.assertTrue(query_is_ready(t2, by_num))
        self.assertTrue(query_is_ready(t3, by_num))

    def test_prd_not_ready_even_if_all_blockers_closed(self):
        blocker = _make_task(1, state='closed')
        prd = _make_task(2, task_type='prd', blocked_by=[1])
        by_num = {1: blocker, 2: prd}
        self.assertFalse(query_is_ready(prd, by_num))


# ---------------------------------------------------------------------------
# query_detect_cycle
# ---------------------------------------------------------------------------


class DetectCycleTests(unittest.TestCase):
    """query_detect_cycle: DFS with cycle path reporting."""

    def test_no_cycle_empty_list(self):
        self.assertIsNone(query_detect_cycle([]))

    def test_no_cycle_no_blockers(self):
        tasks = [_make_task(1), _make_task(2), _make_task(3)]
        self.assertIsNone(query_detect_cycle(tasks))

    def test_no_cycle_linear_chain(self):
        """1 <- 2 <- 3 (3 blocked by 2 blocked by 1)."""
        t1 = _make_task(1)
        t2 = _make_task(2, blocked_by=[1])
        t3 = _make_task(3, blocked_by=[2])
        self.assertIsNone(query_detect_cycle([t1, t2, t3]))

    def test_no_cycle_diamond(self):
        """Diamond: 4 blocked by 2 and 3, both blocked by 1."""
        t1 = _make_task(1)
        t2 = _make_task(2, blocked_by=[1])
        t3 = _make_task(3, blocked_by=[1])
        t4 = _make_task(4, blocked_by=[2, 3])
        self.assertIsNone(query_detect_cycle([t1, t2, t3, t4]))

    def test_simple_two_node_cycle(self):
        """1 blocked by 2, 2 blocked by 1."""
        t1 = _make_task(1, blocked_by=[2])
        t2 = _make_task(2, blocked_by=[1])
        result = query_detect_cycle([t1, t2])
        self.assertIsNotNone(result)
        # Result is a list; first and last element should be the same (cycle).
        self.assertIsInstance(result, list)
        self.assertGreaterEqual(len(result), 2)
        self.assertEqual(result[0], result[-1])

    def test_three_node_cycle(self):
        """1 blocked by 2, 2 blocked by 3, 3 blocked by 1."""
        t1 = _make_task(1, blocked_by=[2])
        t2 = _make_task(2, blocked_by=[3])
        t3 = _make_task(3, blocked_by=[1])
        result = query_detect_cycle([t1, t2, t3])
        self.assertIsNotNone(result)
        self.assertEqual(result[0], result[-1])
        # The cycle path should contain 3 distinct nodes.
        self.assertEqual(len(set(result)), 3)

    def test_cycle_path_contains_all_cycle_nodes(self):
        """Cycle path 3 -> 5 -> 7 -> 3 format."""
        t3 = _make_task(3, blocked_by=[5])
        t5 = _make_task(5, blocked_by=[7])
        t7 = _make_task(7, blocked_by=[3])
        result = query_detect_cycle([t3, t5, t7])
        self.assertIsNotNone(result)
        # All three cycle nodes appear in the path.
        self.assertIn(3, result)
        self.assertIn(5, result)
        self.assertIn(7, result)
        # First == last (cycle marker).
        self.assertEqual(result[0], result[-1])

    def test_dangling_blocker_not_treated_as_cycle(self):
        """A blocker that doesn't exist in the task list is skipped."""
        t1 = _make_task(1, blocked_by=[999])  # 999 not in list
        self.assertIsNone(query_detect_cycle([t1]))

    def test_cycle_isolated_from_rest_of_graph(self):
        """Cycle among some nodes detected even if other nodes are acyclic."""
        t1 = _make_task(1)  # acyclic
        t2 = _make_task(2, blocked_by=[3])  # cycle
        t3 = _make_task(3, blocked_by=[2])  # cycle
        result = query_detect_cycle([t1, t2, t3])
        self.assertIsNotNone(result)

    def test_self_loop(self):
        """Task blocked by itself is a trivial cycle."""
        t1 = _make_task(1, blocked_by=[1])
        result = query_detect_cycle([t1])
        self.assertIsNotNone(result)
        self.assertEqual(result[0], result[-1])
        self.assertIn(1, result)


# ---------------------------------------------------------------------------
# query_would_introduce_cycle
# ---------------------------------------------------------------------------


class WouldIntroduceCycleTests(unittest.TestCase):
    """query_would_introduce_cycle: speculative pre-write check."""

    def _make_chain(self):
        """Linear chain: 3 blocked by 2 blocked by 1."""
        t1 = _make_task(1)
        t2 = _make_task(2, blocked_by=[1])
        t3 = _make_task(3, blocked_by=[2])
        return [t1, t2, t3]

    def test_adding_3_blocked_by_1_no_cycle(self):
        """3 -> 1 edge: 1 cannot reach 3 via existing edges, so no cycle."""
        tasks = self._make_chain()
        # Chain: 3 <- 2 <- 1. Adding 1 blocked_by 3: 1 -> 3 -> 2 -> 1 = cycle!
        # Wait, let me re-read the chain: t3 blocked_by [2], t2 blocked_by [1].
        # Edges: 3->2->1. Adding edge 1->3 closes 1->3->2->1, which IS a cycle.
        self.assertTrue(query_would_introduce_cycle(tasks, source_id=1, new_blocker_id=3))

    def test_adding_4_blocked_by_3_no_cycle(self):
        """Adding a new leaf node blocked by 3 is safe."""
        tasks = self._make_chain()
        t4 = _make_task(4)
        tasks.append(t4)
        self.assertFalse(query_would_introduce_cycle(tasks, source_id=4, new_blocker_id=3))

    def test_self_loop_is_cycle(self):
        tasks = self._make_chain()
        self.assertTrue(query_would_introduce_cycle(tasks, source_id=1, new_blocker_id=1))

    def test_adding_sibling_not_cycle(self):
        """1 blocked by 2 (new); 2 already exists with no blockers."""
        t1 = _make_task(1, blocked_by=[2])
        t2 = _make_task(2)
        tasks = [t1, t2]
        # Adding 2 blocked by 3 (new node): 2->3, no way back to 1 or 2.
        t3 = _make_task(3)
        tasks.append(t3)
        self.assertFalse(query_would_introduce_cycle(tasks, source_id=2, new_blocker_id=3))

    def test_existing_chain_closing_edge_is_cycle(self):
        """3->5->7 exists; adding 7 blocked_by 3 would close 3->5->7->3."""
        t3 = _make_task(3, blocked_by=[5])
        t5 = _make_task(5, blocked_by=[7])
        t7 = _make_task(7)
        tasks = [t3, t5, t7]
        # query_would_introduce_cycle(tasks, source_id=7, new_blocker_id=3)
        # means: add edge 7->3. Can 3 reach 7 via existing? 3->5->7 yes.
        self.assertTrue(query_would_introduce_cycle(tasks, source_id=7, new_blocker_id=3))

    def test_new_blocker_not_in_tasks(self):
        """Blocker that doesn't exist yet cannot create a cycle."""
        tasks = self._make_chain()
        self.assertFalse(query_would_introduce_cycle(tasks, source_id=3, new_blocker_id=999))


# ---------------------------------------------------------------------------
# query_next
# ---------------------------------------------------------------------------


class QueryNextTests(unittest.TestCase):
    """query_next: returns first ready task or None."""

    def test_returns_none_when_empty(self):
        self.assertIsNone(query_next([]))

    def test_returns_none_when_all_blocked(self):
        t1 = _make_task(1, blocked_by=[2])
        t2 = _make_task(2, blocked_by=[1])
        # Both open but cyclic — should raise IssuesError (cycle).
        with self.assertRaises(IssuesError):
            query_next([t1, t2])

    def test_returns_none_when_all_closed(self):
        t1 = _make_task(1, state='closed')
        t2 = _make_task(2, state='closed')
        self.assertIsNone(query_next([t1, t2]))

    def test_returns_none_when_all_prd(self):
        t1 = _make_task(1, task_type='prd')
        t2 = _make_task(2, task_type='prd')
        self.assertIsNone(query_next([t1, t2]))

    def test_returns_none_when_all_blocked_by_open(self):
        t1 = _make_task(1)
        t2 = _make_task(2, blocked_by=[1])
        # t1 is ready (no blockers); t2 is blocked. Should return t1.
        result = query_next([t1, t2])
        self.assertIsNotNone(result)
        self.assertEqual(result['number'], 1)

    def test_single_ready_task(self):
        t1 = _make_task(1)
        result = query_next([t1])
        self.assertIsNotNone(result)
        self.assertEqual(result['number'], 1)

    def test_priority_sort_lower_wins(self):
        t1 = _make_task(1, priority=2)
        t2 = _make_task(2, priority=1)
        result = query_next([t1, t2])
        self.assertEqual(result['number'], 2)

    def test_none_priority_sorts_last(self):
        t1 = _make_task(1, priority=None)
        t2 = _make_task(2, priority=1)
        result = query_next([t1, t2])
        self.assertEqual(result['number'], 2)

    def test_id_tiebreak_lower_id_wins(self):
        t3 = _make_task(3, priority=1)
        t1 = _make_task(1, priority=1)
        result = query_next([t3, t1])
        self.assertEqual(result['number'], 1)

    def test_label_filter_applied(self):
        t1 = _make_task(1, labels=['foo'])
        t2 = _make_task(2, labels=['bar'])
        result = query_next([t1, t2], labels=['foo'])
        self.assertEqual(result['number'], 1)

    def test_label_filter_excludes_all_returns_none(self):
        t1 = _make_task(1, labels=['bar'])
        result = query_next([t1], labels=['nonexistent'])
        self.assertIsNone(result)

    def test_prd_always_excluded(self):
        t1 = _make_task(1, task_type='prd')
        t2 = _make_task(2, task_type='task')
        result = query_next([t1, t2])
        self.assertEqual(result['number'], 2)

    def test_cycle_raises_issues_error(self):
        t1 = _make_task(1, blocked_by=[2])
        t2 = _make_task(2, blocked_by=[1])
        with self.assertRaises(IssuesError) as ctx:
            query_next([t1, t2])
        self.assertIn('cycle', str(ctx.exception))

    def test_cycle_error_message_includes_path(self):
        t1 = _make_task(1, blocked_by=[2])
        t2 = _make_task(2, blocked_by=[1])
        with self.assertRaises(IssuesError) as ctx:
            query_next([t1, t2])
        # The error message should contain task IDs.
        msg = str(ctx.exception)
        self.assertIn('1', msg)
        self.assertIn('2', msg)

    def test_linear_chain_returns_tail(self):
        """1 blocked by 2, 2 blocked by 3 — only 3 is ready."""
        t1 = _make_task(1, blocked_by=[2])
        t2 = _make_task(2, blocked_by=[3])
        t3 = _make_task(3)
        result = query_next([t1, t2, t3])
        self.assertEqual(result['number'], 3)


# ---------------------------------------------------------------------------
# query_ready_set
# ---------------------------------------------------------------------------


class QueryReadySetTests(unittest.TestCase):
    """query_ready_set: returns full sorted ready set."""

    def test_empty_returns_empty(self):
        self.assertEqual(query_ready_set([]), [])

    def test_single_ready_task(self):
        t1 = _make_task(1)
        result = query_ready_set([t1])
        self.assertEqual([t['number'] for t in result], [1])

    def test_excludes_prd(self):
        t1 = _make_task(1, task_type='prd')
        t2 = _make_task(2, task_type='task')
        result = query_ready_set([t1, t2])
        numbers = [t['number'] for t in result]
        self.assertNotIn(1, numbers)
        self.assertIn(2, numbers)

    def test_excludes_closed(self):
        t1 = _make_task(1, state='closed')
        t2 = _make_task(2, state='open')
        result = query_ready_set([t1, t2])
        self.assertEqual([t['number'] for t in result], [2])

    def test_excludes_blocked_by_open(self):
        t1 = _make_task(1)
        t2 = _make_task(2, blocked_by=[1])
        result = query_ready_set([t1, t2])
        self.assertEqual([t['number'] for t in result], [1])

    def test_branching_both_ready_when_blocker_closed(self):
        """When 1 is closed, both 2 and 3 are ready."""
        t1 = _make_task(1, state='closed')
        t2 = _make_task(2, blocked_by=[1])
        t3 = _make_task(3, blocked_by=[1])
        result = query_ready_set([t1, t2, t3])
        numbers = sorted(t['number'] for t in result)
        self.assertEqual(numbers, [2, 3])

    def test_sorted_by_priority_then_id(self):
        t1 = _make_task(1, priority=2)
        t2 = _make_task(2, priority=1)
        t3 = _make_task(3, priority=None)
        result = query_ready_set([t1, t2, t3])
        self.assertEqual([t['number'] for t in result], [2, 1, 3])

    def test_label_filter(self):
        t1 = _make_task(1, labels=['foo'])
        t2 = _make_task(2, labels=['bar'])
        result = query_ready_set([t1, t2], labels=['foo'])
        self.assertEqual([t['number'] for t in result], [1])

    def test_cycle_raises_issues_error(self):
        t1 = _make_task(1, blocked_by=[2])
        t2 = _make_task(2, blocked_by=[1])
        with self.assertRaises(IssuesError):
            query_ready_set([t1, t2])


if __name__ == '__main__':
    unittest.main()
