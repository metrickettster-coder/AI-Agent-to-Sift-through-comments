# TitleSift web page

Paste a YouTube clip or Short, get the name of the manhwa, manhua, manga,
webtoon or anime, the comment it came from, and a link to it on AniList,
MangaDex or Wikipedia.

## Run it locally

    pip install -r requirements.txt
    YOUTUBE_API_KEY=... python3 webapp/server.py      # http://localhost:7860

The YouTube key is read on the server only. The page never receives it.

## How an answer is chosen (`titlesift/finder.py`)

1. **Someone in the comments named it** (the reply-tree reader and every
   comment frame in `discover.py`). Shown with the comment, and the question
   it answered when it was a reply.
2. **The uploader labelled it** in the title or description
   ("Title : War of Extinction", "Manhwa Name : A Life-Changing Turn").
   Recap channels do this constantly and the metadata layer used to keep the
   label as part of the name and rank it under the clickbait title.
3. **Something else the uploader wrote**, only if AniList or MangaDex knows
   it. Unconfirmed, a Short's title bar is clickbait far more often than a
   name ("Bro Sacrificed His Own Kidney To Kill A God").
4. Otherwise: "Nobody's named it yet", with how many people are asking.

Every answer is checked against AniList (and MangaDex, and Wikipedia for live
action). A confirmed answer shows the database's name for the work, unless it
shares nothing with what people called it (AniList files some manhwa under
romaji only).

## Put it online for free

Any host that runs a Dockerfile works. Set one secret, `YOUTUBE_API_KEY`.

**Render** (from a GitHub repo): render.com, sign in with GitHub, New, Web
Service, pick the repo, it finds the Dockerfile, choose the Free instance,
add the environment variable `YOUTUBE_API_KEY`, Create. Free services sleep
after 15 minutes idle; the first visit after that takes about a minute.

**Hugging Face Spaces**: new Space, SDK "Docker", upload the files, add
`YOUTUBE_API_KEY` under Settings, Secrets. Port 7860 is already set.

## Feedback

Every answer asks "Was this right?", and the footer has a Send feedback
form. Each message is written to the service log as a line starting with
`FEEDBACK` and, except a plain "yes, it was right", emailed through the
Formspree form in `FORMSPREE_URL` (free plan: 50 a month). Set `GITHUB_TOKEN` (a fine-grained token limited to this
repository with Issues: read and write) and every message except a plain
"yes, it was right" also becomes a GitHub issue titled `[feedback] ...`.
`FEEDBACK_REPO` overrides the repository.

## "Would you pay?" and tips

After someone says an answer was right, the page asks once per browser
whether they'd pay: only if free, $0.99, $1.99 or $2.99 a month. One vote per
visitor a day counts. See the totals at `/votes`. They live in memory, and
with `GITHUB_TOKEN` set they are also kept in one issue titled
`[price-votes] ...`, so updates don't reset them. Each vote is also logged as
a `PRICEVOTE` line.

Set `KOFI_URL` (for example `https://ko-fi.com/yourname`) and a "Tip on
Ko-fi" link appears in the footer and after a vote. Without it, no link shows.

## Names from visitors

"I know the name" (when nothing was found) and "What's the right name?"
(after "No") save the typed name against the video; "Yes" counts as agreeing
with the answer shown. Each person counts once per name per video. A name is
shown to later visitors once AniList, Wikipedia or MangaDex knows it, or two
different people gave it; agreeing with an answer adds a "Confirmed by N
TitleSift users" line. `/names` lists everything given, newest first.

Names live in memory. With `GITHUB_TOKEN` set (fine-grained, this repo only,
Contents and Issues read and write) they are also saved to `community.json`
on the `titlesift-data` branch every 30 seconds after a change, and loaded
again on start. Render only deploys `main`, so these saves never redeploy.
Only the video id, the name, a count and dates are saved, never who gave it.

A name no database knew is tried again as its separate guesses ("X or Y")
and with a lowercase l read as a capital I. Saved names are rechecked this
way each time the server starts.

## Visit counts

`/stats` shows daily totals (US Pacific days): visitors, first-time
visitors, page views, lookups, answers found, names added, and which
language visitors see. The page tells the server once per load whether it
is this browser's first visit today (remembered in local storage as
`titlesift.seen`); nothing about who visited is kept. With `GITHUB_TOKEN`
set the totals are saved to `stats.json` on the `titlesift-data` branch.

## Languages and light/dark

The page speaks 25 languages (see `langs` in `static/i18n.js`); Arabic,
Persian, Urdu and Hebrew read right to left.
English lives in `static/i18n.js`; each other language is
`static/i18n/<code>.json` with the same keys, and a missing key falls back to
English. The phone's language is picked unless the visitor chose one from the
menu. The server still answers in English: the page matches its messages and
answer notes against the English strings (`srv.*`, `note.*`) and rebuilds them
in the chosen language, so a new message on the server needs a new key there
too. Privacy and Terms stay in English.

The moon/sun button overrides the phone's light or dark setting and is
remembered per browser.

## Phone extras

`manifest.json` makes the page installable ("Add to Home screen"). On
Android, once installed, TitleSift appears in YouTube's Share menu and opens
straight onto the answer (`share_target`). iPhone has no share target for web
apps, so there it is copy link, then Paste.

## Limits

- YouTube's free allowance is 10,000 units a day; a lookup costs about 8, so
  roughly 1,200 new videos a day. Answers are kept 6 hours per video and each
  visitor gets 30 lookups an hour (`LOOKUPS_PER_HOUR`).
- Answers and the visitor counts live in memory and reset on restart.
