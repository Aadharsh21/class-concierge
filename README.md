# Seat Snipe

Plan your ASU semester and snipe the seats. Text Cubbie your major, get the classes you need with the best-rated
professors, and hear the second a seat opens.

Seat Snipe is a [Cubbie](https://cubbie.ai) Skill. It watches Arizona State University's public class search every
15 minutes and, the moment a seat opens in a class you're watching, alerts you the way you picked: a text with a
direct link to add it, a phone call, or both.

## Install

Text this link to Cubbie and reply "yes" when it asks:

```text
https://github.com/Aadharsh21/seat-snipe
```

## What it looks like

```text
You:    CSE 340 is full for spring 😭 tell me when a seat opens
Cubbie: CSE 340, Spring 2027: the hybrid section #21671 (Claveau) is full, 110/110.
        I'll check it every 15 min. When a seat opens, how should I tell you?
        1. Text with the add link
        2. Phone call
        3. Both: a call so you don't miss it, then the link by text
You:    3
Cubbie: Watching #21671. I'll call you, then text the link, the moment a seat opens.

        (later)

Cubbie: 📞 incoming call
Cubbie: Seat open in CSE 340 (#21671, Claveau)! 2 seats left.
        Add it now:
        https://go.oasis.asu.edu/addclass/?STRM=2271&ASU_CLASS_NBR=21671
```

## What it does

- Plans your next semester: tell it your major and semester, and it reads your official ASU major map to list
  the critical courses and elective slots for that term.
- Finds the easiest electives and the best-rated professors, using student reviews from Rate My Professors and
  live seat counts, then offers to watch any full sections. Already have favorite professors? Tell it, and it uses
  yours.
- Watches as many classes as you want, on every ASU campus and ASU Online.
- Checks every 15 minutes and stays quiet until something changes.
- Asks once how you want alerts (a text, a phone call, or both) and remembers it. Change it any time with
  "just text me" or "call me instead".
- Lets you pick no-call hours, like 11pm to 8am: an opening during those hours arrives as a text instead of a call.
- Puts the add link in the text, ready to tap.
- Send "send me a test alert" to see (and hear) exactly what an opening looks like.
- Knows about reserved seats: if the only open seats are held for a specific major or cohort, it tells you
  instead of sending you on a wild goose chase.
- Stops on its own after the last day to add the class, or when you say you got in.
- Never enrolls, drops or swaps anything for you. You add the class yourself in My ASU.
- Needs no sign-in and no keys. Your watch list stays on your own Cubbie drive.

## How it works

- `SKILL.md` tells Cubbie how to find a class, confirm with you, set a 15-minute check-in, and when to call or text.
- For planning, Cubbie reads your major map PDF from degrees.asu.edu, runs `scripts/seats.py compare` to see which
  candidate courses are offered next term and who teaches them, and looks those professors up on Rate My
  Professors with its own web search.
- `scripts/seats.py` reads ASU's public class search (Python standard library only), works out real open seats
  (total minus taken, minus seats reserved for other groups), and remembers what it last saw under
  `~/.config/seat-snipe/`, so you get one alert per opening instead of one every 15 minutes.

Try the script on its own with the saved sample data, no network needed:

```bash
python3 scripts/seats.py search --term "spring 2027" --course "CSE 340" --sample
python3 scripts/seats.py watch add --term 2271 --course "CSE 340" --class 21671 --sample
python3 scripts/seats.py check --sample
```

## Team

Built at DevHacks Fall 2026 for the Cubbie Hackathon by [@Aadharsh21](https://github.com/Aadharsh21) and
[@swayamvindo8](https://github.com/swayamvindo8).

## Credits

Seat data comes from ASU's public class search. Thanks to
[rajkumaar23/asu-course-notifier](https://github.com/rajkumaar23/asu-course-notifier) for documenting how the
class search API answers. Seat Snipe is a student project and isn't affiliated with Arizona State University.
