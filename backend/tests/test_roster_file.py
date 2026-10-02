"""Первичный список группы из файла: читается один раз, дальше им владеет староста."""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from app import moscow
from app.attendance import roster_file, store
from app.attendance.models import RosterEntry

NOW = dt.datetime(2026, 9, 29, 9, 0, tzinfo=moscow.MOSCOW)


def _names(conn: sqlite3.Connection) -> list[str]:
    return [student.name for student in store.active_students(conn)]


def test_a_line_can_be_a_full_name_a_surname_or_numbered_and_comments_are_skipped() -> None:
    text = "# список\n\n1. Иванов  Иван \n2) Петрова\nСидоров Пётр Петрович\n   \n# конец\n"

    assert roster_file.parse(text) == ["Иванов Иван", "Петрова", "Сидоров Пётр Петрович"]


def test_the_same_name_twice_is_taken_once() -> None:
    assert roster_file.parse("Иванов Иван\nиванов иван\nЁлкин Пётр\nелкин пётр") == ["Иванов Иван", "Ёлкин Пётр"]


def test_an_empty_journal_gets_the_list_from_the_file(conn: sqlite3.Connection, tmp_path: Path) -> None:
    file = tmp_path / "roster.txt"
    file.write_text("Петрова Анна\nАзизов\nИванов Иван\n", encoding="utf-8")

    assert roster_file.seed(conn, file, now=NOW) == 3
    assert _names(conn) == ["Азизов", "Иванов Иван", "Петрова Анна"]


def test_the_file_is_read_only_once(conn: sqlite3.Connection, tmp_path: Path) -> None:
    file = tmp_path / "roster.txt"
    file.write_text("Иванов Иван\nПетрова Анна\n", encoding="utf-8")
    roster_file.seed(conn, file, now=NOW)
    # Староста поправил список в журнале; файл потом дополнили: журнал его не слушает.
    store.save_roster(conn, [RosterEntry(name="Иванов Иван")], now=NOW)
    file.write_text("Иванов Иван\nПетрова Анна\nСидоров Пётр\n", encoding="utf-8")

    assert roster_file.seed(conn, file, now=NOW) == 0
    assert _names(conn) == ["Иванов Иван"]


def test_a_headman_who_emptied_the_list_does_not_get_it_back(conn: sqlite3.Connection, tmp_path: Path) -> None:
    file = tmp_path / "roster.txt"
    file.write_text("Иванов Иван\n", encoding="utf-8")
    roster_file.seed(conn, file, now=NOW)
    store.save_roster(conn, [], now=NOW)

    assert roster_file.seed(conn, file, now=NOW) == 0 and _names(conn) == []


def test_no_file_is_not_an_error(conn: sqlite3.Connection, tmp_path: Path) -> None:
    assert roster_file.seed(conn, tmp_path / "нет.txt", now=NOW) == 0 and _names(conn) == []


def test_an_unreadable_file_is_not_fatal(conn: sqlite3.Connection, tmp_path: Path) -> None:
    file = tmp_path / "roster.txt"
    file.write_bytes(b"\xff\xfe\x00bad")

    assert roster_file.seed(conn, file, now=NOW) == 0 and _names(conn) == []


def test_the_example_in_the_repo_is_a_valid_roster() -> None:
    example = Path(__file__).resolve().parents[2] / "roster.example.txt"

    assert roster_file.parse(example.read_text(encoding="utf-8")) == ["Иванов Иван", "Петрова Анна", "Сидоров Пётр"]
