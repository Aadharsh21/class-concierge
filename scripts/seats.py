#!/usr/bin/env python3
"""Seat Snipe: watch full Arizona State University classes and report when a seat opens.

Reads ASU's public class search: no sign-in, no key, standard library only.

  python3 scripts/seats.py search --term "spring 2027" --course "CSE 340"
  python3 scripts/seats.py search --term 2271 --class 18607 --links
  python3 scripts/seats.py watch add --term 2271 --course "CSE 340" --class 21671
  python3 scripts/seats.py watch list
  python3 scripts/seats.py watch remove --class 21671        (or --all)
  python3 scripts/seats.py check                              (the scheduled check)

Add --sample to any command to use the saved copy in assets/ instead of the network.
Sample runs keep their own watch list, apart from the student's.

Exit codes: 0 done, 2 couldn't read the input, 3 not found, 4 ASU class search not
reachable, 5 ASU answered in an unexpected way.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

API_URL = "https://eadvs-cscc-catalog-api.apps.asu.edu/catalog-microservices/api/v1/search/classes"
VIEW_URL = "https://catalog.apps.asu.edu/catalog/classes/classlist?keywords={nbr}&searchType=all&term={term}"
ADD_URL = "https://go.oasis.asu.edu/addclass/?STRM={term}&ASU_CLASS_NBR={nbr}"
USER_AGENT = "Mozilla/5.0 (compatible; seat-snipe/1.0; +https://github.com/Aadharsh21/seat-snipe)"
SKILL_DIR = Path(__file__).resolve().parent.parent
SAMPLE_FILE = SKILL_DIR / "assets" / "sample_cse340_spring2027.json"

WARN_AFTER = 4          # failed checks in a row before the student hears about it (about an hour)
WARN_AGAIN_AFTER = 96   # about a day of failed checks
MISSING_LIMIT = 4       # checks in a row a watched class can be missing before watching stops
DEFAULT_LIMIT = 12      # sections a search prints

EXIT_INPUT, EXIT_NOT_FOUND, EXIT_UNREACHABLE, EXIT_UNEXPECTED = 2, 3, 4, 5

SEASONS = {"spring": "1", "sp": "1", "summer": "4", "sum": "4", "su": "4", "fall": "7", "fa": "7", "autumn": "7"}
SEASON_NAMES = {"1": "Spring", "4": "Summer", "7": "Fall"}
SEASON_START_MONTH = {"1": 1, "4": 5, "7": 8}
CAMPUSES = {"TEMPE": "Tempe", "POLY": "Polytechnic", "WEST": "West Valley", "DTPHX": "Downtown Phoenix",
            "ASUONLINE": "ASU Online", "ICOURSE": "iCourse", "CALHC": "Lake Havasu", "AWC": "Yuma"}
MODES = {"P": "in person", "HY": "hybrid", "OL": "online", "IC": "iCourse"}
NOT_ROOMS = {"INTRT", "TBA", "ONLINE", "ICOURSE", "ASUONLINE", "ARR"}  # placeholders, not real rooms
DAYS = [("MON", "M"), ("TUES", "T"), ("WED", "W"), ("THURS", "Th"), ("FRI", "F"), ("SAT", "Sa"), ("SUN", "Su")]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class Stop(Exception):
    """A problem to report in one line, with the exit code SKILL.md maps to a reply."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


# ---------- reading what the student said ----------

def term_code(year: int, season: str) -> str:
    return f"2{year % 100:02d}{season}"


def term_label(code: str) -> str:
    return f"{SEASON_NAMES.get(code[3:4], '?')} {2000 + int(code[1:3])}"


def next_term(today: date) -> str:
    """The term students are registering for around this date."""
    if today.month == 1 and today.day <= 20:
        return term_code(today.year, "1")
    if (today.month, today.day) >= (8, 21):
        return term_code(today.year + 1, "1")
    return term_code(today.year, "7")


def parse_term(text: str, today: date) -> str:
    t = (text or "").strip().lower().replace("-", " ").replace("'", " ")
    t = re.sub(r"\s+", " ", t)
    if re.fullmatch(r"2\d\d[147]", t):
        return t
    if t in ("", "next", "upcoming", "next term", "next semester"):
        return next_term(today)
    m = re.fullmatch(r"(spring|summer|sum|fall|autumn|sp|su|fa) ?(\d{2}|\d{4})?", t)
    if not m:
        raise Stop(EXIT_INPUT, f"Couldn't read the term {text!r}. Use something like \"spring 2027\" or the code 2271.")
    season = SEASONS[m.group(1)]
    if m.group(2):
        year = int(m.group(2))
        year = year + 2000 if year < 100 else year
    else:
        year = today.year if today.month <= SEASON_START_MONTH[season] else today.year + 1
    return term_code(year, season)


def parse_course(text: str) -> tuple[str, str]:
    m = re.fullmatch(r"\s*([A-Za-z]{2,4})\s*-?\s*(\d{3}[A-Za-z]?)\s*", text or "")
    if not m:
        raise Stop(EXIT_INPUT, f"Couldn't read the course {text!r}. Use the subject and number, like \"CSE 340\".")
    return m.group(1).upper(), m.group(2).upper()


def parse_class_nbr(text: str) -> str:
    t = (text or "").strip().lstrip("#")
    if not re.fullmatch(r"\d{4,6}", t):
        raise Stop(EXIT_INPUT, f"Couldn't read the class number {text!r}. It's the 5-digit number, like 18607.")
    return t


def parse_today(text: str | None) -> date:
    if not text:
        return date.today()
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise Stop(EXIT_INPUT, f"--today should look like 2026-10-24, not {text!r}.") from None


# ---------- talking to ASU class search ----------

def request_json(url: str):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer null", "Accept": "application/json",
                                               "User-Agent": USER_AGENT})
    problem = "no answer"
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read()
            break
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise Stop(EXIT_UNEXPECTED, f"ASU's class search now refuses anonymous lookups (HTTP {exc.code}); "
                                            "the seat checker needs an update.") from None
            if exc.code != 429 and exc.code < 500:
                raise Stop(EXIT_UNEXPECTED, f"ASU's class search answered HTTP {exc.code}; "
                                            "the seat checker needs an update.") from None
            problem = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            problem = str(getattr(exc, "reason", exc))
        if attempt == 0:
            time.sleep(3)
    else:
        raise Stop(EXIT_UNREACHABLE, f"ASU's class search isn't answering right now ({problem}).")
    try:
        return json.loads(body)
    except ValueError:
        raise Stop(EXIT_UNEXPECTED, "ASU's class search sent something other than a class list; "
                                    "the seat checker needs an update.") from None


def fetch(term: str, subject: str = "", catalog: str = "", keywords: str = "", sample: Path | None = None) -> dict:
    """Sections for one class search query, as {"sections": [...], "total": n}."""
    if sample is not None:
        try:
            data = json.loads(Path(sample).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise Stop(EXIT_UNEXPECTED, f"The sample file {sample} couldn't be read ({exc}).") from None
    else:
        params = {"refine": "Y", "campusOrOnlineSelection": "A", "honors": "F", "promod": "F",
                  "searchType": "all", "term": term}
        if subject:
            params["subject"] = subject
        if catalog:
            params["catalogNbr"] = catalog
        if keywords:
            params["keywords"] = keywords
        data = request_json(API_URL + "?" + urllib.parse.urlencode(params))

    classes = data.get("classes") if isinstance(data, dict) else None
    if not isinstance(classes, list):
        raise Stop(EXIT_UNEXPECTED, "ASU's class search answered without a class list; the seat checker needs an update.")
    sections = []
    for raw in classes:
        s = parse_section(raw)
        if s is None:
            continue
        if sample is not None:  # ASU filters on its side; do the same for the saved copy
            if s["term"] != term or (subject and s["subject"] != subject) \
                    or (catalog and s["catalog"] != catalog) or (keywords and s["nbr"] != keywords):
                continue
        sections.append(s)
    total = data.get("total")
    total = total.get("value") if isinstance(total, dict) else total
    if sample is not None:
        total = len(sections)
    return {"sections": sections, "total": to_int(total, len(sections))}


def to_int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_section(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    clas = raw.get("CLAS") or {}
    nbr = str(clas.get("CLASSNBR") or "").strip()
    if not nbr:
        return None
    seat = raw.get("seatInfo") or {}
    cap = to_int(seat.get("ENRL_CAP"), to_int(clas.get("ENRLCAP")))
    taken = to_int(seat.get("ENRL_TOT"), to_int(clas.get("ENRLTOT")))
    reserved = sum(max(to_int(r.get("ENRL_CAP")) - to_int(r.get("ENRL_TOT")), 0)
                   for r in raw.get("reservedSeatsInfo") or [] if isinstance(r, dict))
    open_total = max(cap - taken, 0)
    open_reserved = min(reserved, open_total)
    open_any = open_total - open_reserved
    if str(clas.get("CLASSSTAT") or "A").upper() != "A":
        status = "CANCELLED"
    elif open_any > 0:
        status = "OPEN"
    elif open_total > 0:
        status = "RESERVED"
    else:
        status = "FULL"
    instructors = clas.get("INSTRUCTORSLIST")
    if isinstance(instructors, str):
        instructors = [instructors]
    instructors = [str(i).strip() for i in instructors or [] if str(i).strip()]
    location = str(clas.get("LOCATION") or "").strip().upper()
    mode = str(clas.get("INSTRUCTIONMODE") or "").strip().upper()
    return {
        "nbr": nbr,
        "term": str(clas.get("STRM") or "").strip(),
        "subject": str(clas.get("SUBJECT") or "").strip().upper(),
        "catalog": str(clas.get("CATALOGNBR") or "").strip().upper(),
        "section": str(clas.get("CLASSSECTION") or "").strip(),
        "title": str(clas.get("COURSETITLELONG") or clas.get("TITLE") or "").strip(),
        "session": str(clas.get("SESSIONCODE") or "").strip().upper(),
        "start_date": str(clas.get("STARTDATE") or "")[:10],
        "campus": CAMPUSES.get(location, location.title()),
        "mode": MODES.get(mode, mode.lower()),
        "days": "".join(short for key, short in DAYS if clas.get(key) == "Y"),
        "start": str(clas.get("STARTTIME") or "").strip(),
        "end": str(clas.get("ENDTIME") or "").strip(),
        "room": "" if str(clas.get("FACILITYID") or "").strip().upper() in NOT_ROOMS
        else str(clas.get("FACILITYID") or "").strip(),
        "instructors": instructors or ["Staff"],
        "cap": cap,
        "taken": taken,
        "open": open_any,
        "open_reserved": open_reserved,
        "status": status,
        "deadline": str(clas.get("ENROLLDEADLINE") or "")[:10],
    }


# ---------- wording ----------

def pretty_date(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{MONTHS[d.month - 1]} {d.day}"


def is_online(s: dict) -> bool:
    return s["campus"] == "ASU Online" or s["mode"] in ("online", "iCourse")


def when_where(s: dict) -> str:
    parts = []
    if s["days"] and s["start"]:
        parts.append(f"{s['days']} {s['start']}-{s['end']}")
    if is_online(s):
        parts.append("online" if s["days"] else "online, no set meeting time")
    else:
        place = s["campus"] + (f" {s['room']}" if s["room"] else "")
        if s["mode"] and s["mode"] != "in person":
            place += f" ({s['mode']})"
        parts.append(place)
    if s["session"] and s["session"] != "C" and s["start_date"]:
        parts.append(f"session {s['session']} from {pretty_date(s['start_date'])}")
    return ", ".join(parts)


def seats_text(s: dict) -> str:
    if s["status"] == "CANCELLED":
        return "cancelled"
    if s["status"] == "FULL":
        return f"FULL ({s['taken']}/{s['cap']})"
    if s["status"] == "RESERVED":
        return f"0 open to everyone ({s['open_reserved']} reserved seats open)"
    text = f"{s['open']} open of {s['cap']}"
    if s["open_reserved"]:
        text += f", plus {s['open_reserved']} reserved"
    return text


def describe(s: dict) -> str:
    who = ", ".join(s["instructors"][:2])
    return f"#{s['nbr']} {s['subject']} {s['catalog']}-{s['section']} | {when_where(s)} | {who} | {seats_text(s)}"


def short_label(s: dict) -> str:
    if s["days"] and s["start"]:
        when = f"{s['days']} {s['start']}"
    elif is_online(s):
        when = "online"
    else:
        when = f"{s['campus']} {s['mode']}".strip()
    return f"#{s['nbr']} {s['subject']} {s['catalog']}-{s['section']} ({s['instructors'][0]}, {when})"


def link_lines(term: str, nbr: str) -> list[str]:
    return [f"  add: {ADD_URL.format(term=term, nbr=nbr)}", f"  view: {VIEW_URL.format(term=term, nbr=nbr)}"]


def minutes(clock: str) -> int:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*([AP]M)", clock or "", re.I)
    if not m:
        return 24 * 60
    hour = int(m.group(1)) % 12 + (12 if m.group(3).upper() == "PM" else 0)
    return hour * 60 + int(m.group(2))


# ---------- the watch list (kept under HOME, so it survives restarts and Skill updates) ----------

def state_file(sample: bool) -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "seat-snipe"
    return base / ("sample-watches.json" if sample else "watches.json")


def load_state(sample: bool) -> dict:
    path = state_file(sample)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("not an object")
    except FileNotFoundError:
        state = {}
    except (OSError, ValueError):
        try:
            path.replace(path.with_name(path.stem + ".broken.json"))
        except OSError:
            pass
        print("NOTE The saved watch list couldn't be read, so it was reset. Ask the student what to watch again.")
        state = {}
    state.setdefault("version", 1)
    state.setdefault("watches", [])
    state.setdefault("fail_streak", 0)
    return state


def save_state(state: dict, sample: bool) -> None:
    path = state_file(sample)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# ---------- commands ----------

def cmd_search(args, today: date) -> int:
    term = parse_term(args.term, today)
    if args.course:
        subject, catalog = parse_course(args.course)
        sections = [s for s in fetch(term, subject=subject, catalog=catalog, sample=args.sample_path)["sections"]
                    if s["subject"] == subject and s["catalog"] == catalog]
        what = f"{subject} {catalog}"
    elif args.cls:
        what = None
        sections = []
    else:
        raise Stop(EXIT_INPUT, "Give a course like --course \"CSE 340\" or a class number like --class 18607.")
    if args.cls:
        nbr = parse_class_nbr(args.cls)
        if not args.course:
            sections = fetch(term, keywords=nbr, sample=args.sample_path)["sections"]
            what = f"class #{nbr}"
        sections = [s for s in sections if s["nbr"] == nbr]
    if not sections:
        raise Stop(EXIT_NOT_FOUND, f"{what} isn't on ASU's class search for {term_label(term)} (term {term}).")

    shown = sections
    if args.campus and args.campus.lower() not in ("any", "all"):
        want = args.campus.lower()
        shown = [s for s in shown if (is_online(s) if want in ("online", "asu online", "icourse")
                                      else want in s["campus"].lower())]
    if args.instructor:
        who = args.instructor.lower()
        shown = [s for s in shown if any(who in name.lower() for name in s["instructors"])]
    if not shown:
        raise Stop(EXIT_NOT_FOUND, f"None of the {len(sections)} sections of {what} match those filters "
                                   f"for {term_label(term)}.")
    shown.sort(key=lambda s: (is_online(s), s["campus"], minutes(s["start"]), s["section"]))
    full = sum(1 for s in shown if s["status"] == "FULL")
    first = shown[0]
    print(f"{first['subject']} {first['catalog']} {first['title']}, {term_label(term)} (term {term}): "
          f"{len(shown)} section{'s' if len(shown) != 1 else ''}, {full} full")
    for s in shown[:args.limit]:
        print(describe(s))
        if args.links:
            print("\n".join(link_lines(term, s["nbr"])))
    if len(shown) > args.limit:
        print(f"(+{len(shown) - args.limit} more; narrow it with --campus or --instructor)")
    return 0


def cmd_watch_add(args, today: date) -> int:
    term = parse_term(args.term, today)
    nbrs = list(dict.fromkeys(parse_class_nbr(c) for c in args.cls))
    found: dict[str, dict] = {}
    if args.course:
        subject, catalog = parse_course(args.course)
        for s in fetch(term, subject=subject, catalog=catalog, sample=args.sample_path)["sections"]:
            if s["nbr"] in nbrs:
                found[s["nbr"]] = s
    for nbr in nbrs:
        if nbr not in found:
            for s in fetch(term, keywords=nbr, sample=args.sample_path)["sections"]:
                if s["nbr"] == nbr:
                    found[nbr] = s
    missing = [n for n in nbrs if n not in found]
    if not found:
        raise Stop(EXIT_NOT_FOUND, f"Couldn't find class #{', #'.join(missing)} for {term_label(term)}. "
                                   "Check the number, or include the course, like --course \"CSE 340\".")

    state = load_state(args.is_sample)
    added = 0
    for nbr in nbrs:
        s = found.get(nbr)
        if s is None:
            print(f"NOT-FOUND #{nbr} isn't on ASU's class search for {term_label(term)}; not watching it.")
            continue
        label = short_label(s)
        if s["status"] == "CANCELLED":
            print(f"ENDED {label} is cancelled; not watching it.")
            continue
        if s["deadline"] and today.isoformat() > s["deadline"]:
            print(f"ENDED {label}: the last day to add it ({pretty_date(s['deadline'])}) has passed; not watching it.")
            continue
        entry = {"term": term, "nbr": nbr, "subject": s["subject"], "catalog": s["catalog"], "label": label,
                 "deadline": s["deadline"], "last_status": s["status"], "missing": 0,
                 "last_checked": datetime.now().isoformat(timespec="minutes")}
        existing = next((w for w in state["watches"] if w["term"] == term and w["nbr"] == nbr), None)
        if existing:
            existing.update(entry)
        else:
            entry["added"] = today.isoformat()
            state["watches"].append(entry)
        added += 1
        if s["status"] == "OPEN":
            print(f"ALREADY-OPEN {label} has {s['open']} open seat{'s' if s['open'] != 1 else ''} right now")
            print("\n".join(link_lines(term, nbr)))
        else:
            print(f"WATCHING {label}, now {seats_text(s)}")
    save_state(state, args.is_sample)
    count = len(state["watches"])
    print(f"Watching {count} class{'es' if count != 1 else ''} in total.")
    return 0 if added else EXIT_NOT_FOUND


def cmd_watch_list(args, today: date) -> int:
    state = load_state(args.is_sample)
    if not state["watches"]:
        print("NO-WATCHES Not watching any classes.")
        return 0
    count = len(state["watches"])
    print(f"Watching {count} class{'es' if count != 1 else ''}:")
    for w in state["watches"]:
        print(f"{w['label']}, {term_label(w['term'])}: last seen {w.get('last_status', '?')}, "
              f"since {w.get('added', '?')}")
    return 0


def cmd_watch_remove(args, today: date) -> int:
    state = load_state(args.is_sample)
    if args.all:
        removed, state["watches"] = state["watches"], []
        not_watched: set[str] = set()
    else:
        if not args.cls:
            raise Stop(EXIT_INPUT, "Say which class to stop watching (--class 18607) or use --all.")
        nbrs = {parse_class_nbr(c) for c in args.cls}
        removed = [w for w in state["watches"] if w["nbr"] in nbrs]
        state["watches"] = [w for w in state["watches"] if w["nbr"] not in nbrs]
        not_watched = nbrs - {w["nbr"] for w in removed}
    save_state(state, args.is_sample)
    for w in removed:
        print(f"REMOVED {w['label']}")
    for nbr in sorted(not_watched):
        print(f"NOT-WATCHED #{nbr} wasn't on the watch list.")
    if not state["watches"]:
        print("NO-WATCHES Not watching any classes now.")
    return 0


def cmd_check(args, today: date) -> int:
    state = load_state(args.is_sample)
    watches = state["watches"]
    if not watches:
        print("NO-WATCHES Nothing to watch, so the Seat Snipe check-in can be deleted.")
        return 0

    found: dict[tuple[str, str], dict | None] = {}
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for w in watches:
        groups.setdefault((w["term"], w["subject"], w["catalog"]), []).append(w)
    try:
        for (term, subject, catalog), group in groups.items():
            result = fetch(term, subject=subject, catalog=catalog, sample=args.sample_path)
            by_nbr = {s["nbr"]: s for s in result["sections"]}
            for w in group:
                s = by_nbr.get(w["nbr"])
                if s is None and result["total"] > len(result["sections"]):
                    # A big course can spill past the first page of results; look this class up directly.
                    s = next((x for x in fetch(term, keywords=w["nbr"], sample=args.sample_path)["sections"]
                              if x["nbr"] == w["nbr"]), None)
                found[(w["term"], w["nbr"])] = s
    except Stop as exc:
        if exc.code not in (EXIT_UNREACHABLE, EXIT_UNEXPECTED):
            raise
        state["fail_streak"] = state.get("fail_streak", 0) + 1
        save_state(state, args.is_sample)
        streak = state["fail_streak"]
        if streak in (WARN_AFTER, WARN_AGAIN_AFTER):
            print(f"WARN {exc} The last {streak} checks failed; still trying every 15 minutes.")
        else:
            print(f"FAILED {exc} ({streak} failed in a row; stay quiet)")
        return exc.code

    state["fail_streak"] = 0
    now = datetime.now().isoformat(timespec="minutes")
    new_open = 0
    keep = []
    for w in watches:
        label = w["label"]
        if w.get("deadline") and today.isoformat() > w["deadline"]:
            print(f"ENDED {label}: the last day to add it ({pretty_date(w['deadline'])}) has passed, "
                  "so watching stopped.")
            continue
        s = found.get((w["term"], w["nbr"]))
        if s is None:
            w["missing"] = w.get("missing", 0) + 1
            if w["missing"] >= MISSING_LIMIT:
                print(f"ENDED {label} is no longer on ASU's class search, so watching stopped.")
                continue
            print(f"MISSING {label} wasn't in this check's results (stay quiet)")
            keep.append(w)
            continue
        w["missing"] = 0
        label = w["label"] = short_label(s)
        previous, current = w.get("last_status"), s["status"]
        if current == "CANCELLED":
            print(f"ENDED {label} was cancelled by ASU, so watching stopped.")
            continue
        if current == "OPEN" and previous != "OPEN":
            new_open += 1
            print(f"NEW-OPEN {label} has {s['open']} open seat{'s' if s['open'] != 1 else ''} of {s['cap']}")
            print("\n".join(link_lines(w["term"], w["nbr"])))
        elif current == "OPEN":
            print(f"STILL-OPEN {label}, {seats_text(s)} (the student already knows)")
        elif current == "RESERVED" and previous == "FULL":
            print(f"NEW-RESERVED {label} has {s['open_reserved']} seats open, but all are reserved "
                  "for specific groups of students")
            print(link_lines(w["term"], w["nbr"])[1])
        else:
            print(f"{current} {label}, {seats_text(s)}")
        w["last_status"] = current
        w["last_checked"] = now
        keep.append(w)
    state["watches"] = keep
    save_state(state, args.is_sample)
    if not keep:
        print("NO-WATCHES Nothing left to watch, so the Seat Snipe check-in can be deleted.")
    print(f"SUMMARY checked {len(watches)} watched class{'es' if len(watches) != 1 else ''}: "
          f"{new_open} new opening{'s' if new_open != 1 else ''}.")
    return 0


# ---------- command line ----------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--sample", action="store_true",
                        help="use the saved copy in assets/ instead of the network, with its own watch list")
    common.add_argument("--sample-file", help=argparse.SUPPRESS)
    common.add_argument("--today", help="the student's local date as YYYY-MM-DD (default: today)")

    parser = argparse.ArgumentParser(description="Watch full ASU classes and report when a seat opens.")
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", parents=[common], help="list the sections of a course and their seats")
    search.add_argument("--term", default="next", help='"spring 2027", "fall", "next" or a code like 2271')
    search.add_argument("--course", help='subject and number, like "CSE 340"')
    search.add_argument("--class", dest="cls", help="a 5-digit class number, like 18607")
    search.add_argument("--campus", help="tempe, poly, west, downtown, online or any")
    search.add_argument("--instructor", help="part of the instructor's name")
    search.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="sections to print")
    search.add_argument("--links", action="store_true", help="print the add and view links for each section")
    search.set_defaults(func=cmd_search)

    watch = sub.add_parser("watch", help="add, list or remove watched classes")
    watch_sub = watch.add_subparsers(dest="action", required=True)
    add = watch_sub.add_parser("add", parents=[common], help="start watching classes")
    add.add_argument("--term", default="next")
    add.add_argument("--course", help='subject and number, like "CSE 340" (faster and more reliable)')
    add.add_argument("--class", dest="cls", action="append", required=True, help="class number; repeat for more")
    add.set_defaults(func=cmd_watch_add)
    lst = watch_sub.add_parser("list", parents=[common], help="show what is being watched")
    lst.set_defaults(func=cmd_watch_list)
    rm = watch_sub.add_parser("remove", parents=[common], help="stop watching classes")
    rm.add_argument("--class", dest="cls", action="append", help="class number; repeat for more")
    rm.add_argument("--all", action="store_true", help="stop watching everything")
    rm.set_defaults(func=cmd_watch_remove)

    check = sub.add_parser("check", parents=[common], help="check every watched class once (the scheduled check)")
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.sample or args.sample_file:
        args.sample_path = Path(args.sample_file) if args.sample_file else SAMPLE_FILE
    else:
        args.sample_path = None
    args.is_sample = args.sample_path is not None
    try:
        return args.func(args, parse_today(args.today)) or 0
    except Stop as exc:
        print(exc)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
