"""argparse-layer tests for src.ingest.__main__."""

from __future__ import annotations

import pytest

from src.ingest.__main__ import _parse_pages, main


def test_parse_pages_range():
    assert _parse_pages("0-3") == [0, 1, 2, 3]


def test_parse_pages_csv():
    assert _parse_pages("0,2,4") == [0, 2, 4]


def test_parse_pages_mixed():
    assert _parse_pages("0-2,5,7-8") == [0, 1, 2, 5, 7, 8]


def test_parse_pages_none():
    assert _parse_pages(None) is None


def test_main_rejects_grade_5(capsys):
    with pytest.raises(SystemExit):
        main([
            "some.pdf",
            "--grade", "5",
            "--subject", "english",
            "--book", "b",
            "--book-id", "bid",
        ])
    err = capsys.readouterr().err
    assert "invalid choice: 5" in err


def test_main_rejects_unknown_subject(capsys):
    with pytest.raises(SystemExit):
        main([
            "some.pdf",
            "--grade", "8",
            "--subject", "physics",
            "--book", "b",
            "--book-id", "bid",
        ])
    err = capsys.readouterr().err
    assert "invalid choice: 'physics'" in err
