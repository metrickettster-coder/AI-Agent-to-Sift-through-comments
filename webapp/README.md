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
