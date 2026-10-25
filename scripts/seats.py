#!/usr/bin/env python3
"""Seat Snipe: watch full Arizona State University classes and report when a seat opens.

Reads ASU's public class search: no sign-in, no key, standard library only.

  python3 scripts/seats.py search --term "spring 2027" --course "CSE 340"
  python3 scripts/seats.py search --term 2271 --class 18607 --links
  python3 scripts/seats.py compare --term spring --course "CSE 340" --course "CSE 365" --campus tempe
  python3 scripts/seats.py watch add --term 2271 --course "CSE 340" --class 21671
  python3 scripts/seats.py watch list
  python3 scripts/seats.py watch remove --class 21671        (or --all)
  python3 scripts/seats.py alerts --set both                  (text, call or both)
  python3 scripts/seats.py check                              (the scheduled check)
  python3 scripts/seats.py drill                              (a test alert)

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
COURSE_URL = ("https://catalog.apps.asu.edu/catalog/classes/classlist?campusOrOnlineSelection=A"
              "&catalogNbr={catalog}&honors=F&promod=F&searchType=all&subject={subject}&term={term}")
ADD_URL = "https://go.oasis.asu.edu/addclass/?STRM={term}&ASU_CLASS_NBR={nbr}"
USER_AGENT = ("seat-snipe/1.2 (Cubbie skill; one student's watch list, checks every 15 min; "
              "+https://github.com/Aadharsh21/seat-snipe)")
SKILL_DIR = Path(__file__).resolve().parent.parent
SAMPLE_FILE = SKILL_DIR / "assets" / "sample_cse340_spring2027.json"

WARN_AFTER = 4          # failed checks in a row before the student hears about it (about an hour)
WARN_AGAIN_AFTER = 96   # about a day of failed checks
MISSING_LIMIT = 4       # checks in a row a watched class can be missing before watching stops
DEFAULT_LIMIT = 12      # sections a search prints

EXIT_INPUT, EXIT_NOT_FOUND, EXIT_UNREACHABLE, EXIT_UNEXPECTED, EXIT_BLOCKED = 2, 3, 4, 5, 6

SEASONS = {"spring": "1", "sp": "1", "summer": "4", "sum": "4", "su": "4", "fall": "7", "fa": "7", "autumn": "7"}
SEASON_NAMES = {"1": "Spring", "4": "Summer", "7": "Fall"}
SEASON_START_MONTH = {"1": 1, "4": 5, "7": 8}
CAMPUSES = {"TEMPE": "Tempe", "POLY": "Polytechnic", "WEST": "West Valley", "DTPHX": "Downtown Phoenix",
            "ASUONLINE": "ASU Online", "ICOURSE": "iCourse", "CALHC": "Lake Havasu", "AWC": "Yuma"}
MODES = {"P": "in person", "HY": "hybrid", "OL": "online", "IC": "iCourse"}
NOT_ROOMS = {"INTRT", "TBA", "ONLINE", "ICOURSE", "ASUONLINE", "ARR"}  # placeholders, not real rooms
KINDS = {"LEC": "Lecture", "LAB": "Lab", "LABORATORY": "Lab", "REC": "Recitation", "DIS": "Discussion",
         "SEM": "Seminar", "STU": "Studio", "ACT": "Activity", "CLN": "Clinical", "FLD": "Field work",
         "IND": "Independent study", "INT": "Internship", "PRA": "Practicum", "WKS": "Workshop"}
DAYS = [("MON", "M"), ("TUES", "T"), ("WED", "W"), ("THURS", "Th"), ("FRI", "F"), ("SAT", "Sa"), ("SUN", "Su")]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
ALERT_CHOICES = {
    "text": "a text with the add link",
    "call": "a phone call only",
    "both": "a phone call, then the add link by text",
}
ALERT_WORDS = {"text": "text", "texts": "text", "sms": "text", "message": "text", "messages": "text",
               "call": "call", "calls": "call", "phone": "call", "phone call": "call", "phone calls": "call",
               "both": "both", "all": "both", "call and text": "both", "text and call": "both"}


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
                server = (exc.headers.get("Server") or "").strip() if exc.headers else ""
                raise Stop(EXIT_BLOCKED, f"ASU's class search turned this computer away (HTTP {exc.code}"
                                         f"{', server ' + server if server else ''}).") from None
            if exc.code != 429 and exc.code < 500:
                raise Stop(EXIT_UNEXPECTED, f"ASU's class search answered HTTP {exc.code}; "
                                            "the seat checker needs an update.") from None
            problem = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            problem = str(getattr(exc, "reason", exc))
            if "tunnel connection failed" in problem.lower():  # this computer's own network refused, not ASU
                raise Stop(EXIT_BLOCKED, f"This computer's network won't connect to ASU's class search ({problem}).") \
                    from None
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
    kind_text = str(raw.get("SSR") or clas.get("XLATLONGNAME") or "").strip()
    kind_code = str(clas.get("SSRCOMPONENT") or clas.get("COMPONENTPRIMARY") or "").strip().upper()
    kind = KINDS.get(kind_text.upper(), kind_text) if kind_text else KINDS.get(kind_code, kind_code.title())
    return {
        "kind": kind,
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


# ---------- seats read in Cubbie's browser, for when ASU turns the script away ----------

def parse_seen_list(values: list[str] | None) -> dict[str, tuple[int, int]]:
    """--seen 21671=2/110 means the browser showed "Open seats: 2 of 110" for class 21671."""
    seen: dict[str, tuple[int, int]] = {}
    for value in values or []:
        m = re.fullmatch(r"\s*#?(\d{4,6})\s*=\s*(\d+)\s*(?:/|of)\s*(\d+)\s*", value or "")
        if not m:
            raise Stop(EXIT_INPUT, f"Couldn't read --seen {value!r}. Use <class number>=<open>/<total>, like 21671=0/110.")
        open_, total = int(m.group(2)), int(m.group(3))
        if open_ > total:
            raise Stop(EXIT_INPUT, f"--seen {value!r} has more open seats than total seats.")
        seen[m.group(1)] = (open_, total)
    return seen


def seen_section(term: str, nbr: str, subject: str, catalog: str, open_: int, total: int,
                 label: str | None = None) -> dict:
    """A section built from numbers read off the class search page instead of ASU's data feed."""
    return {"nbr": nbr, "term": term, "subject": subject, "catalog": catalog, "section": "", "title": "",
            "session": "", "start_date": "", "campus": "", "mode": "", "days": "", "start": "", "end": "",
            "room": "", "instructors": ["Staff"], "kind": "", "cap": total, "taken": max(total - open_, 0),
            "open": open_, "open_reserved": 0, "status": "OPEN" if open_ > 0 else "FULL", "deadline": "",
            "synthetic": True, "label": label or (f"#{nbr} {subject} {catalog}".strip() if subject else f"#{nbr}")}


def label_of(s: dict) -> str:
    return s["label"] if s.get("synthetic") else short_label(s)


BROWSER_NOTE = "Open it in the browser (the page needs JavaScript; a plain web read shows no seats)."


def blocked_search(args, exc: Stop, today: date) -> None:
    term = parse_term(args.term, today)
    print(f"BLOCKED {exc} Read the class search page instead. {BROWSER_NOTE}")
    if args.course:
        subject, catalog = parse_course(args.course)
        print(f"READ {COURSE_URL.format(subject=subject, catalog=catalog, term=term)}")
    else:
        print(f"READ {VIEW_URL.format(nbr=parse_class_nbr(args.cls), term=term)}")
    print("For each section, read the class number, days and time, instructor and the \"Open seats: X of Y\" line.")


def blocked_compare(args, exc: Stop, today: date) -> None:
    term = parse_term(args.term, today)
    print(f"BLOCKED {exc} Read each course's class search page instead. {BROWSER_NOTE}")
    for subject, catalog in dict.fromkeys(parse_course(c) for c in args.course):
        print(f"READ {subject} {catalog}: {COURSE_URL.format(subject=subject, catalog=catalog, term=term)}")
    print("For each course, note whether it's offered, its sections' instructors, and their \"Open seats\" lines.")


def blocked_watch_add(args, exc: Stop, today: date) -> None:
    term = parse_term(args.term, today)
    print(f"BLOCKED {exc} Read each class's seats on the class search page instead. {BROWSER_NOTE}")
    for nbr in dict.fromkeys(parse_class_nbr(c) for c in args.cls):
        print(f"READ #{nbr}: {VIEW_URL.format(nbr=nbr, term=term)}")
    print("Then run the same watch add command again, adding --seen <class number>=<open>/<total> for each class "
          "(from its \"Open seats: X of Y\" line), like --seen 21671=0/110.")


def blocked_check(args, exc: Stop, today: date) -> None:
    state = load_state(args.is_sample)
    print(f"BLOCKED {exc} Read each watched class on the class search page instead. {BROWSER_NOTE}")
    for w in state["watches"]:
        print(f"READ {w['label']}: {VIEW_URL.format(nbr=w['nbr'], term=w['term'])}")
    print("Then run: check --seen <class number>=<open>/<total> with one --seen per class you read (from its "
          "\"Open seats: X of Y\" line), like --seen 21671=0/110, and act on those lines as usual.")


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


def kind_tag(s: dict) -> str:
    """' Lab', ' Recitation' and so on; lectures (the usual case) get no tag."""
    kind = s.get("kind") or ""
    return "" if kind in ("", "Lecture") else f" {kind}"


def describe(s: dict) -> str:
    who = ", ".join(s["instructors"][:2])
    return (f"#{s['nbr']} {s['subject']} {s['catalog']}-{s['section']}{kind_tag(s)} | {when_where(s)} | {who} | "
            f"{seats_text(s)}")


def short_label(s: dict) -> str:
    if s["days"] and s["start"]:
        when = f"{s['days']} {s['start']}"
    elif is_online(s):
        when = "online"
    else:
        when = f"{s['campus']} {s['mode']}".strip()
    return f"#{s['nbr']} {s['subject']} {s['catalog']}-{s['section']}{kind_tag(s)} ({s['instructors'][0]}, {when})"


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
    state.setdefault("alert_by", None)
    state.setdefault("quiet", None)
    state.setdefault("tz", None)
    return state


def parse_alert(text: str) -> str:
    choice = ALERT_WORDS.get(re.sub(r"\s+", " ", (text or "").strip().lower()))
    if not choice:
        raise Stop(EXIT_INPUT, f"Couldn't read the alert choice {text!r}. Use text, call or both.")
    return choice


def parse_clock(text: str) -> str:
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?\s*m?\.?\s*", text or "", re.I)
    if not m:
        raise Stop(EXIT_INPUT, f"Couldn't read the time {text!r}. Use something like 11pm or 23:00.")
    hour, minute, half = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if half:
        if not 1 <= hour <= 12:
            raise Stop(EXIT_INPUT, f"Couldn't read the time {text!r}. Use something like 11pm or 23:00.")
        hour = hour % 12 + (12 if half == "p" else 0)
    if hour > 23 or minute > 59:
        raise Stop(EXIT_INPUT, f"Couldn't read the time {text!r}. Use something like 11pm or 23:00.")
    return f"{hour:02d}:{minute:02d}"


def parse_quiet(text: str) -> dict | None:
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    if t in ("off", "none", "no", "anytime", "any time", "call anytime", "always"):
        return None
    parts = re.split(r"\s*(?:-|to|until|till)\s*", t)
    if len(parts) != 2:
        raise Stop(EXIT_INPUT, f"Couldn't read the no-call hours {text!r}. Use something like \"11pm-8am\", or off.")
    start, end = parse_clock(parts[0]), parse_clock(parts[1])
    if start == end:
        raise Stop(EXIT_INPUT, "The no-call hours need a different start and end time.")
    return {"start": start, "end": end}


def clock_label(hhmm: str) -> str:
    hour, minute = int(hhmm[:2]), int(hhmm[3:])
    text = f"{hour % 12 or 12}" + (f":{minute:02d}" if minute else "")
    return f"{text} {'AM' if hour < 12 else 'PM'}"


def in_window(minutes_now: int, quiet: dict) -> bool:
    start = int(quiet["start"][:2]) * 60 + int(quiet["start"][3:])
    end = int(quiet["end"][:2]) * 60 + int(quiet["end"][3:])
    if start < end:
        return start <= minutes_now < end
    return minutes_now >= start or minutes_now < end  # the window runs past midnight


def student_minutes(state: dict, override: str | None) -> int | None:
    """Minutes past midnight in the student's time zone, or None if this computer can't tell."""
    if override:
        hhmm = parse_clock(override)
        return int(hhmm[:2]) * 60 + int(hhmm[3:])
    tz = state.get("tz")
    if not tz:
        return None
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(tz))
    except Exception:  # no time zone database here, or an unknown name
        return None
    return now.hour * 60 + now.minute


def check_tz(name: str) -> str:
    try:
        from zoneinfo import ZoneInfo, available_timezones
        if not available_timezones():  # this computer has no time zone database to check against
            return name
        ZoneInfo(name)
    except ImportError:
        return name
    except Exception:
        raise Stop(EXIT_INPUT, f"{name!r} isn't a time zone name. Use one like America/Phoenix.") from None
    return name


def quiet_text(state: dict) -> str:
    quiet = state.get("quiet")
    if not quiet:
        return "Calls at any hour."
    zone = f" {state['tz']}" if state.get("tz") else ""
    return f"No calls {clock_label(quiet['start'])} to {clock_label(quiet['end'])}{zone}; texts only then."


def alert_line(state: dict, now_minutes: int | None) -> str:
    choice = state.get("alert_by")
    picked = choice if choice in ALERT_CHOICES else "both"
    note = "" if choice in ALERT_CHOICES else "; the student never picked, so both is the default"
    quiet = state.get("quiet")
    if picked in ("call", "both") and quiet:
        window = f"{clock_label(quiet['start'])} to {clock_label(quiet['end'])}"
        if now_minutes is None:
            return (f"  alert by: {picked} ({ALERT_CHOICES[picked]}{note}), except no calls from {window} the "
                    "student's time: if it's inside that window now, text the alert instead of calling")
        if in_window(now_minutes, quiet):
            now = f"{now_minutes // 60:02d}:{now_minutes % 60:02d}"
            return (f"  alert by: text (they picked {picked}, but it's {clock_label(now)} for them, inside their "
                    f"no-call hours of {window}, so text only)")
    return f"  alert by: {picked} ({ALERT_CHOICES[picked]}{note})"


def save_state(state: dict, sample: bool) -> None:
    path = state_file(sample)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# ---------- commands ----------

def by_campus(sections: list[dict], campus: str | None) -> list[dict]:
    if not campus or campus.lower() in ("any", "all"):
        return sections
    want = campus.lower()
    if want in ("online", "asu online", "icourse"):
        return [s for s in sections if is_online(s)]
    return [s for s in sections if want in s["campus"].lower()]


def cmd_compare(args, today: date) -> int:
    """One line per course: offered or not, how full, and who teaches it (for looking up reviews)."""
    term = parse_term(args.term, today)
    courses = list(dict.fromkeys(parse_course(c) for c in args.course))
    campus = (args.campus or "").strip().lower()
    if not campus or campus in ("any", "all"):
        where = ""
    elif campus in ("online", "asu online", "icourse"):
        where = " (online sections)"
    else:
        where = f" at {campus.title()}"
    print(f"{term_label(term)} (term {term}){where}, one line per course:")
    for subject, catalog in courses:
        try:
            sections = [s for s in fetch(term, subject=subject, catalog=catalog, sample=args.sample_path)["sections"]
                        if s["subject"] == subject and s["catalog"] == catalog]
        except Stop as exc:
            if exc.code in (EXIT_UNREACHABLE, EXIT_BLOCKED):
                raise
            print(f"{subject} {catalog}: couldn't check ({exc})")
            continue
        sections = by_campus(sections, args.campus)
        if not sections:
            print(f"{subject} {catalog}: not offered{where} in {term_label(term)}")
            continue
        full = sum(1 for s in sections if s["status"] == "FULL")
        open_seats = sum(s["open"] for s in sections)
        teachers: dict[str, list[int]] = {}
        for s in sections:
            counts = teachers.setdefault(s["instructors"][0], [0, 0])
            counts[0] += 1
            counts[1] += s["open"]
        who = "; ".join(f"{name} ({n} section{'s' if n != 1 else ''}, {seats} open)"
                        for name, (n, seats) in sorted(teachers.items(), key=lambda kv: (-kv[1][0], kv[0])))
        print(f"{subject} {catalog} {sections[0]['title']}: {len(sections)} section{'s' if len(sections) != 1 else ''}, "
              f"{full} full, {open_seats} open seats | {who}")
    return 0


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
    shown = by_campus(shown, args.campus)
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
    if args.together and len(nbrs) < 2:
        raise Stop(EXIT_INPUT, "--together needs at least two class numbers, like a lecture and its lab.")
    found: dict[str, dict] = {}
    courses = list(dict.fromkeys(parse_course(c) for c in args.course or []))
    for nbr, (open_, total) in parse_seen_list(args.seen).items():  # read off the page in Cubbie's browser
        if nbr in nbrs:
            subject, catalog = courses[0] if len(courses) == 1 else ("", "")
            found[nbr] = seen_section(term, nbr, subject, catalog, open_, total)
    todo = [n for n in nbrs if n not in found]
    if todo:
        for subject, catalog in courses:
            for s in fetch(term, subject=subject, catalog=catalog, sample=args.sample_path)["sections"]:
                if s["nbr"] in todo:
                    found[s["nbr"]] = s
        for nbr in todo:
            if nbr not in found:
                for s in fetch(term, keywords=nbr, sample=args.sample_path)["sections"]:
                    if s["nbr"] == nbr:
                        found[nbr] = s
    missing = [n for n in nbrs if n not in found]
    if not found:
        raise Stop(EXIT_NOT_FOUND, f"Couldn't find class #{', #'.join(missing)} for {term_label(term)}. "
                                   "Check the number, or include the course, like --course \"CSE 340\".")

    state = load_state(args.is_sample)
    if args.alert:
        state["alert_by"] = parse_alert(args.alert)
    if args.together and len(found) < 2:
        raise Stop(EXIT_NOT_FOUND, f"Only found #{', #'.join(found)} for {term_label(term)}, so there's nothing to "
                                   "watch together. Check the other class number or its course.")
    group = "+".join(sorted(found)) if args.together else None
    added = 0
    for nbr in nbrs:
        s = found.get(nbr)
        if s is None:
            print(f"NOT-FOUND #{nbr} isn't on ASU's class search for {term_label(term)}; not watching it.")
            continue
        label = label_of(s)
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
        entry["group"] = group if args.together else (existing or {}).get("group")
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
    if group:
        print(f"TOGETHER #{' and #'.join(sorted(found))}: the alert comes only when every one of them has a seat.")
    count = len(state["watches"])
    print(f"Watching {count} class{'es' if count != 1 else ''} in total.")
    if state["alert_by"] in ALERT_CHOICES:
        print(f"ALERT-BY {state['alert_by']}: {ALERT_CHOICES[state['alert_by']]}. {quiet_text(state)}")
    else:
        print("ASK-ALERTS The student hasn't picked how to be alerted. Ask: text, call or both? "
              "Then run: alerts --set <choice>")
    return 0 if added else EXIT_NOT_FOUND


def cmd_alerts(args, today: date) -> int:
    state = load_state(args.is_sample)
    changed = False
    if args.set:
        state["alert_by"] = parse_alert(args.set)
        changed = True
    if args.tz:
        state["tz"] = check_tz(args.tz.strip())
        changed = True
    if args.quiet:
        state["quiet"] = parse_quiet(args.quiet)
        changed = True
    if changed:
        save_state(state, args.is_sample)
    choice = state["alert_by"]
    if choice in ALERT_CHOICES:
        print(f"ALERT-BY {choice}: {ALERT_CHOICES[choice]}. {quiet_text(state)}")
        if choice in ("call", "both") and state.get("quiet") is None and not args.quiet:
            print("ASK-QUIET Calls can come at any hour. Ask whether to skip calls at night and text instead, "
                  "like 11pm-8am, then run: alerts --quiet <window> --tz <student's time zone> (or --quiet off)")
        elif state.get("quiet") and not state.get("tz"):
            print("ASK-TZ Run: alerts --tz <student's time zone, like America/Phoenix>, so no-call hours use their clock.")
    else:
        print("ALERT-BY not set. Ask the student: text, call or both?")
    return 0


def ago(iso: str | None) -> str:
    """How long ago a time from this computer's clock was, so the student's time zone doesn't matter."""
    try:
        mins = int((datetime.now() - datetime.fromisoformat(iso)).total_seconds() // 60)
    except (TypeError, ValueError):
        return "not yet"
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    if mins < 48 * 60:
        return f"{mins // 60} h ago"
    return f"{mins // (24 * 60)} days ago"


def cmd_watch_list(args, today: date) -> int:
    state = load_state(args.is_sample)
    if not state["watches"]:
        print("NO-WATCHES Not watching any classes.")
        return 0
    count = len(state["watches"])
    print(f"Watching {count} class{'es' if count != 1 else ''}:")
    for w in state["watches"]:
        partners = [o["nbr"] for o in state["watches"] if w.get("group") and o.get("group") == w["group"]
                    and o["nbr"] != w["nbr"]]
        together = f" (together with #{', #'.join(partners)})" if partners else ""
        print(f"{w['label']}{together}, {term_label(w['term'])}: {w.get('last_status', '?')} when last checked "
              f"{ago(w.get('last_checked'))}, watching since {w.get('added', '?')}")
    return 0


def cmd_drill(args, today: date) -> int:
    """A test alert: the saved CSE 340 data with full section #21671 suddenly showing 2 open seats.

    Uses no network and leaves every watch list alone."""
    s = next((x for x in fetch("2271", subject="CSE", catalog="340", sample=SAMPLE_FILE)["sections"]
              if x["nbr"] == "21671"), None)
    if s is None:
        raise Stop(EXIT_UNEXPECTED, "The Skill's sample data is missing; install the Skill again.")
    s = dict(s, taken=s["cap"] - 2, open=2, open_reserved=0, status="OPEN")
    print("TEST-ALERT This is a drill, not a real opening. Handle the next lines like NEW-OPEN, "
          "but start the call and the text with \"Test alert\".")
    print(f"NEW-OPEN {short_label(s)} has 2 open seats of {s['cap']}")
    state = load_state(args.is_sample)
    print(alert_line(state, student_minutes(state, args.now)))
    print("\n".join(link_lines("2271", "21671")))
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


def cmd_page_blocked(args, state: dict) -> int:
    """The browser hit a human check or sign-in screen during a scheduled check: ask the student once, not every time."""
    state["verify_streak"] = state.get("verify_streak", 0) + 1
    save_state(state, args.is_sample)
    streak = state["verify_streak"]
    if streak == 1 or streak % WARN_AGAIN_AFTER == 0:
        print("ASK-VERIFY ASU's class search wants a quick human check before seats can be read. Text the student "
              "once: reply when you can take over the browser for a few seconds, and checks will pick up again.")
    else:
        print(f"NEEDS-VERIFY still waiting for the student's human check ({streak} checks so far; stay quiet)")
    return 0


def cmd_check(args, today: date) -> int:
    state = load_state(args.is_sample)
    watches = state["watches"]
    if not watches:
        print("NO-WATCHES Nothing to watch, so the Seat Snipe check-in can be deleted.")
        return 0
    if args.page_blocked:
        return cmd_page_blocked(args, state)

    found: dict[tuple[str, str], dict | None] = {}
    seen = parse_seen_list(args.seen)
    groups: dict[tuple[str, str, str], list[dict]] = {}
    singles: list[dict] = []
    for w in watches:
        if seen:
            if w["nbr"] in seen:
                open_, total = seen[w["nbr"]]
                found[(w["term"], w["nbr"])] = seen_section(w["term"], w["nbr"], w["subject"], w["catalog"],
                                                            open_, total, w["label"])
        elif w.get("subject") and w.get("catalog"):
            groups.setdefault((w["term"], w["subject"], w["catalog"]), []).append(w)
        else:
            singles.append(w)
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
        for w in singles:
            found[(w["term"], w["nbr"])] = next(
                (x for x in fetch(w["term"], keywords=w["nbr"], sample=args.sample_path)["sections"]
                 if x["nbr"] == w["nbr"]), None)
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
    state["verify_streak"] = 0
    now = datetime.now().isoformat(timespec="minutes")
    alert = alert_line(state, student_minutes(state, args.now))
    new_open = 0
    keep = []
    rows = []  # (watch, section, previous status, current status) for every class still being watched
    for w in watches:
        label = w["label"]
        if w.get("deadline") and today.isoformat() > w["deadline"]:
            print(f"ENDED {label}: the last day to add it ({pretty_date(w['deadline'])}) has passed, "
                  "so watching stopped.")
            continue
        if seen and w["nbr"] not in seen:
            print(f"UNREAD {label} wasn't read this time (stay quiet)")
            keep.append(w)
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
        if not s.get("synthetic"):
            w["label"] = short_label(s)
        if s["status"] == "CANCELLED":
            print(f"ENDED {w['label']} was cancelled by ASU, so watching stopped.")
            continue
        rows.append((w, s, w.get("last_status"), s["status"]))
        keep.append(w)

    sizes: dict[str, int] = {}  # members of each set still being watched, read this time or not
    for w in keep:
        if w.get("group"):
            sizes[w["group"]] = sizes.get(w["group"], 0) + 1
    handled: set[str] = set()
    for w, s, previous, current in rows:
        group = w.get("group")
        if group and sizes.get(group, 0) >= 2:
            # A set watched together, like a lecture and its lab: the student needs a seat in every one.
            if group in handled:
                continue
            handled.add(group)
            members = [row for row in rows if row[0].get("group") == group]
            names = " and ".join(m[0]["label"] for m in members)
            if len(members) < sizes[group]:
                print(f"PARTLY-READ {names}: the rest of the set wasn't read this time (stay quiet)")
                continue
            all_open_now = all(m[3] == "OPEN" for m in members)
            all_open_before = all(m[2] == "OPEN" for m in members)
            if all_open_now and not all_open_before:
                new_open += 1
                print(f"NEW-OPEN {names}: every one of them has a seat now, and the student needs all of them")
                print(alert)
                for m in members:
                    print(f"  {m[0]['label']}: {seats_text(m[1])}")
                    print("\n".join("  " + line for line in link_lines(m[0]["term"], m[0]["nbr"])))
            elif all_open_now:
                print(f"STILL-OPEN {names} (the student already knows)")
            else:
                opened = [m[0]["label"] for m in members if m[3] == "OPEN"]
                if opened:
                    print(f"PARTLY-OPEN {' and '.join(opened)} has a seat, but the rest of the set is still full "
                          "(stay quiet)")
                else:
                    print(f"FULL {names} (watched together)")
        elif current == "OPEN" and previous != "OPEN":
            new_open += 1
            print(f"NEW-OPEN {w['label']} has {s['open']} open seat{'s' if s['open'] != 1 else ''} of {s['cap']}")
            print(alert)
            print("\n".join(link_lines(w["term"], w["nbr"])))
        elif current == "OPEN":
            print(f"STILL-OPEN {w['label']}, {seats_text(s)} (the student already knows)")
        elif current == "RESERVED" and previous == "FULL":
            print(f"NEW-RESERVED {w['label']} has {s['open_reserved']} seats open, but all are reserved "
                  "for specific groups of students")
            print(link_lines(w["term"], w["nbr"])[1])
        else:
            print(f"{current} {w['label']}, {seats_text(s)}")
    for w, s, previous, current in rows:
        w["last_status"] = current
        w["last_checked"] = now
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
    common.add_argument("--now", help=argparse.SUPPRESS)  # the student's local time, for testing no-call hours

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
    search.set_defaults(func=cmd_search, on_blocked=blocked_search)

    compare = sub.add_parser("compare", parents=[common],
                             help="one line per course: offered or not, how full, and who teaches it")
    compare.add_argument("--term", default="next", help='"spring 2027", "fall", "next" or a code like 2271')
    compare.add_argument("--course", action="append", required=True, help='like "CSE 340"; repeat for more')
    compare.add_argument("--campus", help="tempe, poly, west, downtown, online or any")
    compare.set_defaults(func=cmd_compare, on_blocked=blocked_compare)

    watch = sub.add_parser("watch", help="add, list or remove watched classes")
    watch_sub = watch.add_subparsers(dest="action", required=True)
    add = watch_sub.add_parser("add", parents=[common], help="start watching classes")
    add.add_argument("--term", default="next")
    add.add_argument("--course", action="append",
                     help='subject and number, like "CSE 340" (faster and more reliable); repeat for more')
    add.add_argument("--class", dest="cls", action="append", required=True, help="class number; repeat for more")
    add.add_argument("--together", action="store_true",
                     help="the student needs all of these (like a lecture and its lab): alert only when all have seats")
    add.add_argument("--alert", help="how the student wants to hear about an opening: text, call or both")
    add.add_argument("--seen", action="append",
                     help="seats read on the class search page, as <class number>=<open>/<total>; repeat for more")
    add.set_defaults(func=cmd_watch_add, on_blocked=blocked_watch_add)
    lst = watch_sub.add_parser("list", parents=[common], help="show what is being watched")
    lst.set_defaults(func=cmd_watch_list)
    rm = watch_sub.add_parser("remove", parents=[common], help="stop watching classes")
    rm.add_argument("--class", dest="cls", action="append", help="class number; repeat for more")
    rm.add_argument("--all", action="store_true", help="stop watching everything")
    rm.set_defaults(func=cmd_watch_remove)

    check = sub.add_parser("check", parents=[common], help="check every watched class once (the scheduled check)")
    check.add_argument("--seen", action="append",
                       help="seats read on the class search page, as <class number>=<open>/<total>; repeat for more")
    check.add_argument("--page-blocked", action="store_true",
                       help="the browser got a human check or sign-in screen instead of the class list")
    check.set_defaults(func=cmd_check, on_blocked=blocked_check)

    drill = sub.add_parser("drill", parents=[common], help="print a test alert from the saved sample data")
    drill.set_defaults(func=cmd_drill)

    alerts = sub.add_parser("alerts", parents=[common], help="show or set how the student hears about an opening")
    alerts.add_argument("--set", help="text, call or both")
    alerts.add_argument("--quiet", help='no calls in this window, texts instead: like "11pm-8am", or off')
    alerts.add_argument("--tz", help="the student's time zone, like America/Phoenix")
    alerts.set_defaults(func=cmd_alerts)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.sample or args.sample_file:
        args.sample_path = Path(args.sample_file) if args.sample_file else SAMPLE_FILE
    else:
        args.sample_path = None
    args.is_sample = args.sample_path is not None
    try:
        today = parse_today(args.today)
        return args.func(args, today) or 0
    except Stop as exc:
        on_blocked = getattr(args, "on_blocked", None)
        if exc.code == EXIT_BLOCKED and on_blocked:
            try:
                on_blocked(args, exc, today)
            except Stop as inner:
                print(f"BLOCKED {exc}")
                print(inner)
        else:
            print(exc)
        return exc.code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:  # whoever was reading stopped early (piped into head, say); nothing to report
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
