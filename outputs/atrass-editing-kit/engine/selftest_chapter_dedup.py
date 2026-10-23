#!/usr/bin/env python3
"""Behavioral guards for explicit chapter text deduplication."""
import copy
import unittest

from deduplicate_chapter_text import resolve_decisions


class ChapterDedupTests(unittest.TestCase):
    def setUp(self):
        self.project = {
            'fps': '60', 'duration': 4, 'clips': [{'keep': 'literal'}],
            'audio': [{'keep': 'literal'}], 'audio_master': {'keep': 'literal'},
            'layers': [
                {'id': 'caption', 'kind': 'caption', 'text': '같은 제목',
                 'in': 1, 'out': 2, 'source_dialogue_binding': {'take_id': 'one'}},
                {'id': 'label', 'kind': 'label', 'text': '같은  제목',
                 'in': 0, 'out': 1, 'source_visual_binding': {'take_id': 'one'}},
                {'id': 'one-chapter-title', 'kind': 'text', 'text': '같은 제목',
                 'in': 1, 'out': 2, 'source_chapter_binding': {'take_id': 'one'}},
            ],
        }
        self.decisions = [
            {'remove_id': 'caption', 'retain_id': 'one-chapter-title'},
            {'remove_id': 'label', 'retain_id': 'one-chapter-title'},
        ]

    def reject(self):
        with self.assertRaises(ValueError):
            resolve_decisions(self.project, self.decisions)

    def test_preserves_edit_and_removed_provenance(self):
        before = copy.deepcopy(self.project)
        result, records = resolve_decisions(self.project, self.decisions)
        self.assertEqual(self.project, before)
        for key in ('clips', 'audio', 'audio_master', 'duration'):
            self.assertEqual(result[key], before[key])
        self.assertEqual(result['layers'], [before['layers'][2]])
        self.assertEqual([x['removed_layer'] for x in records], before['layers'][:2])

    def test_different_text(self):
        self.project['layers'][0]['text'] = '다른 말'
        self.reject()

    def test_different_take(self):
        self.project['layers'][0]['source_dialogue_binding']['take_id'] = 'two'
        self.reject()

    def test_caption_not_fully_covered(self):
        self.project['layers'][0]['in'] = 0.5
        self.reject()

    def test_label_gap(self):
        self.project['layers'][1]['out'] = 0.5
        self.reject()

    def test_repeated_decision(self):
        self.decisions.append(copy.deepcopy(self.decisions[0]))
        self.reject()

    def test_unknown_id(self):
        self.decisions[0]['remove_id'] = 'absent'
        self.reject()


if __name__ == '__main__':
    unittest.main(verbosity=2)
