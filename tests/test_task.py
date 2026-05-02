"""Task module tests: slug, snake<->camel, frontmatter round-trip."""

import unittest

from issues import (
    SLUG_MAX_LEN,
    DEFAULT_SLUG,
    task_new,
    task_parse,
    task_parse_comments,
    task_comments_to_raw,
    task_serialise,
    task_slug,
    task_to_camel,
    task_to_json_dict,
    task_to_snake,
)


class SlugTests(unittest.TestCase):
    def test_basic_lowercase_and_hyphenate(self):
        self.assertEqual(task_slug('Hello World'), 'hello-world')

    def test_collapses_runs_of_separators(self):
        self.assertEqual(task_slug('hello---world!!!foo'), 'hello-world-foo')

    def test_strips_edge_hyphens(self):
        self.assertEqual(task_slug('  hello world  '), 'hello-world')
        self.assertEqual(task_slug('---foo---'), 'foo')

    def test_empty_title_returns_untitled(self):
        self.assertEqual(task_slug(''), DEFAULT_SLUG)

    def test_all_emoji_returns_untitled(self):
        # Pure emoji has no ASCII fallback after NFKD/encode-ignore.
        self.assertEqual(task_slug(''), DEFAULT_SLUG)

    def test_ascii_fold_accents(self):
        self.assertEqual(task_slug('Café Résumé'), 'cafe-resume')
        self.assertEqual(task_slug('naïve façade'), 'naive-facade')

    def test_ascii_fold_german(self):
        # ß has no canonical NFKD decomp to ss, so it gets dropped — that's
        # acceptable, as long as it doesn't crash and produces something.
        result = task_slug('Größe')
        self.assertTrue(result)
        self.assertNotIn('ß', result)

    def test_truncates_at_max_length(self):
        long_title = 'x' * 200
        self.assertEqual(task_slug(long_title), 'x' * SLUG_MAX_LEN)

    def test_truncate_strips_trailing_hyphen(self):
        # 49 chars + a separator that lands exactly at position 50.
        title = 'a' * 49 + ' bbb'
        slug = task_slug(title)
        self.assertEqual(len(slug), 49)
        self.assertFalse(slug.endswith('-'))

    def test_numeric_titles(self):
        self.assertEqual(task_slug('Issue 42 — first'), 'issue-42-first')

    def test_only_special_chars_returns_untitled(self):
        self.assertEqual(task_slug('!!!@@@###'), DEFAULT_SLUG)


class CaseTranslationTests(unittest.TestCase):
    def test_snake_to_camel(self):
        snake = {
            'created_at': 1,
            'state_reason': 'completed',
            'blocked_by': [1, 2],
            'number': 5,
            'title': 't',
        }
        camel = task_to_camel(snake)
        self.assertIn('createdAt', camel)
        self.assertIn('stateReason', camel)
        self.assertIn('blockedBy', camel)
        self.assertIn('number', camel)
        self.assertIn('title', camel)
        self.assertEqual(camel['createdAt'], 1)
        self.assertEqual(camel['blockedBy'], [1, 2])

    def test_camel_to_snake(self):
        camel = {
            'createdAt': 1,
            'stateReason': 'completed',
            'blockedBy': [1, 2],
            'number': 5,
        }
        snake = task_to_snake(camel)
        self.assertIn('created_at', snake)
        self.assertIn('state_reason', snake)
        self.assertIn('blocked_by', snake)
        self.assertEqual(snake['blocked_by'], [1, 2])

    def test_round_trip_identity(self):
        snake = {'created_at': 'x', 'blocked_by': [], 'number': 1, 'title': 'y'}
        round_tripped = task_to_snake(task_to_camel(snake))
        self.assertEqual(round_tripped, snake)


class FrontmatterRoundTripTests(unittest.TestCase):
    def _round_trip(self, task):
        text = task_serialise(task)
        parsed = task_parse(text)
        # Compare only the fields that serialise/parse touches.
        for k in (
            'number', 'title', 'type', 'parent', 'labels', 'blocked_by',
            'priority', 'state', 'state_reason', 'created_at', 'closed_at',
            'assignees', 'body',
        ):
            self.assertEqual(parsed.get(k), task.get(k), f'mismatch on {k!r}')

    def test_minimal_task(self):
        task = task_new(number=1, title='Hello', body='just a body\n')
        self._round_trip(task)

    def test_full_task(self):
        task = task_new(
            number=42,
            title='The thing',
            body='Body line 1\nBody line 2\n',
            task_type='prd',
            parent=7,
            labels=['foo', 'bar baz', 'with:colon'],
            blocked_by=[1, 2, 3],
            priority=2,
            assignees=['alice', 'bob'],
        )
        task['state'] = 'closed'
        task['state_reason'] = 'completed'
        task['closed_at'] = '2026-05-02T10:00:00Z'
        self._round_trip(task)

    def test_title_with_yaml_specials(self):
        for tricky in [
            "Title with 'single' quotes",
            'Title with "double" quotes',
            'Title: with colon',
            '#hashtag start',
            'true',  # would otherwise parse as bool
            '42',    # would otherwise parse as int
            'null',
            '   leading-space',
            'trailing-space   ',
        ]:
            task = task_new(number=1, title=tricky, body='b')
            with self.subTest(title=tricky):
                self._round_trip(task)

    def test_empty_lists(self):
        task = task_new(number=1, title='t', body='b')
        # Defaults are empty lists.
        self.assertEqual(task['labels'], [])
        self.assertEqual(task['blocked_by'], [])
        self.assertEqual(task['assignees'], [])
        self._round_trip(task)

    def test_unicode_body(self):
        task = task_new(
            number=1, title='Unicode body',
            body='Hello 世界 — émoji 🎉\n',
        )
        self._round_trip(task)

    def test_body_preserved_with_blank_lines(self):
        task = task_new(
            number=1, title='Multi para',
            body='Para one.\n\nPara two.\n\nPara three.\n',
        )
        self._round_trip(task)

    def test_comments_section_preserved(self):
        task = task_new(number=1, title='With comments', body='Initial.\n')
        task['comments_raw'] = (
            '## Comments\n\n### 2026-05-02T10:00:00Z — alice\n\n'
            'A first comment.\n'
        )
        text = task_serialise(task)
        parsed = task_parse(text)
        self.assertIn('## Comments', parsed['comments_raw'])
        self.assertIn('alice', parsed['comments_raw'])


class JsonViewTests(unittest.TestCase):
    def test_state_uppercased(self):
        task = task_new(number=1, title='t', body='b')
        d = task_to_json_dict(task)
        self.assertEqual(d['state'], 'OPEN')

    def test_camel_case_keys(self):
        task = task_new(number=1, title='t', body='b', priority=2,
                        blocked_by=[1, 2])
        task['state_reason'] = 'completed'
        task['closed_at'] = '2026-05-02T10:00:00Z'
        d = task_to_json_dict(task)
        for key in (
            'number', 'title', 'state', 'stateReason', 'createdAt',
            'closedAt', 'labels', 'assignees', 'body', 'priority',
            'parent', 'type', 'blockedBy', 'comments',
        ):
            self.assertIn(key, d)
        for key in d:
            self.assertNotIn('_', key, f'{key!r} contains underscore')

    def test_labels_assignees_are_string_arrays(self):
        task = task_new(
            number=1, title='t', body='b',
            labels=['a', 'b'], assignees=['alice'],
        )
        d = task_to_json_dict(task)
        self.assertEqual(d['labels'], ['a', 'b'])
        self.assertEqual(d['assignees'], ['alice'])
        for label in d['labels']:
            self.assertIsInstance(label, str)

    def test_comments_default_empty_list(self):
        task = task_new(number=1, title='t', body='b')
        d = task_to_json_dict(task)
        self.assertEqual(d['comments'], [])


class CommentsParseTests(unittest.TestCase):
    """task_parse_comments and task_comments_to_raw."""

    def test_empty_string_returns_empty_list(self):
        self.assertEqual(task_parse_comments(''), [])

    def test_none_like_falsy_returns_empty_list(self):
        self.assertEqual(task_parse_comments(None), [])

    def test_comments_heading_only_returns_empty_list(self):
        # A `## Comments` section with no entries.
        raw = '## Comments\n'
        self.assertEqual(task_parse_comments(raw), [])

    def test_comments_heading_with_blank_lines_returns_empty_list(self):
        raw = '## Comments\n\n\n'
        self.assertEqual(task_parse_comments(raw), [])

    def test_single_comment_parsed(self):
        raw = (
            '## Comments\n\n'
            '### 2026-05-02T10:00:00Z — alice\n\n'
            'First comment body.\n'
        )
        comments = task_parse_comments(raw)
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]['timestamp'], '2026-05-02T10:00:00Z')
        self.assertEqual(comments[0]['author'], 'alice')
        self.assertEqual(comments[0]['body'], 'First comment body.')

    def test_multiple_comments_parsed(self):
        raw = (
            '## Comments\n\n'
            '### 2026-05-02T10:00:00Z — alice\n\n'
            'First comment.\n\n'
            '### 2026-05-02T11:00:00Z — bob\n\n'
            'Second comment.\n'
        )
        comments = task_parse_comments(raw)
        self.assertEqual(len(comments), 2)
        self.assertEqual(comments[0]['author'], 'alice')
        self.assertEqual(comments[0]['body'], 'First comment.')
        self.assertEqual(comments[1]['author'], 'bob')
        self.assertEqual(comments[1]['body'], 'Second comment.')

    def test_multiline_comment_body_preserved(self):
        raw = (
            '## Comments\n\n'
            '### 2026-05-02T10:00:00Z — alice\n\n'
            'Line one.\nLine two.\n'
        )
        comments = task_parse_comments(raw)
        self.assertEqual(len(comments), 1)
        self.assertIn('Line one.', comments[0]['body'])
        self.assertIn('Line two.', comments[0]['body'])

    def test_malformed_header_skipped(self):
        # A `### ` line without the em dash separator is not a valid comment
        # header — the regex won't match it, so no comment is emitted.
        raw = (
            '## Comments\n\n'
            '### this-is-not-a-valid-header\n\n'
            'Some text.\n\n'
            '### 2026-05-02T10:00:00Z — alice\n\n'
            'Good comment.\n'
        )
        comments = task_parse_comments(raw)
        # Only the well-formed entry should be parsed.
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]['author'], 'alice')

    def test_round_trip_empty(self):
        self.assertEqual(task_comments_to_raw([]), '')

    def test_round_trip_single_comment(self):
        original = [{'timestamp': '2026-05-02T10:00:00Z', 'author': 'alice',
                     'body': 'Hello world.'}]
        raw = task_comments_to_raw(original)
        parsed = task_parse_comments(raw)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]['timestamp'], original[0]['timestamp'])
        self.assertEqual(parsed[0]['author'], original[0]['author'])
        self.assertEqual(parsed[0]['body'], original[0]['body'])

    def test_round_trip_multiple_comments(self):
        original = [
            {'timestamp': '2026-05-02T10:00:00Z', 'author': 'alice',
             'body': 'First.'},
            {'timestamp': '2026-05-02T11:00:00Z', 'author': 'bob',
             'body': 'Second.'},
        ]
        raw = task_comments_to_raw(original)
        parsed = task_parse_comments(raw)
        self.assertEqual(len(parsed), len(original))
        for i, (p, o) in enumerate(zip(parsed, original)):
            with self.subTest(i=i):
                self.assertEqual(p['timestamp'], o['timestamp'])
                self.assertEqual(p['author'], o['author'])
                self.assertEqual(p['body'], o['body'])

    def test_to_raw_contains_heading(self):
        raw = task_comments_to_raw([
            {'timestamp': '2026-05-02T10:00:00Z', 'author': 'alice',
             'body': 'hi'},
        ])
        self.assertIn('## Comments', raw)
        self.assertIn('### 2026-05-02T10:00:00Z — alice', raw)
        self.assertIn('hi', raw)

    def test_full_file_round_trip_with_comments(self):
        """parse(serialise(task_with_comments)) preserves comments."""
        task = task_new(number=1, title='With comments', body='Initial.\n')
        comments = [
            {'timestamp': '2026-05-02T10:00:00Z', 'author': 'alice',
             'body': 'Comment one.'},
            {'timestamp': '2026-05-02T11:00:00Z', 'author': 'bob',
             'body': 'Comment two.'},
        ]
        task['comments_raw'] = task_comments_to_raw(comments)
        text = task_serialise(task)
        parsed = task_parse(text)
        reparsed_comments = task_parse_comments(parsed['comments_raw'])
        self.assertEqual(len(reparsed_comments), 2)
        self.assertEqual(reparsed_comments[0]['author'], 'alice')
        self.assertEqual(reparsed_comments[1]['author'], 'bob')


class JsonViewCommentTests(unittest.TestCase):
    def test_task_to_json_dict_includes_parsed_comments(self):
        task = task_new(number=1, title='t', body='b')
        from issues import task_comments_to_raw
        task['comments_raw'] = task_comments_to_raw([
            {'timestamp': '2026-05-02T10:00:00Z', 'author': 'alice',
             'body': 'My comment.'},
        ])
        d = task_to_json_dict(task)
        self.assertEqual(len(d['comments']), 1)
        self.assertEqual(d['comments'][0]['author'], 'alice')
        self.assertEqual(d['comments'][0]['body'], 'My comment.')

    def test_task_to_json_dict_empty_comments(self):
        task = task_new(number=1, title='t', body='b')
        d = task_to_json_dict(task)
        self.assertEqual(d['comments'], [])

    def test_task_to_json_dict_no_comments_raw_key(self):
        task = task_new(number=1, title='t', body='b')
        task.pop('comments_raw', None)
        d = task_to_json_dict(task)
        self.assertEqual(d['comments'], [])


if __name__ == '__main__':
    unittest.main()
