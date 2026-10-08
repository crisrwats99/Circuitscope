"""
core/tutor.py

The TUTOR's side: load the submissions students handed in, mark the theory
answers, and produce the mark sheet and each student's feedback. No screen code
here (the Tutor tab is just buttons on top of this), so the rules can be tested exactly.

What it does:
    * loads submission files and REFUSES any whose signature fails (each refusal
      names the file and the reason, the other files still load)
    * keeps one submission per student number (a re-submission replaces an earlier one)
    * warns about problems a tutor should know: the assessment was edited after the
      student started, or one box was used by several students (the UID watermark)
    * holds the tutor's True/False decision for each THEORY item and remembers them
      in a file, so marking can be stopped and continued
    * calculates marks with the same grader the practice mode uses
    * writes the CSV mark sheet and one feedback text file per student, at a level
      the lecturer chooses (marks only / correct-incorrect / full detail)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum

from core.export import MarkRow, write_marksheet
from core.grading import MISSING_VARIABLE, NOT_ANSWERED, OUT_OF_TOLERANCE, THEORY_INCORRECT, GradeReport, Status
from core.signing_key import SIGNING_KEY
from core.models import Assessment, ItemType
from core.submission import (Submission, SubmissionError, check_matches,
                             grade_submission, load_submission)

MARKING_FORMAT = "circuitscope-marking"
MARKING_VERSION = 1


class FeedbackLevel(str, Enum):
    MARKS_ONLY = "marks_only"        # totals and a mark for each question
    CORRECTNESS = "correctness"      # + each item right or wrong, with a reason but no expected values
    FULL = "full"                    # + measured and expected values, and model answers for theory


@dataclass
class StudentRecord:
    source: str                                   # the file it came from
    submission: Submission
    decisions: dict[str, bool] = field(default_factory=dict)   # theory item id -> tutor's decision

    @property
    def student_number(self) -> str:
        return self.submission.student_number


class TutorSession:
    def __init__(self, assessment: Assessment, key: bytes = SIGNING_KEY):
        if assessment.answers_removed:
            raise ValueError("This is a student copy with the answers removed, so it cannot be used for "
                             "grading. Use the lecturer's own file.")
        self.assessment = assessment
        self.key = key
        self.records: list[StudentRecord] = []
        self._saved: dict[str, dict[str, bool]] = {}     # decisions loaded from the marking file
        self._theory_ids = [i.id for _q, _p, i in assessment.iter_items() if i.item_type == ItemType.THEORY]

    # ---- loading submissions ------------------------------------------------------

    def load_files(self, paths: list[str]) -> list[str]:
        """Load each file. Returns plain-English messages (one per file that was refused or replaced)."""
        messages: list[str] = []
        for path in paths:
            name = os.path.basename(path)
            try:
                sub = load_submission(path, self.key)
            except SubmissionError as exc:
                messages.append(f"{name}: not loaded. {exc}")
                continue
            note = self.add_submission(sub, path)
            if note:
                messages.append(f"{name}: {note}")
        return messages

    def add_submission(self, sub: Submission, source: str = "") -> str:
        """Add one student's work. Returns a note if it replaced or lost against an earlier one."""
        number = sub.student_number
        existing = next((r for r in self.records if r.student_number == number), None)
        if existing is not None:
            if sub.created_utc < existing.submission.created_utc:
                return (f"ignored: student {number} already has a later submission "
                        f"({existing.submission.created_utc}).")
            self.records.remove(existing)
            note = f"replaced the earlier submission from student {number} ({existing.submission.created_utc})."
        else:
            note = ""
        self.records.append(StudentRecord(source, sub, dict(self._saved.get(number, {}))))
        self.records.sort(key=lambda r: r.student_number)
        return note

    def record(self, student_number: str) -> StudentRecord:
        for r in self.records:
            if r.student_number == student_number:
                return r
        raise KeyError(f"No submission from student '{student_number}'.")

    # ---- problems worth knowing about -----------------------------------------------

    def notes(self, record: StudentRecord) -> list[str]:
        out = list(check_matches(self.assessment, record.submission))
        uid = record.submission.device_uid
        others = sorted(r.student_number for r in self.records
                        if r is not record and r.submission.device_uid == uid)
        if others:
            out.append(f"The same box ({uid}) was also used by: {', '.join(others)}.")
        return out

    # ---- theory marking -----------------------------------------------------------------

    def theory_items(self) -> list[str]:
        return list(self._theory_ids)

    def pending_theory(self, record: StudentRecord) -> list[str]:
        """Theory items the tutor has not marked yet for this student."""
        return [i for i in self._theory_ids if i not in record.decisions]

    def set_decision(self, student_number: str, item_id: str, correct: bool | None) -> None:
        """Mark a theory answer correct (True) or incorrect (False); None removes the decision."""
        record = self.record(student_number)
        if item_id not in self._theory_ids:
            raise ValueError(f"Item '{item_id}' is not a theory item, so it is not marked by hand.")
        if correct is None:
            record.decisions.pop(item_id, None)
        elif isinstance(correct, bool):
            record.decisions[item_id] = correct
        else:
            raise ValueError("A decision must be True (correct), False (incorrect) or None (not marked).")

    def total_pending(self) -> int:
        return sum(len(self.pending_theory(r)) for r in self.records)

    def save_marking(self, path: str) -> None:
        """Remember the tutor's decisions (also those for students not loaded right now)."""
        merged = {n: dict(d) for n, d in self._saved.items()}
        for r in self.records:
            merged[r.student_number] = dict(r.decisions)
        self._saved = merged
        data = {"format": MARKING_FORMAT, "version": MARKING_VERSION,
                "assessment_id": self.assessment.assessment_id, "decisions": merged}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)

    def load_marking(self, path: str) -> None:
        """Read earlier decisions; applies to students already loaded and to ones loaded later."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"The marking file could not be read ({exc}).") from exc
        if not isinstance(data, dict) or data.get("format") != MARKING_FORMAT:
            raise ValueError("This is not a Circuitscope marking file.")
        if data.get("assessment_id") != self.assessment.assessment_id:
            raise ValueError("This marking file belongs to a different assessment.")
        try:
            self._saved = {str(n): {str(i): bool(v) for i, v in d.items()}
                           for n, d in data.get("decisions", {}).items()}
        except (AttributeError, TypeError) as exc:
            raise ValueError(f"The marking file is damaged ({exc}).") from exc
        for r in self.records:
            r.decisions.update(self._saved.get(r.student_number, {}))

    # ---- marks ---------------------------------------------------------------------------------

    def report(self, record: StudentRecord) -> GradeReport:
        return grade_submission(self.assessment, record.submission, record.decisions)

    def sheet_notes(self, record: StudentRecord) -> list[str]:
        """What goes in the mark sheet's Notes column: the warnings, and any unmarked theory."""
        notes = self.notes(record)
        pending = self.pending_theory(record)
        if pending:
            notes.append(f"{len(pending)} theory answer(s) not marked yet ({', '.join(pending)}).")
        return notes

    def write_marksheet(self, path: str) -> None:
        rows = [MarkRow(r.student_number, r.submission.device_uid, self.report(r), self.sheet_notes(r))
                for r in self.records]
        write_marksheet(path, self.assessment, rows)

    # ---- feedback for students ----------------------------------------------------------------------

    def feedback_text(self, record: StudentRecord, level: FeedbackLevel) -> str:
        report = self.report(record)
        results = {r.item_id: r for r in report.item_results}
        lines = [f"Circuitscope feedback: {self.assessment.title}",
                 f"Student number: {record.student_number}",
                 f"Total: {report.total:g} / {report.total_available:g}"]
        pending = self.pending_theory(record)
        if pending:
            lines.append(f"Note: {len(pending)} theory answer(s) have not been marked yet, so this total "
                         f"may still go up.")
        lines.append("")
        for question in self.assessment.questions:
            qr = next(q for q in report.question_results if q.question_id == question.id)
            lines.append(f"Question {question.id}: {question.title}  {qr.awarded:g} / {qr.available:g}")
            if level == FeedbackLevel.MARKS_ONLY:
                continue
            for part in question.parts:
                for item in part.items:
                    r = results[item.id]
                    if item.id in pending:                       # a theory answer nobody has marked yet
                        lines.append(f"  {item.id} {item.description}: Awaiting tutor marking (?/{r.available:g})")
                        continue
                    verdict = "Correct" if r.status == Status.CORRECT else "Not correct"
                    lines.append(f"  {item.id} {item.description}: {verdict} ({r.awarded:g}/{r.available:g})")
                    if r.status != Status.CORRECT:
                        lines.append(f"      {self._reason(r, level)}")
                        if level == FeedbackLevel.FULL and item.item_type == ItemType.THEORY and item.model_answer:
                            lines.append(f"      Model answer: {item.model_answer}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _reason(result, level: FeedbackLevel) -> str:
        """Why an item lost marks. Below FULL the expected value and tolerance are not revealed."""
        if level == FeedbackLevel.FULL:
            return result.message
        if result.error_code == OUT_OF_TOLERANCE:
            return "Your value was outside the allowed range."
        if result.error_code == THEORY_INCORRECT:
            return "Your answer was marked incorrect."
        if result.error_code == MISSING_VARIABLE:
            return result.message                      # names the value the student did not give
        if result.error_code == NOT_ANSWERED:
            return "No answer was recorded."
        if result.message.startswith("Waveform could not be analysed"):
            return result.message
        return "This item could not be graded automatically. Ask your tutor."

    def write_feedback(self, folder: str, level: FeedbackLevel) -> list[str]:
        """One .txt file per student in `folder`. Returns the paths written."""
        os.makedirs(folder, exist_ok=True)
        written = []
        for r in self.records:
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", r.student_number) or "student"
            path = os.path.join(folder, f"{safe}.txt")
            n = 2
            while path in written:                       # two numbers that clean up to the same name
                path = os.path.join(folder, f"{safe}_{n}.txt")
                n += 1
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.feedback_text(r, level))
            written.append(path)
        return written
