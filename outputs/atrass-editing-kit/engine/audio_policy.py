"""Validate explicit sample-underflow intent at plan/interchange boundaries.

This module does not render, resample, or turn on a policy. Frozen reference
history is data, so only active plan/project execution trees are inspected.
"""
FIELD = 'sample_underflow_policy'
STRICT = 'strict'
BOUNDED = 'bounded_linear_rate1_one_sample'
POLICIES = (STRICT, BOUNDED)
# These fields preserve historical evidence, not active renderer options.
HISTORY_FIELDS = {'source_metadata', 'join_origin', 'source_dialogue_binding',
                  'source_visual_binding', 'layout_fit'}


def policy_value(value):
    if value not in POLICIES:
        raise ValueError('Unsupported sample_underflow_policy: ' + repr(value))
    return value


def validate_audio_policy(document):
    """Return mode without mutating input; reject ignored misplaced intent."""
    if not isinstance(document, dict):
        raise ValueError('Audio policy requires a project/plan object')
    if FIELD in document:
        raise ValueError(FIELD + ' belongs only to audio_master')
    master = document.get('audio_master', {})
    if not isinstance(master, dict):
        raise ValueError('audio_master must be an object')
    mode = policy_value(master.get(FIELD, STRICT))

    def visit(value, path):
        if isinstance(value, dict):
            if FIELD in value:
                raise ValueError(FIELD + ' belongs only to audio_master, found at ' + path)
            for key, child in value.items():
                if key not in HISTORY_FIELDS:
                    visit(child, path + '.' + key)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                visit(child, path + '[' + str(i) + ']')

    visit({key: value for key, value in master.items() if key != FIELD}, 'audio_master')
    for group in ('media', 'fonts', 'takes', 'clips', 'layers', 'events', 'audio'):
        visit(document.get(group, {}), group)
    return mode
