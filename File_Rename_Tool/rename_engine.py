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

# Square brackets whose content mentions "conflict" (SharePoint / sync noise).
_BRACKET_CONFLICT = re.compile(r"\[[^\]]*\bconflict\b[^\]]*\]", re.IGNORECASE)

# Parenthetical artifacts: conflict-related, bare "copy", or small numeric recovery markers.
_PAREN_CONFLICT_OR_COPY = re.compile(
    r"\(\s*(?:[^)]*\bconflict\b[^)]*|conflict\s+copy|copy)\s*\)",
    re.IGNORECASE,
)
# "(1)" / "(2)" style recovery suffixes only (avoid stripping "(12)" in legitimate titles).
_PAREN_SMALL_NUMBER = re.compile(
    r"(?:^|[\s._-])\(\s*[12]\s*\)(?=[\s._-]|$)",
    re.IGNORECASE,
)

# No$$ flag (case-insensitive). Match 2+ $ so sync noise like "NO$$$" is fully removed; display stays "No$$".
# (?!\w) avoids matching "no$$" inside a longer token.
_FLAG_NO_MONEY = re.compile(r"(?i)\bno\$\$+(?!\w)")

# NO # flag (case-insensitive). \b after # fails at end-of-string; use (?!\w) like No$$.
_FLAG_NO_POUND = re.compile(r"(?i)\bno\s*#(?!\w)")

# Apple / Finder noise: "-Alisha's MacBook Pro" after a date (require Name's before MacBook Pro).
_MACBOOK_SUFFIX = re.compile(
    r"(?i)[\-–—]\s*[A-Za-z0-9]+['\u2019]s\s+MacBook\s+Pro\s*$"
)

# Trailing version letter: one A–Z immediately before extension, preceded by sep or dot chain after date.
_TRAILING_VERSION = re.compile(r"([\s._-])([A-Z])$")


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


@dataclass
class MonthYearMatch:
    """Month + year only (no day), e.g. 03.2013 or 9.20 → prefix YYYY-MM."""

    start: int
    end: int
    raw: str
    yyyymm: str  # YYYY-MM


# Month.year or month.yy — not followed by another dot+digits (avoids stealing from M.D.Y).
# Must follow start or a separator so we don't match inside "v1.2020".
_MONTH_YEAR_PATTERN = re.compile(
    r"(?i)(?:^|[\s._-])(?P<mo>\d{1,2})\.(?P<yr>\d{2}|\d{4})(?![0-9.])"
)
# Glued after letters: "Report03.2013" — only 01–12 with leading zero (avoids "v1.2020").
_MONTH_YEAR_GLUED = re.compile(
    r"(?<=[A-Za-z])(?P<mo>0[1-9]|1[0-2])\.(?P<yr>\d{4})(?![0-9.])", re.IGNORECASE
)


def remove_junk_from_basename(basename_no_ext: str) -> str:
    """
    Remove sync/recovery noise before date/version/flag parsing.
    - Bracket segments containing 'conflict'
    - Parenthetical conflict/copy phrases or small isolated (1)/(2) markers
    - Bare phrases like 'conflict copy' / 'recovery copy'
    - Finder-style device suffix: "-Someone's MacBook Pro" (not general "MacBook Pro" titles)
    """
    s = basename_no_ext
    s = _BRACKET_CONFLICT.sub(" ", s)
    # Repeat paren removals until stable (overlapping unlikely)
    for _ in range(8):
        nxt = _PAREN_CONFLICT_OR_COPY.sub(" ", s)
        nxt = _PAREN_SMALL_NUMBER.sub(" ", nxt)
        if nxt == s:
            break
        s = nxt
    s = re.sub(r"\bconflict\s+copy\b", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\brecovery\s+copy\b", " ", s, flags=re.IGNORECASE)
    s = _MACBOOK_SUFFIX.sub(" ", s)
    return s


def extract_no_money_flag(stem: str) -> Tuple[str, bool]:
    """Remove No$$ from stem; return (stem_without_flag, has_flag)."""
    has = _FLAG_NO_MONEY.search(stem) is not None
    cleaned = _FLAG_NO_MONEY.sub(" ", stem)
    return cleaned, has


def extract_no_pound_flag(stem: str) -> Tuple[str, bool]:
    """Remove NO # from stem; return (stem_without_flag, has_flag). Canonical display: NO #."""
    has = _FLAG_NO_POUND.search(stem) is not None
    cleaned = _FLAG_NO_POUND.sub(" ", stem)
    return cleaned, has


def extract_trailing_version_letter(stem: str) -> Tuple[str, Optional[str]]:
    """
    Single capital A–Z at end of stem, preceded by space/period/underscore/dash.
    Returns (stem_without_version, version_letter_or_None).
    """
    s = stem.rstrip()
    m = _TRAILING_VERSION.search(s)
    if not m:
        return s, None
    prefix = s[: m.start(1)]  # drop separator before version letter as well
    letter = m.group(2)
    # Require meaningful event prefix so we do not treat "A" alone as version+empty event
    if not prefix.strip():
        return s, None
    return prefix.rstrip(" \t._-"), letter


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
            datetime(y, month, day)
        except ValueError:
            continue
        raw = m.group(0)
        iso = f"{y:04d}-{month:02d}-{day:02d}"
        matches.append(DateMatch(start=m.start(), end=m.end(), iso=iso, raw=raw))
        last_end = m.end()
    return matches


def _my_spans_overlap(a: Tuple[int, int], b: Tuple[int, int]) -> bool:
    return not (a[1] <= b[0] or b[1] <= a[0])


def find_month_year_matches(basename_no_ext: str) -> List[MonthYearMatch]:
    """
    Month + year tokens (no day), same month/year rules as full dates.
    Separator-bound first, then letter-glued (Report03.2013) if it does not overlap.
    """
    matches: List[MonthYearMatch] = []
    last_end = -1
    for m in _MONTH_YEAR_PATTERN.finditer(basename_no_ext):
        mo_s = m.group("mo")
        yr_s = m.group("yr")
        month = int(mo_s)
        if not (1 <= month <= 12):
            continue
        y = _parse_year(yr_s)
        if y is None:
            continue
        start, end = m.start(), m.end()
        if start < last_end:
            continue
        raw = basename_no_ext[start:end]
        yyyymm = f"{y:04d}-{month:02d}"
        matches.append(MonthYearMatch(start=start, end=end, raw=raw, yyyymm=yyyymm))
        last_end = end

    for m in _MONTH_YEAR_GLUED.finditer(basename_no_ext):
        mo_s = m.group("mo")
        yr_s = m.group("yr")
        month = int(mo_s)
        y = _parse_year(yr_s)
        if y is None:
            continue
        start, end = m.start(), m.end()
        span = (start, end)
        if any(_my_spans_overlap(span, (x.start, x.end)) for x in matches):
            continue
        raw = basename_no_ext[start:end]
        matches.append(
            MonthYearMatch(start=start, end=end, raw=raw, yyyymm=f"{y:04d}-{month:02d}")
        )

    matches.sort(key=lambda x: x.start)
    return matches


def classify_month_year_confidence(
    matches: List[MonthYearMatch],
) -> Tuple[str, Optional[MonthYearMatch]]:
    if not matches:
        return "NO DATE", None
    if len(matches) > 1:
        return "MEDIUM", matches[0]
    return "HIGH", matches[0]


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
        return "LOW", dates[0]

    return "HIGH", dates[0]


def _collapse_separators_and_trim(s: str) -> str:
    """Collapse runs of spaces, dots, underscores, dashes; trim those chars from ends."""
    s = re.sub(r"[\s._-]+", " ", s)
    s = s.strip(" \t._-")
    return s


def _remove_primary_date_from_stem(stem: str, date_to_remove: Optional[DateMatch]) -> str:
    if not date_to_remove:
        return stem
    return stem[: date_to_remove.start] + " " + stem[date_to_remove.end :]


def _assemble_proposed_name(
    calendar_prefix: Optional[str],
    event_name: str,
    version: Optional[str],
    flag_tokens: List[str],
    ext: str,
) -> str:
    """
    Leading calendar token (YYYY-MM-DD or YYYY-MM) - Event - Version - Flag(s).ext
    flag_tokens: e.g. ["No$$"] and/or ["NO #"] in stable order (No$$ then NO #).
    Omit segments when absent; avoid double dashes.
    """
    parts: List[str] = []
    if calendar_prefix:
        parts.append(calendar_prefix)
    if event_name:
        parts.append(event_name)
    if version:
        parts.append(version)
    for tok in flag_tokens:
        if tok:
            parts.append(tok)

    if not parts:
        return ""

    core = " - ".join(parts)
    # Normalize accidental duplicate dashes from empty segments
    core = re.sub(r"(?:\s*-\s*){2,}", " - ", core)
    core = core.strip(" -")
    return f"{core}{ext}" if ext else core


def build_proposed_filename(original_filename: str) -> dict:
    """
    Analyze a single filename (not full path).
    Returns extracted_date, proposed, confidence, needs_review, version, flag,
    cleaned_event_name, etc.
    """
    empty = {
        "extracted_date": "",
        "proposed": original_filename,
        "confidence": "NO DATE",
        "needs_review": True,
        "version": "",
        "flag": "",
        "cleaned_event_name": "",
    }

    if not original_filename or original_filename in (".", ".."):
        return {**empty, "proposed": original_filename or ""}

    base, ext = os.path.splitext(original_filename)

    # 1) Junk removal first (brackets/parens per spec)
    stem = remove_junk_from_basename(base)

    # 2) Flags — NO # before No$$, both before version so "… D NO #" yields version D
    stem, has_no_pound = extract_no_pound_flag(stem)
    stem, has_no_money = extract_no_money_flag(stem)

    # 3) Version letter at end (not inside words — requires leading separator)
    stem, version_letter = extract_trailing_version_letter(stem)

    # 4) Full M.D.Y on stem first; if none, month+year only (prefix YYYY-MM)
    dates = find_valid_dates_in_basename(stem)
    confidence_full, primary = classify_confidence(stem, dates)

    my_matches: List[MonthYearMatch] = []
    my_primary: Optional[MonthYearMatch] = None
    if primary is None:
        my_matches = find_month_year_matches(stem)
        confidence, my_primary = classify_month_year_confidence(my_matches)
    else:
        confidence = confidence_full

    # 5) Event = stem with calendar token removed, then humanize separators
    if primary:
        after_calendar = _remove_primary_date_from_stem(stem, primary)
        extracted = primary.iso
    elif my_primary:
        after_calendar = stem
        for m in sorted(my_matches, key=lambda z: z.start, reverse=True):
            after_calendar = after_calendar[: m.start] + " " + after_calendar[m.end :]
        extracted = my_matches[0].yyyymm
    else:
        after_calendar = stem
        extracted = ""

    after_calendar = after_calendar.replace(".", " ").replace("_", " ")
    cleaned_event = _collapse_separators_and_trim(after_calendar)

    flag_tokens: List[str] = []
    if has_no_money:
        flag_tokens.append("No$$")
    if has_no_pound:
        flag_tokens.append("NO #")
    proposed = _assemble_proposed_name(
        extracted or None, cleaned_event, version_letter, flag_tokens, ext
    )

    # If everything stripped away, fall back to the original filename
    if not proposed.strip() or proposed == ext:
        proposed = original_filename

    needs_review = confidence != "HIGH"

    flag_display = "; ".join(flag_tokens)

    return {
        "extracted_date": extracted,
        "proposed": proposed,
        "confidence": confidence,
        "needs_review": needs_review,
        "version": version_letter or "",
        "flag": flag_display,
        "cleaned_event_name": cleaned_event,
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
        "version": info["version"],
        "flag": info["flag"],
        "cleaned_event_name": info["cleaned_event_name"],
        "proposed": info["proposed"],
        "confidence": info["confidence"],
        "needs_review": needs_review_cell,
        "conflict": conflict_cell,
        "approve_default": approve_default,
        "batch_id": batch_id,
    }
