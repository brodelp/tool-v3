"""Byte-exact JSON member splicing: member spans, replacement, append, and removal."""

import pytest

from scat_validator.splice import (
    append_member,
    json_name,
    member,
    object_members,
    remove_member,
    replace_member,
    replace_value,
)
from scat_validator.updates import with_set, without_set


def test_members_report_name_and_value_spans() -> None:
    text = '{ "a" : 1 ,"b":[2]}'
    a, b = object_members(text)
    assert (a.name, b.name) == ("a", "b")
    assert text[a.name_start] == '"'
    assert text[a.value_start : a.value_end] == "1"
    assert text[b.name_start : b.value_end] == '"b":[2]'
    assert member([a, b], "b") is b
    assert member([a, b], "c") is None


def test_escaped_names_are_decoded() -> None:
    assert [m.name for m in object_members('{"\\u0061nnotations":1}')] == ["annotations"]


def test_nested_object_members() -> None:
    text = '{"a":{ "x":1,"y":{"z":2}},"b":3}'
    start = text.index("{", 1)
    assert [m.name for m in object_members(text, start)] == ["x", "y"]
    assert [m.name for m in object_members(text)] == ["a", "b"]


def test_empty_object_has_no_members() -> None:
    assert object_members("{ }") == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("{}", '{"x":2}'),
        ("{ }", '{ "x":2}'),
        ('{"a":1}', '{"a":1,"x":2}'),
        ('{"a":1 }', '{"a":1,"x":2 }'),
        ('{"a":1.0E2\n}', '{"a":1.0E2,"x":2\n}'),
    ],
)
def test_append_member(text: str, expected: str) -> None:
    assert append_member(text, 0, '"x":2') == expected


def test_append_member_to_nested_object() -> None:
    text = '{"a":{"b":1},"c":2}'
    assert append_member(text, 5, '"x":2') == '{"a":{"b":1,"x":2},"c":2}'


@pytest.mark.parametrize(
    ("text", "name", "expected"),
    [
        ('{"a":1.0E2,"b":2,"c":3}', "a", '{"b":2,"c":3}'),
        ('{"a":1.0E2 , "b" : 2 ,"c":3}', "b", '{"a":1.0E2  ,"c":3}'),
        ('{"a":1,"b":2 , "c":1.0E2 }', "c", '{"a":1,"b":2  }'),
        ('{ "a" : 1.0E2 }', "a", "{  }"),
        ('{"a":1,"b":2}', "a", '{"b":2}'),
    ],
)
def test_remove_member(text: str, name: str, expected: str) -> None:
    assert remove_member(text, 0, name) == expected


def test_remove_member_absent_raises() -> None:
    with pytest.raises(KeyError):
        remove_member('{"a":1}', 0, "b")


def test_replace_member_keeps_neighbors_byte_exact() -> None:
    text = '{"a" : 1.0E2 , "b" :\t[ 1 ] ,"c":"😀"}'
    span = member(object_members(text), "b")
    assert span is not None
    assert replace_member(text, span, '"d":{}') == '{"a" : 1.0E2 , "d":{} ,"c":"😀"}'
    assert replace_value(text, span, "null") == '{"a" : 1.0E2 , "b" :\tnull ,"c":"😀"}'


def test_json_name_keeps_non_ascii() -> None:
    assert json_name('a"é') == '"a\\"é"'


def test_with_set_replaces_appends_and_creates() -> None:
    sets = '{"a":1,"annotation_sets" : {"x":{}} }'
    assert with_set(sets, "x", "NEW") == '{"a":1,"annotation_sets" : {"x":NEW} }'
    assert with_set(sets, "y", "NEW") == '{"a":1,"annotation_sets" : {"x":{},"y":NEW} }'
    assert with_set('{"a":1}', "y", "NEW") == '{"a":1,"annotation_sets":{"y":NEW}}'


def test_without_set_removes_or_reports_absence() -> None:
    text = '{"a":1,"annotation_sets":{"x":{},"y":2}}'
    assert without_set(text, "x") == '{"a":1,"annotation_sets":{"y":2}}'
    assert without_set('{"a":1,"annotation_sets":{"x":{}}}', "x") == '{"a":1,"annotation_sets":{}}'
    assert without_set(text, "z") is None
    assert without_set('{"a":1}', "x") is None
