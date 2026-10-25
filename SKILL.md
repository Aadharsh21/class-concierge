---
name: seat-snipe
description: Watches full ASU classes and alerts the student by text, phone call or both (their pick) the moment a seat opens, with a link to add the class. Use when an Arizona State University student says a class is full, asks to be told when a seat opens, wants to snipe or track a seat or a class number during registration, asks how many seats are left in a course, asks which classes are being watched, wants to change how they get alerts, or wants a test seat alert.
---

# Seat Snipe

Watches full classes on Arizona State University's public class search and tells the student the moment a
seat opens, so they can grab it before anyone else. It covers every ASU campus and ASU Online, and only ASU.

It never enrolls, drops or swaps classes for the student: My ASU needs their own sign-in. It sends the add
link and the student adds the class themselves.

## When to use

- "CSE 365 is full, tell me when a seat opens"
- "watch class 21671 for spring"
- "any seats left in MAT 343 with Smith?"
- "what classes are you watching for me?" or "I got in, stop watching CSE 365"
- "just text me from now on" or "call me instead"
- "send me a test alert"

Not for choosing classes, degree planning, or anything that changes the student's enrollment.

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
3. Each line is one section: class number, days and time, place, instructor, seats. Show the student only the
   sections that fit what they asked for (at most five), one short line each, and ask which ones to watch. Suggest
   the full ones that match as the default.

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
     Calls can come at any hour.
     ```

     Their answer is also their yes to start. If they reply just "yes" or "ok", use both.
   - A saved choice: confirm with it instead, for example "I'll check #21671 every 15 min and call + text you,
     same as last time. Start?" Start only after the student says yes.
3. Run, adding `--alert <text|call|both>` only when the student just picked one:

   ```bash
   python3 scripts/seats.py watch add --term "<term>" --course "<SUBJECT NUMBER>" --class <number> --alert both
   ```

   Repeat `--class` to watch several sections of the same course in one go. If the output says `ASK-ALERTS`, ask
   the question above, then run `python3 scripts/seats.py alerts --set <choice>`.
4. If there is no Seat Snipe check-in yet, create one recurring check-in that runs every 15 minutes, with this task:
   "Seat Snipe: from the seat-snipe Skill's folder, run `python3 scripts/seats.py check` and follow the
   Scheduled check steps in its SKILL.md. Contact the student only when those steps say to."
   One check-in covers every watched class, so never create a second one.
5. Reply in one or two lines: what is being watched, and how you'll alert them.

### 3. Scheduled check (every 15 minutes, on its own)

Run `python3 scripts/seats.py check`, then act only on these lines:

- `NEW-OPEN`: a seat just opened. Alert the student right away the way the `alert by` line under it says:
  - `text`: text the alert (see Replies) with the add link on its own line. Don't call.
  - `call`: call the student. Say which course opened, read the class number digit by digit, and tell them to
    add it in My ASU now. Don't text.
  - `both`: call first and keep it to two sentences (which class opened, and that the link is in their texts),
    then text the alert.

  If a call can't be placed, text the alert instead and say the call didn't go through.
- `NEW-RESERVED`: seats opened, but all are reserved for specific groups of students (a major or cohort, for
  example). Text once, never call, with the view link so they can check whether they qualify.
- `ENDED`: watching stopped for that class (the last day to add it passed, it was cancelled, or it left the class
  search). Text the student once.
- `WARN`: ASU's class search hasn't answered for a while. Text the student once that you'll keep trying.
- `NO-WATCHES`: nothing is left to watch. Delete this Seat Snipe check-in and don't text the student.

Anything else (`FULL`, `RESERVED`, `STILL-OPEN`, `MISSING`, `FAILED`, `SUMMARY`) needs no message: stay silent.
A watched class stays on the list after an alert until the student says they got in, so if it fills and opens
again they hear about it again.

### 4. Managing the watch list and alerts

- "What are you watching?" or "are you still checking?": `python3 scripts/seats.py watch list` (it also shows
  when each class was last checked).
- "I got in", "stop watching CSE 365": `python3 scripts/seats.py watch remove --class <number>`, or `--all`.
- "Just text me", "call me instead", "do both": `python3 scripts/seats.py alerts --set <text|call|both>`, then
  confirm the change in one line.
- Whenever a command prints `NO-WATCHES`, delete the Seat Snipe check-in.

### 5. Test alert

When the student wants to see what an alert looks like ("send me a test alert", "test seat snipe"), run
`python3 scripts/seats.py drill`. It prints a `NEW-OPEN` line built from saved sample data, without touching the
network or the watch list. Handle it exactly like a real `NEW-OPEN`, following its `alert by` line, but start the
call and the text with "Test alert" so it is never mistaken for a real seat. If the student asks for the test
later ("in 5 minutes"), do it as a one-time check-in.

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

Never say the student is enrolled, and never report a seat the script didn't print.

## When something fails

| Exit code | Meaning | What to do |
| --- | --- | --- |
| 2 | The term, course, class number or alert choice couldn't be read | Ask for it like "CSE 340, spring 2027", a 5-digit class number, or text, call or both |
| 3 | Not on ASU's class search for that term | Say so, and ask the student to check the course, number or term |
| 4 | ASU's class search isn't answering | If the student is waiting, say ASU's class search isn't responding and offer to try again in a few minutes. During a scheduled check, stay silent unless a `WARN` line printed |
| 5 | ASU answered in an unexpected way | Say the seat checker needs an update. Never guess seat numbers |

Without scheduled check-ins or calls (when this Skill runs outside Cubbie), run `check` whenever the student asks
and tell them to ask again later.

## Trying it without the network

Add `--sample` to any command to use the saved copy of the real Spring 2027 CSE 340 search in `assets/`. Sample
runs keep their own watch list and alert choice, and never touch the student's.

```bash
python3 scripts/seats.py search --term 2271 --course "CSE 340" --sample
```
