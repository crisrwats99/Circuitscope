"""
core/export.py

Writes the lecturer's MARK SHEET as a CSV file that opens in Excel.

One row per student:
    Student number | Device UID | Q1 (/5) | Q2 (/3) | Q3 (/2) | Total | Out of | Needs review

The question columns are generated automatically from the assessment, so a
practical with 4 questions gets 4 columns and one with 9 gets 9. "Needs review"
counts the items the tutor must look at (could not be graded, e.g. a bad
waveform, or nothing recorded).

CSV is used here (and only here) because it is a flat table for Excel; the
assessments themselves are JSON.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass

from core.grading import GradeReport, Status
from core.models import Assessment


@dataclass
class MarkRow:
    """One student's line in the mark sheet."""
    student_number: str
    device_uid: str
    report: GradeReport


def _safe_text(value: str) -> str:
    """
    Excel treats a cell starting with = + - @ as a FORMULA and runs it, so a
    student number like '=cmd|...' could do harm when the lecturer opens the
    file. Putting an apostrophe in front makes Excel treat it as plain text.
    """
    text = str(value)
    if text and text[0] in "=+-@\t\r":
        return "'" + text
    return text


def _number(x: float) -> str:
    """Marks like 3.0 -> '3', 2.5 -> '2.5', and 0.1+0.2 -> '0.3' (no float noise)."""
    text = f"{x:.3f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def question_columns(assessment: Assessment) -> list[tuple[str, str]]:
    """
    The auto-generated question columns as (question id, header text), e.g.
    ('1', 'Q1 (/5)'). The number in brackets is what the question is out of.
    """
    columns = []
    for question in assessment.questions:
        out_of = sum(item.marks for part in question.parts for item in part.items)
        columns.append((question.id, f"Q{question.id} (/{_number(out_of)})"))
    return columns


def marksheet_rows(assessment: Assessment, rows: list[MarkRow]) -> list[list[str]]:
    """The whole mark sheet as a list of text rows (header first)."""
    columns = question_columns(assessment)
    header = ["Student number", "Device UID"] + [h for _id, h in columns] \
        + ["Total", "Out of", "Needs review"]
    table = [header]
    for row in rows:
        marks = row.report.question_marks                       # question id -> mark
        review = sum(1 for r in row.report.item_results
                     if r.status in (Status.CANNOT_GRADE, Status.NOT_ANSWERED))
        table.append(
            [_safe_text(row.student_number), _safe_text(row.device_uid)]
            + [_number(marks.get(qid, 0.0)) for qid, _h in columns]
            + [_number(row.report.total), _number(row.report.total_available), str(review)])
    return table


def write_marksheet(path: str, assessment: Assessment, rows: list[MarkRow]) -> None:
    """Write the mark sheet to `path` (.csv)."""
    # utf-8-sig adds a small marker at the start so Excel reads accents correctly.
    # newline="" stops Python adding blank lines between rows on Windows.
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(marksheet_rows(assessment, rows))


def write_waveform_csv(path: str, samples, sample_rate_hz: float, ref_samples=None) -> None:
    """
    Save a capture as CSV (time in seconds, volts) so it can be plotted in Excel or
    included in a report. A reference channel adds a third column.
    """
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        header = ["time_s", "volts"] + (["ref_volts"] if ref_samples is not None else [])
        writer.writerow(header)
        for i, volts in enumerate(samples):
            row = [f"{i / sample_rate_hz:.9g}", f"{volts:.6f}"]
            if ref_samples is not None:
                row.append(f"{ref_samples[i]:.6f}")
            writer.writerow(row)
