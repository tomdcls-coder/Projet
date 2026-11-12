import pytest

from worker.workflows.repo_catchup import (
    _extract_prs,
    _extract_tool_payload,
    _one_line_summary,
    build_digest,
    validate_repo,
    validate_since,
)


def test_validate_repo_accepts_owner_name():
    assert validate_repo("owner/repo") == "owner/repo"


def test_validate_repo_accepts_github_url():
    assert validate_repo("https://github.com/owner/repo/") == "owner/repo"


def test_validate_repo_rejects_bad_input():
    with pytest.raises(ValueError):
        validate_repo("just-a-name")
    with pytest.raises(ValueError):
        validate_repo("")


def test_validate_since_normalizes():
    assert validate_since("2025-01-15") == "2025-01-15"


def test_validate_since_rejects_bad_format():
    with pytest.raises(ValueError):
        validate_since("15/01/2025")


def test_extract_tool_payload_from_mcp_text_content():
    import json

    payload = {"items": [{"number": 1}]}
    result = {"content": [{"type": "text", "text": json.dumps(payload)}]}
    assert _extract_tool_payload(result) == payload


def test_extract_tool_payload_falls_back_to_raw():
    result = {"content": [{"type": "text", "text": "not json"}]}
    assert _extract_tool_payload(result) == result


def test_extract_tool_payload_unwraps_structured_content():
    result = {"structuredContent": {"items": []}}
    assert _extract_tool_payload(result) == {"items": []}


def test_extract_prs_from_items():
    payload = {"total_count": 2, "items": [{"number": 1}, {"number": 2}, "junk"]}
    assert _extract_prs(payload) == [{"number": 1}, {"number": 2}]


def test_extract_prs_from_bare_list():
    assert _extract_prs([{"number": 1}]) == [{"number": 1}]


def test_extract_prs_returns_empty_on_unknown_shape():
    assert _extract_prs({"foo": "bar"}) == []
    assert _extract_prs(None) == []


def test_one_line_summary_contains_link_author_and_date():
    pr = {
        "number": 42,
        "title": "Add feature",
        "html_url": "https://github.com/owner/repo/pull/42",
        "user": {"login": "alice"},
        "closed_at": "2025-02-01T10:00:00Z",
    }
    line = _one_line_summary(pr, "closed_at")
    assert "<https://github.com/owner/repo/pull/42|#42 Add feature>" in line
    assert "_alice_" in line
    assert "(2025-02-01)" in line


def test_one_line_summary_truncates_long_title():
    pr = {
        "number": 1,
        "title": "x" * 300,
        "html_url": "https://github.com/owner/repo/pull/1",
        "user": {"login": "bob"},
    }
    line = _one_line_summary(pr, "created_at")
    assert "..." in line
    assert len(line) < 250


def test_build_digest_renders_both_sections():
    merged = [
        {
            "number": 7,
            "title": "Fix bug",
            "html_url": "https://github.com/o/r/pull/7",
            "user": {"login": "dev"},
            "closed_at": "2025-02-02T00:00:00Z",
        }
    ]
    pending = [
        {
            "number": 8,
            "title": "Add feature",
            "html_url": "https://github.com/o/r/pull/8",
            "user": {"login": "dev2"},
            "created_at": "2025-02-03T00:00:00Z",
        }
    ]
    digest = build_digest("o/r", "2025-02-01", merged, pending)
    assert "o/r" in digest
    assert "2025-02-01" in digest
    assert "Fusionnées" in digest
    assert "#7 Fix bug" in digest
    assert "En attente de revue" in digest
    assert "#8 Add feature" in digest


def test_build_digest_empty_sections():
    digest = build_digest("o/r", "2025-02-01", [], [])
    assert "_aucune_" in digest


def test_build_digest_truncates_sections():
    merged = [
        {
            "number": i,
            "title": f"PR {i}",
            "html_url": f"https://github.com/o/r/pull/{i}",
            "user": {"login": "dev"},
            "closed_at": "2025-02-02T00:00:00Z",
        }
        for i in range(30)
    ]
    digest = build_digest("o/r", "2025-02-01", merged, [], max_per_section=20)
    assert "…et 10 de plus" in digest
