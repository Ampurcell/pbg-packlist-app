"""
Pure logic for scanning files, detecting dates in filenames, cleaning names,
and classifying confidence. No Streamlit or Google dependencies here.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, List, Optional, Tuple

# Matches M.D.YY, MM.DD.YYYY, etc. (month.day.year) — month/day 1–2 digits, year 2 or 4 digits.
# Negative lookbehind/ahead: avoid gluing into larger digit runs.
_DATE_PATTERN = re.compile(
    r"(?<![0-9])(?P<m>\d{1,2})\.(?P<d>\d{1,2})\.(?P<y>\d{2}|\d{4})(?![0-9])"
)

# Looks like another dotted number group that could confuse humans (not necessarily a valid date).
_EXTRA_DOT_NUMBER_HINT = re.compile(r"\d{1,3}\.\d{1,3}\.")

# Final rename pattern prefix: "YYYY-MM-DD - …" (used to skip already-done files in batching).
_ALREADY_STANDARDIZED_BASENAME = re.compile(r"^\d{4}-\d{2}-\d{2}\s+-\s+")


def is_already_standardized_basename(filename: str) -> bool:
    """
    True if the file's basename already starts with an ISO date and our separator,
    e.g. "2022-05-10 - ContractName - A - No$$.pdf".
    """
    base = os.path.basename(filename)
    return bool(_ALREADY_STANDARDIZED_BASENAME.match(base))


def filter_paths_skip_standardized(paths: List[str], skip: bool) -> Tuple[List[str], int]:
    """
    If skip is True, drop paths whose basename matches is_already_standardized_basename.
    Returns (paths_for_batching, number_skipped_as_standardized).
    """
    if not skip:
        return list(paths), 0
    kept: List[str] = []
    skipped = 0
    for p in paths:
        if is_already_standardized_basename(p):
            skipped += 1
        else:
            kept.append(p)
    return kept, skipped


def iter_files(root: str, include_subfolders: bool) -> Iterator[str]:
    """Yield file paths under root (files only, not directories)."""
    root = os.path.abspath(os.path.expanduser(root))
    if not include_subfolders:
        if not os.path.isdir(root):
            return
        for name in os.listdir(root):
            p = os.path.join(root, name)
            if os.path.isfile(p):
                yield p
        return

    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            yield os.path.join(dirpath, fn)


def collect_all_files(root: str, include_subfolders: bool) -> List[str]:
    paths = list(iter_files(root, include_subfolders))
    paths.sort(key=lambda p: p.lower())
    return paths


def _parse_year(y_str: str) -> Optional[int]:
    if len(y_str) == 4:
        y = int(y_str)
        if 1000 <= y <= 2999:
            return y
        return None
    if len(y_str) == 2:
        y2 = int(y_str)
        if 0 <= y2 <= 30:
            return 2000 + y2
        if 31 <= y2 <= 99:
            return 1900 + y2
    return None


def _valid_month_day(month: int, day: int) -> bool:
    return 1 <= month <= 12 and 1 <= day <= 31


@dataclass
class DateMatch:
    """One validated date occurrence in the basename."""

    start: int
    end: int
    iso: str  # YYYY-MM-DD
    raw: str  # original substring e.g. 01.02.20


def find_valid_dates_in_basename(basename_no_ext: str) -> List[DateMatch]:
    """Find all non-overlapping valid M.D.Y date tokens in order."""
    matches: List[DateMatch] = []
    last_end = -1
    for m in _DATE_PATTERN.finditer(basename_no_ext):
        if m.start() < last_end:
            continue
        month = int(m.group("m"))
        day = int(m.group("d"))
        if not _valid_month_day(month, day):
            continue
        y = _parse_year(m.group("y"))
        if y is None:
            continue
        try:
            # Light sanity: datetime accepts impossible Feb 31 in older Python? Actually raises.
            datetime(y, month, day)
        except ValueError:
            continue
        raw = m.group(0)
        iso = f"{y:04d}-{month:02d}-{day:02d}"
        matches.append(DateMatch(start=m.start(), end=m.end(), iso=iso, raw=raw))
        last_end = m.end()
    return matches


def _has_extra_confusing_numbers(basename_no_ext: str, used_spans: List[Tuple[int, int]]) -> bool:
    """
    True if there are dotted digit patterns outside the detected date span(s)
    (heuristic for MEDIUM — e.g. semantic versions next to a real date).
    """

    def inside_used_span(start: int, end: int) -> bool:
        return any(start >= a and end <= b for a, b in used_spans)

    for m in _EXTRA_DOT_NUMBER_HINT.finditer(basename_no_ext):
        if inside_used_span(m.start(), m.end()):
            continue
        return True
    return False


def _failed_date_like_tokens(basename_no_ext: str) -> int:
    """Count regex matches that look like M.D.Y but fail month/day/year rules (partial/ambiguous)."""
    n = 0
    for m in _DATE_PATTERN.finditer(basename_no_ext):
        month = int(m.group("m"))
        day = int(m.group("d"))
        y = _parse_year(m.group("y"))
        ok = _valid_month_day(month, day) and y is not None
        if ok:
            try:
                datetime(y, month, day)
            except ValueError:
                ok = False
        if not ok:
            n += 1
    return n


def classify_confidence(
    basename_no_ext: str, dates: List[DateMatch]
) -> Tuple[str, Optional[DateMatch]]:
    """
    Returns (confidence, primary_date_or_none).
    primary_date: first date used for proposal / removal when any exist.
    """
    failed_tokens = _failed_date_like_tokens(basename_no_ext)

    if not dates:
        if failed_tokens:
            return "LOW", None
        return "NO DATE", None
    if len(dates) > 1:
        return "MEDIUM", dates[0]

    span = (dates[0].start, dates[0].end)
    confusing = _has_extra_confusing_numbers(basename_no_ext, [span])
    if confusing:
        return "MEDIUM", dates[0]
    if failed_tokens > 0:
        # Valid date present, but other date-shaped junk in the name → ambiguous
        return "LOW", dates[0]

    # Exactly one valid date, no extra confusing dotted groups → HIGH
    return "HIGH", dates[0]


def clean_stem_after_date_removal(stem: str, date_to_remove: Optional[DateMatch]) -> str:
    """Remove date token, replace dots/underscores with spaces, collapse spaces, trim spaces/dashes."""
    s = stem
    if date_to_remove:
        s = s[: date_to_remove.start] + " " + s[date_to_remove.end :]
    s = s.replace(".", " ").replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip(" \t-")
    return s


def _normalize_stem_like_clean(fragment: str) -> str:
    """Same space/dot rules as clean_stem_after_date_removal (without date removal)."""
    s = fragment.replace(".", " ").replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip(" \t-")
    return s


def _split_post_date_version(post: str) -> Optional[Tuple[str, str]]:
    """
    If ``post`` is the basename substring immediately after the date token, detect
    ``<single-letter version><name>`` and return (name_after, letter). Else None.
    """
    post = post.lstrip(" \t._-")
    if len(post) < 2:
        return None

    m = re.match(r"^([A-Z])\s+(.+)$", post)
    if m:
        return m.group(2), m.group(1)

    m = re.match(r"^([A-Z])[-_](.+)$", post)
    if m:
        return m.group(2), m.group(1)

    m = re.match(r"^([A-Z])([A-Z][a-z].*)$", post)
    if m:
        return m.group(2), m.group(1)

    return None


def rebuild_stem_when_version_follows_date(
    base: str, primary: DateMatch, cleaned_fallback: str
) -> str:
    """
    Original layout often ``… <date> <A|B|…> <title>`` (or glued ``…<date>A<title>``).
    Rebuild the descriptive stem as ``<prefix + title normalized> - <letter>`` so
    the version is not read as part of the title. Falls back to *cleaned_fallback*
    when the after-date segment does not match a revision pattern.
    """
    split = _split_post_date_version(base[primary.end :])
    if split is None:
        return cleaned_fallback

    name_after, letter = split
    pre = base[: primary.start].rstrip(" \t._-")
    inner = f"{pre} {name_after}".strip() if pre else name_after
    inner_norm = _normalize_stem_like_clean(inner)
    if not inner_norm:
        return f"{letter}" if letter else cleaned_fallback
    return f"{inner_norm} - {letter}"


def separate_name_from_trailing_version(stem: str) -> str:
    """
    Put a visible separator before a trailing single-letter version marker so it
    does not read as part of the word (e.g. ContractA -> Contract - A).

    Handles:
    - Glued: lowercase/digit + one trailing A-Z (ReportMar2024A -> ...A split only at end).
    - Single space before one trailing A-Z (Report A -> Report - A).

    Skips when the marker is already next to - or ., or when the name already
    ends with ' - <letter>'.
    """
    s = stem.strip()
    if len(s) < 2:
        return stem

    if re.search(r" - [A-Z]$", s):
        return stem

    # Glued revision letter after lowercase or digit (e.g. ContractA, file2B)
    s = re.sub(r"([a-z0-9])([A-Z])$", r"\1 - \2", s)

    # One or more spaces before a single trailing letter (e.g. Report A); do not
    # touch if the character before the spaces is already . or -
    s = re.sub(r"([^\s.\-])(\s+)([A-Z])$", r"\1 - \3", s)

    return s


def build_proposed_filename(original_filename: str) -> dict:
    """
    Analyze a single filename (not full path).
    Returns keys: extracted_date (str or ""), proposed (str), confidence (str),
    needs_review (bool), parts for logging.
    """
    if not original_filename or original_filename in (".", ".."):
        return {
            "extracted_date": "",
            "proposed": original_filename,
            "confidence": "NO DATE",
            "needs_review": True,
        }

    base, ext = os.path.splitext(original_filename)
    dates = find_valid_dates_in_basename(base)
    confidence, primary = classify_confidence(base, dates)

    cleaned = clean_stem_after_date_removal(base, primary)
    if primary:
        cleaned = rebuild_stem_when_version_follows_date(base, primary, cleaned)
    cleaned = separate_name_from_trailing_version(cleaned)
    # Preserve extension as-is (including case)
    if primary:
        proposed = f"{primary.iso} - {cleaned}{ext}" if cleaned else f"{primary.iso}{ext}"
    else:
        proposed = f"{cleaned}{ext}" if cleaned else original_filename

    needs_review = confidence != "HIGH"

    return {
        "extracted_date": primary.iso if primary else "",
        "proposed": proposed,
        "confidence": confidence,
        "needs_review": needs_review,
    }


def batch_slice(files: List[str], start: int, size: int) -> List[str]:
    end = start + max(0, size)
    if start < 0:
        start = 0
    return files[start:end]


def proposed_path_conflicts(original_full_path: str, proposed_filename: str) -> bool:
    """True if a different file already exists at the target path."""
    directory = os.path.dirname(original_full_path)
    target = os.path.join(directory, proposed_filename)
    if not os.path.isfile(original_full_path):
        return False
    if not os.path.exists(target):
        return False
    return not os.path.samefile(target, original_full_path)


def analyze_row(full_path: str) -> dict:
    """One file row for UI / sheet export."""
    fn = os.path.basename(full_path)
    info = build_proposed_filename(fn)
    conflict = proposed_path_conflicts(full_path, info["proposed"])
    approve_default = "YES" if info["confidence"] == "HIGH" else "NO"
    needs_review_cell = "YES" if info["needs_review"] else "NO"
    conflict_cell = "YES" if conflict else "NO"
    batch_id = ""  # filled by caller with batch id string
    return {
        "full_path": full_path,
        "original_filename": fn,
        "extracted_date": info["extracted_date"],
        "proposed": info["proposed"],
        "confidence": info["confidence"],
        "needs_review": needs_review_cell,
        "conflict": conflict_cell,
        "approve_default": approve_default,
        "batch_id": batch_id,
    }
