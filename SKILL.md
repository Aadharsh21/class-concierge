---
name: seat-snipe
description: Plans an ASU student's classes from their major map and student reviews, then watches full classes and alerts them by text, call or both the moment a seat opens. Use when an Arizona State University student asks which classes to register for next semester, what their major map requires, which electives or professors are easiest or best rated, says a class is full, asks to be told when a seat opens, wants to track a seat or class number during registration, asks how many seats are left, asks which classes are being watched, wants to change alerts or no-call hours, or wants a test seat alert. Not for study schedules or homework.
---

# Seat Snipe

Helps an Arizona State University student get the classes they need: it reads their major map to say what next
semester should look like, suggests the easiest electives and best-rated professors from student reviews, and
watches full classes on ASU's public class search so the student hears the moment a seat opens. It covers every
ASU campus and ASU Online, and only ASU.

It never enrolls, drops or swaps classes for the student: My ASU needs their own sign-in. It sends the add
link and the student adds the class themselves. Course choices are the student's; Seat Snipe only suggests.

## When to use

- "what classes should I take next semester?" or "what's on my major map for spring?"
- "what's the easiest HU elective?" or "who's the best professor for CSE 330?"
- "CSE 365 is full, tell me when a seat opens"
- "watch class 21671 for spring" or "my BIO 181 lab is full too"
- "any seats left in MAT 343 with Smith?"
- "what classes are you watching for me?" or "I got in, stop watching CSE 365"
- "just text me from now on", "call me if I don't answer" or "no calls after 11pm"
- "send me a test alert"

Not for study schedules, homework, or anything that changes the student's enrollment.

To plan a semester, start at section 6. To watch a class, start at section 1. Whenever a command prints `BLOCKED`,
see section 7.

## How it works

Run every command from this Skill's folder. `scripts/seats.py` reads ASU's public class search: no sign-in and
no key. It keeps the watch list and the student's alert choice under `~/.config/seat-snipe/`, so they carry
over between conversations.

### 1. Find the class

1. Work out the term from what the student said ("spring", "fall 2027") and pass it as `--term`. If they didn't
   say, pass `--term next` and mention which term you used.
2. Run:

   ```bash
   python3 scripts/seats.py search --term "<term>" --course "<SUBJECT NUMBER>"
   ```

   Add `--campus tempe` (or `poly`, `west`, `downtown`, `online`) or `--instructor "<last name>"` when the student
   mentioned one. If they gave a 5-digit class number instead of a course, use `--class <number>`.
3. Each line is one section: class number, days and time, place, instructor, seats. Labs, recitations and other
   non-lecture sections are labeled (like `#99001 PHY 122-1101 Lab`) and have their own class numbers and seats.
   Show the student only the sections that fit what they asked for (at most five), one short line each, and ask
   which ones to watch. Suggest the full ones that match as the default.
4. If the course comes with a lab or recitation (in the same listing, or as a separate course like a lecture's
   companion lab), ask which one the student wants too: they need a seat in both, so check both.

If the student only wanted to know how many seats are left, answer from the search and stop here.

### 2. Ask how to alert them, then start watching

1. If a section the student wants already has open seats, say so right away and send its add link
   (`search ... --links` prints it). They may still want it watched in case it fills before they can enroll.
2. Run `python3 scripts/seats.py alerts` to see whether the student already picked how to be alerted.
   - `ALERT-BY not set`: before watching anything, ask in one message, for example:

     ```text
     I'll check #21671 every 15 min. When a seat opens, how should I tell you?
     1. Text with the add link
     2. Phone call
     3. Both: a call so you don't miss it, then the link by text
     4. Text first, and call only if you don't reply in 5 min
     ```

     Their answer is also their yes to start. If they reply just "yes" or "ok", use both. Save option 4 as
     `escalate`.
   - If they picked anything with a call (2, 3 or 4), ask one more question: "Should I skip calls at night and just text then? Give
     me a window like 11pm to 8am, or say call anytime." Suggest 11pm to 8am as the default.
   - Save the answers in one command, with the student's time zone so the window follows their clock:

     ```bash
     python3 scripts/seats.py alerts --set <text|call|both|escalate> --quiet "<window, like 11pm-8am, or off>" --tz "<time zone, like America/Phoenix>"
     ```

     Leave out `--quiet` and `--tz` when they picked text.
   - A saved choice: confirm with it instead, for example "I'll check #21671 every 15 min and call + text you,
     same as last time. Start?" Start only after the student says yes.
3. Run:

   ```bash
   python3 scripts/seats.py watch add --term "<term>" --course "<SUBJECT NUMBER>" --class <number>
   ```

   Repeat `--class` to watch several sections of the same course in one go. If the output says `ASK-ALERTS`, ask
   the alert question above and save the answer with `alerts --set`.

   **Lecture and lab:** if only one of the pair is full, watch just that one. If both are full, watch them as a
   set, so the alert comes only when both have a seat (a lecture seat is no use while the lab is still full).
   Repeat `--course` when they're different courses:

   ```bash
   python3 scripts/seats.py watch add --term "<term>" --course "<LECTURE COURSE>" --course "<LAB COURSE>" --class <lecture number> --class <lab number> --together
   ```

4. If there is no Seat Snipe check-in yet, create one recurring check-in that runs every 15 minutes, with this task:
   "Seat Snipe: from the seat-snipe Skill's folder, run `python3 scripts/seats.py check` and follow the
   Scheduled check steps in its SKILL.md. Contact the student only when those steps say to."
   One check-in covers every watched class, so never create a second one.
5. Reply in one or two lines: what is being watched, and how you'll alert them.

### 3. Scheduled check (every 15 minutes, on its own)

Run `python3 scripts/seats.py check` and act only on these lines:

- `NEW-OPEN`: a seat just opened. Alert the student right away the way the `alert by` line under it says (it
  already accounts for their no-call hours; if it asks you to check their local time, do):
  - `text`: text the alert (see Replies) with the add link on its own line. Don't call.
  - `call`: call the student. Say which course opened, read the class number digit by digit, and tell them to
    add it in My ASU now. Don't text.
  - `both`: call first and keep it to two sentences (which class opened, and that the link is in their texts),
    then text the alert.
  - `escalate`: text the alert now, and don't call yet. Then do what the `follow-up:` line says: create the
    one-time check-in it describes, 5 minutes out. When that check-in runs `followup`, `CALL-NOW` means call the
    student (two sentences, like `both`), and `NO-CALL` means stay silent.

  Whenever the student replies after a seat alert (anything at all, even "ok"), run
  `python3 scripts/seats.py ack` so a pending follow-up call is cancelled.

  If a call can't be placed, text the alert instead and say the call didn't go through.

  When a `NEW-OPEN` names a set (like a lecture and its lab), every class in it has a seat now: say they need to
  add all of them, and text each add link on its own line.
- `NEW-RESERVED`: seats opened, but all are reserved for specific groups of students (a major or cohort, for
  example). Text once, never call, with the view link so they can check whether they qualify.
- `ENDED`: watching stopped for that class (the last day to add it passed, it was cancelled, or it left the class
  search). Text the student once.
- `WARN`: ASU's class search has blocked or ignored the last several checks. Text the student once that you're
  still trying, and that a screenshot of Class Search from their phone works in the meantime (section 7).
- `NO-WATCHES`: nothing is left to watch. Delete this Seat Snipe check-in and don't text the student.

Anything else (`FULL`, `RESERVED`, `STILL-OPEN`, `PARTLY-OPEN`, `PARTLY-READ`, `UNREAD`, `MISSING`, `FAILED`,
`SUMMARY`) needs no message: stay silent. A blocked check prints `FAILED`; the next check simply tries again. `PARTLY-OPEN` means only part of a set has a seat, which the student
can't use yet.
A watched class stays on the list after an alert until the student says they got in, so if it fills and opens
again they hear about it again.

### 4. Managing the watch list and alerts

- "What are you watching?" or "are you still checking?": `python3 scripts/seats.py watch list` (it also shows
  when each class was last checked).
- "I got in", "stop watching CSE 365": `python3 scripts/seats.py watch remove --class <number>`, or `--all`.
- "Just text me", "call me instead", "do both", "text me first and call if I don't answer":
  `python3 scripts/seats.py alerts --set <text|call|both|escalate>`, then confirm the change in one line.
- "No calls after 10pm", "don't call before 9", "call me anytime":
  `python3 scripts/seats.py alerts --quiet "<window, like 10pm-8am>" --tz "<time zone>"`, or `--quiet off`.
- Whenever a command prints `NO-WATCHES`, delete the Seat Snipe check-in.

### 5. Test alert

When the student wants to see what an alert looks like ("send me a test alert", "test seat snipe"), run
`python3 scripts/seats.py drill`. It prints a `NEW-OPEN` line built from saved sample data, without touching the
network or the watch list. Handle it exactly like a real `NEW-OPEN`, following its `alert by` line (no-call hours
included, so the student sees exactly what would happen right now), but start the call and the text with
"Test alert" so it is never mistaken for a real seat. If the student asks for the test later ("in 5
minutes"), do it as a one-time check-in.

### 6. Plan next semester: major map, electives and professors

1. **Know the student.** Use what Cubbie remembers. Otherwise ask in one message: "What's your major, and which
   semester are you going into? (like 'CSE, semester 6' or 'junior spring')". Then work out:
   - the term on the map: semester 6 is Term 6. For transfer students or anyone off the usual pace, ask roughly how
     many credits they've finished and use the term whose hour range covers that number.
   - their catalog year, usually the fall they started at ASU. Someone going into semester 6 in spring 2027
     started in fall 2024, so use 2024. Ask if it's unclear.

   Remember the major, catalog year and term so the student isn't asked again.
2. **Open their major map.** Search the web for "ASU major map <major>" and take the plan code from a
   degrees.asu.edu link: the part right after `ASU00/`, like ESCSEBSE for Computer Systems Engineering BSE.
   Then read the map's PDF:

   ```text
   https://degrees.asu.edu/major-map/ASU00/<PLAN CODE>/null/ALL/<CATALOG YEAR>/fetchpdf
   ```

   If it doesn't open, try the year before, and say which year's map you used.
3. **Read their term.** Pull out the courses the map marks as critical (later terms call them necessary) with
   their minimum grades, the term's other courses and requirements, and every elective slot (like "Upper Division
   CSE Technical Elective" or "Upper Division HU or SB") with the approved course list the map gives for it.
4. **Text the summary**, critical courses first (see Replies). If it runs past a phone screen, make a one-page
   PDF of the plan, send it, and text only the highlights. Say that their degree audit in My ASU is the final word,
   since the map is the recommended order, not their record.
5. **Offer the shortlist:** "Want me to find the easiest electives and the best-rated professors?" On yes, work on
   it as a background task and text the shortlist when it's ready:
   1. For each elective slot, pick up to six candidates from the map's approved list. For a broad general studies
      slot, also search the web for courses ASU students call easy in that area (Reddit's r/ASU is a good
      source). Skip anything the student has taken or ruled out.
   2. Check them all in one run, adding `--campus <their campus>`:

      ```bash
      python3 scripts/seats.py compare --term "<term>" --course "<SUBJECT NUMBER>" --course "<SUBJECT NUMBER>"
      ```

      Each line says whether the course is offered that term, how full it is, and who teaches it. Drop courses
      that aren't offered.
   3. For each instructor teaching a candidate or a critical course, search the web for
      "<instructor> Arizona State University Rate My Professors" and read the overall rating, level of difficulty,
      would-take-again percentage and number of ratings. Fewer than five ratings is weak evidence; say so.
   4. Rank the electives: lower difficulty and higher rating first, then more open seats. Suggest the top three,
      one line each: course, best professor, rating and difficulty, seats.
   5. For each critical course, name the best-rated professor's section with its class number and seats.

   Say the ratings come from student reviews on Rate My Professors and can be out of date. Never invent a rating,
   a course or a professor.
6. **The student's own picks win.** If they name the professors or courses they want ("CSE 330 with Smith"), use
   those (`search --instructor "<name>"`). Remember professors they like or want to avoid for next time.
7. **Hand off to watching:** end with "Want me to watch any of these?" A full section goes straight to section 2.

### 7. When ASU blocks Cubbie (exit code 6, `BLOCKED`)

ASU's class search blocks Cubbie's servers some of the time, even though it works fine on the student's phone.
Don't open the class search in Cubbie's browser to get around this, and don't ask the student to take over the
browser for a verification check: ASU sends Cubbie's browser to a sign-in page even after the check.

- **The student is waiting (search, compare, watch add):** say it in one line, like "ASU's class search is blocking
  me right now." Send them the link the script printed so they can open it on their phone, and ask for a
  screenshot. Read the class numbers, days and times, instructors and "Open seats: X of Y" lines from the
  screenshot and use them exactly as you would the script's output. Offer to try the live lookup again later.
- **Watching from a screenshot:** run the same `watch add` command with one `--seen <class number>=<open>/<total>`
  per class, like `--seen 21671=0/110`. The scheduled checks then keep trying ASU directly.
- **A screenshot arrives any time later:** read it and run
  `python3 scripts/seats.py check --seen <class number>=<open>/<total>` with one `--seen` per watched class you can
  see, then act on its lines as section 3 says.
- **Scheduled check:** nothing extra to do. A blocked check prints `FAILED` (stay silent), and after several in a
  row it prints `WARN` once (section 3).

Never guess seat numbers: if a screenshot doesn't show a class, leave that class out.

## Replies

Keep each reply to a few short lines of plain text with no Markdown. Lead with what matters. Put every link on its
own line, and write class numbers with `#` (like #21671) so the student can find them in My ASU. A seat alert
text should look like this:

```text
Seat open in CSE 340 (#21671, Claveau)! 2 seats left.
Add it now:
https://go.oasis.asu.edu/addclass/?STRM=2271&ASU_CLASS_NBR=21671
Seats go fast. I'll keep watching until you tell me you got in.
```

A call (call only) sounds like: "Hey, it's Cubbie. A seat just opened in CSE 340 with Claveau, class number
2 1 6 7 1. Add it in My ASU now, seats go fast."

A semester summary should look like this:

```text
Spring 2027 is Term 6 on your <major> map (<catalog year> catalog).
Critical: <course>, <course> (C or better)
Also: <course>, <course>, plus <requirement>
Want me to find the easiest picks and the best-rated professors?
```

Never say the student is enrolled, and never report a seat the script didn't print.

## When something fails

| Exit code | Meaning | What to do |
| --- | --- | --- |
| 2 | The term, course, class number, alert choice, no-call window or time zone couldn't be read | Ask for it again, like "CSE 340, spring 2027", a 5-digit class number, text/call/both/text-first, or a window like 11pm-8am |
| 3 | Not on ASU's class search for that term | Say so, and ask the student to check the course, number or term |
| 4 | ASU's class search isn't answering | If the student is waiting, say ASU's class search isn't responding and offer to try again in a few minutes. During a scheduled check, stay silent unless a `WARN` line printed |
| 5 | ASU answered in an unexpected way | Say the seat checker needs an update. Never guess seat numbers |
| 6 | ASU blocked Cubbie (`BLOCKED`) | If the student is waiting, say so in one line and ask for a screenshot (section 7). During a scheduled check, stay silent unless a `WARN` line printed |

Without scheduled check-ins or calls (when this Skill runs outside Cubbie), run `check` whenever the student asks
and tell them to ask again later.

## Trying it without the network

Add `--sample` to any command to use the saved copy of the real Spring 2027 CSE 340 search in `assets/`. Sample
runs keep their own watch list and alert choice, and never touch the student's.

```bash
python3 scripts/seats.py search --term 2271 --course "CSE 340" --sample
```
