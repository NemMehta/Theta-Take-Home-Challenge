"""Unit tests for deriving the instance commit from an instance_id (pure)."""

import pytest

from taskbundle.runner import RunnerError, instance_commit_from_id


def test_extracts_40_hex_commit():
    iid = ("instance_ansible__ansible-"
           "cb94c0cc550df9e98f1247bc71d8c2b861c75049"
           "-v1055803c3a812189a1133297f7f5468579283f86")
    assert instance_commit_from_id(iid) == "cb94c0cc550df9e98f1247bc71d8c2b861c75049"


def test_extracts_trailing_commit_for_go_ids():
    # Go instance ids end in `-<40hex>` with no `-v<digest>` suffix.
    iid = "instance_flipt-io__flipt-518ec324b66a07fdd95464a5e9ca5fe7681ad8f9"
    assert instance_commit_from_id(iid) == "518ec324b66a07fdd95464a5e9ca5fe7681ad8f9"


def test_raises_when_pattern_absent():
    with pytest.raises(RunnerError):
        instance_commit_from_id("instance_without_any_commit_marker")
