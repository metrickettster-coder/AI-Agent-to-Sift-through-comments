# titlesift

Reads a YouTube comment section and returns a ranked list of the titles people
named - movies, manhwa, webtoons, manga, books - with how many distinct
commenters mentioned each and an example comment.

## Status

Working end to end against live YouTube comments via the Data API v3.
Validated on 957 comments from `nlnH-_Etzbo` ("I Read 35 Manhwa
Recommendations. I Regret It.", teferi).

## Install

    pip install rapidfuzz wordfreq

## Run

    # offline, against a saved comment dump
    python3 -m titlesift.cli --file testdata/fixture.json

    # live (needs YOUTUBE_API_KEY)
    export YOUTUBE_API_KEY=...
    python3 -m titlesift.cli --video "https://www.youtube.com/watch?v=VIDEO_ID" --limit 500

    # machine-readable
    python3 -m titlesift.cli --file testdata/fixture.json --json

## Two kinds of comment section

Which video you point this at changes the problem completely.

**Recommendation videos** ("I read 35 manhwa...") - people list titles they
already know, casually and badly spelled, dozens of different ones. The job is
to normalise and rank. The catalogue path below does this.

**Clips, edits and Shorts** - almost nobody names anything. The comments are
reactions, plus a trickle of "what's this called?", plus a handful of people
answering. The title is what the audience *wants*, not what they say. On one
K-drama clip, 601 comments contained exactly 15 mentions of the show's name -
but the top answer had 335 likes, because everyone there wanted it.

Those need different machinery, and the tool runs both:

| | recommendation video | clip / edit |
|---|---|---|
| titles named | dozens | one |
| how it appears | casually, mid-sentence | as an answer to a question |
| signal | how many people said it | how hard it was upvoted |
| module | `extract.py` (catalogue) | `discover.py` (open vocabulary) |

`discover.py` needs no catalogue at all. It matches the *shapes* people use to
state a title - "Drama name : X", "it's called X", "X on Netflix", `"X"` - and
lets agreement between commenters plus likes decide what is real. That matters
because no catalogue you own will contain every title; on the manhwa video it
surfaced Hellper, Leviathan, Villains Are Destined to Die, Her Summon and
S-Class Hunters I Raised, none of which were in the seed.

Measured on the two videos above, top result only:

| video | answer | commenters | likes |
|---|---|---|---|
| bullying-drama Short | Teach You A Lesson | 30 | 1,661 |
| K-drama clip | May I Help You | 10 | 563 |

Both correct, both ranked first by a wide margin, with the catalogue path
finding nothing on either.

## Reading the video itself

On both of the first clip videos tested, the answer was in the video's own
metadata the whole time:

    "... #kdramaedit #teachyoualesson"          -> Teach You A Lesson
    "... | May i help you ? | MBC | Tiki Tiki"  -> May I Help You

`metadata.py` reads the title bar, description and hashtags. Hashtags arrive
glued together, so they are split with a frequency-weighted dynamic program
(`segment()`) rather than a dictionary - the point is to find titles nobody
has catalogued:

    #teachyoualesson  -> teach you a lesson      #allofusaredead -> all of us are dead
    #mayihelpyou      -> may i help you          #squidgame      -> squid game

Metadata alone is noisy - a clip's title bar is full of channel names and
broadcasters, and on one video "Tiki Tiki" and "MBC" outranked the real title.
The fix is not a better scorer, it is **corroboration**: `analyze.py` reads the
video three independent ways (metadata, comments, catalogue) and a candidate
appearing in two of them is treated as high confidence. "May i help you"
appears in both the title bar and the comments; "Tiki Tiki" appears in neither.

## Alternate names

One work carries many names, and left alone they compete and split the vote:

    Teach You A Lesson = Get Schooled = true education
    Attack on Titan    = Shingeki no Kyojin = AoT

`aliases.py` finds them two ways: people state them outright ("in Korean the
title is X", "aka X", "based on the manhwa X"), and the catalogue knows them
for works it has. Stated aliases attach to the video's answer instead of
competing with it.

One guard matters. "In Korean the title is X" only means X is *this video's*
work when the video has one subject. On a recommendation video listing 40
titles, the implicit referent is whatever that commenter was discussing - and
without the guard, Omniscient Reader's Viewpoint absorbed "The Boxer" and
"The S-classes That I Raised" as its own aliases. Stated aliases are therefore
only attached when the video resolves to three or fewer answers.

## Languages

People ask "what is this called?" in every language on YouTube, and answer in
the asker's. Every pattern in `discover.py` was taken from a corpus of 8,342
real comments across 16 videos, not translated by guesswork:

    La serie se llama Estamos muertos          -> Estamos muertos
    Movie ka naam Dhoom hai                    -> Dhoom
    Фильм называется Битва за Москву           -> Битва за Москву
    dizinin adı Çukur                          -> Çukur
    judulnya Pengabdi Setan                    -> Pengabdi Setan
    اسم المسلسل مسلسل الحب                      -> مسلسل الحب

18 languages have answer frames; `testdata/multilingual.json` pins 19 cases,
including the traps - "Movie ka naam kya hai" and "Как фильм называется?" are
questions, not answers, and must yield nothing.

**97% of that corpus was Latin script.** Romanised Hindi and Turkish matter as
much as Cyrillic or Arabic, which is easy to miss if you plan for scripts
rather than languages.

`demand()` counts the asking separately, in the same 18 languages. On a clip
that is the market signal, and it works even when nobody answers: one video in
the corpus had 28 people asking and no answer available anywhere in the thread.

## The sweep: 530 videos, 130,949 comments

Randomly selected clip and Short videos across 25 queries and several
languages, 150+ comments each.

| | | |
|---|---|---|
| videos where someone asked what it is | 397 | **75%** |
| people asking, in total | 2,623 | |
| high-confidence answer | 160 | 30% |
| medium | 161 | 30% |
| low - metadata-only guess, treat as noise | 206 | 39% |

Of the 397 videos where people were asking, 138 (35%) got a high-confidence
answer and 266 (67%) got high or medium. **259 videos had people asking and
no answer this tool would stand behind.** That gap is the opportunity.

A hand audit of 60 high-confidence answers put roughly 90% correct - Vincenzo,
My Demon, F4 Thailand, Sakamoto Days, Mushoku Tensei, All of Us Are Dead,
Business Proposal and so on, each matching its video.

**Do not read "an answer was found on 99% of videos" off this data.** That
number is real and meaningless: 206 of those are low-confidence metadata-only
guesses like "school", "k drama status" and "Just sibling things". The
confidence tiers exist precisely so that number can be ignored.

Two bugs the sweep exposed, both in the audit rather than the totals:

- **"la novela" and "esta novela" came back as high-confidence titles** on
  Spanish clips. They mean "the soap opera" and "this soap opera" - media
  words wearing an article. `novela` was already junk-listed; the article
  walked it straight past the check. Determiners in 8 languages are now
  stripped before the junk test.
- `"#f4 Thailand"` kept its hash. Slots are now stripped of leading `#`/`@`.

Metadata was the single largest evidence source, contributing to 306 answers -
more than comments (197) or the catalogue (153). Reading the video's own title
and hashtags is the cheapest win in the whole pipeline.

## Honest results on 14 random videos (earlier, smaller run)

The two videos this was first built against were ones where the audience was
actively asking. That flatters it. Across 14 *randomly selected* clip videos:

| | |
|---|---|
| videos yielding an answer | 4 of 14 |
| of those, clearly correct | 3 |
| people asking across all 14 | 63 |

Most clips produce nothing, which is the correct behaviour - most clips have
no answer in the comments to find. Two things had to be fixed to get there,
both found only by running the random sample:

- **A quote is not an answer.** The quoted frame fired 86 times and produced
  "Yes it is" (60,628 likes), "Chill Out" and "Disclaimer: This". People quote
  dialogue, lyrics and each other. Quotes now support an answer but can never
  be one (`STRONG_FRAMES` vs `WEAK_FRAMES`).
- **"His name is Adi" is a character, not a title.** Bare "name is X" pulled in
  character names that outranked real answers. A negative lookbehind did not
  work, because `\s*` lets the match start on the space and moves the
  lookbehind past the possessive. Every genuine hit carried a media word or
  "the", so the frame now requires that anchor.

## How it works

The hard part is not finding titles, it is deciding that a string *is* one.
Four stages:

1. **Candidate generation** (`extract.py`) - quoted spans, recommendation cues
   ("read X", "check out X"), an exact n-gram sweep against the alias index,
   bare uppercase acronyms, and a fuzzy sweep for misspellings.
2. **Resolution** - exact alias hit, then acronym expansion, then fuzzy match.
   Misspellings ("sollo levelling") are found by blocking on shared character
   trigrams and scoring only the few dozen plausible aliases, which keeps the
   step viable against a full-size catalogue.
3. **Disambiguation** (`disambiguate.py`) - the precision layer. Scores how
   ordinary the commenter's wording is using English word frequency, then
   requires contextual evidence for ambiguous ones: capitalisation, a media
   word, a recommendation cue, paired quotes, a year, an author, or another
   confidently-resolved title beside it. Penalises compound continuations
   ("monster energy", "dune buggy") but only when the next word is noun-like,
   so "Interstellar made me cry" survives.
4. **Ranking** (`rank.py`) - groups by canonical title, ranks by *distinct
   commenters* rather than raw mentions so one person repeating themselves
   cannot dominate, and keeps the spelling variants people actually used.

## Measured

Two suites, 50 comments, 44 expected mentions. The adversarial suite was
written specifically to break the matcher, and did - single-common-word
titles scored 0.417 precision before the disambiguation layer.

| suite | precision | recall | f1 |
|---|---|---|---|
| fixture (40 comments, incl. 10 no-title traps) | 1.000 | 1.000 | 1.000 |
| adversarial (25 comments, common-word titles) | 0.900 | 0.900 | 0.900 |
| **combined** | **0.977** | **0.977** | **0.977** |

Throughput: ~300-1500 comments/sec single-threaded.

**These fixtures are hand-written, not real YouTube comments.** They are a
development harness. See "On live data" for what they failed to predict.

## On live data

957 comments, one video. The fixtures scored 0.977 throughout every stage
below and never moved - they could not see any of this.

| stage | mentions | titles | note |
|---|---|---|---|
| first live run | 236 | 22 | ~20 false *Hunter x Hunter* commenters |
| after fuzzy fix | 206 | 18 | audited clean |
| after catalogue expansion (47 -> 77 titles) | 376 | 40 | logic unchanged |

**What live data exposed that fixtures could not.** Fuzzy matching used
rapidfuzz `WRatio`, which scores substrings highly. Real comment sections are
full of titles that are *not in the catalogue*, and `WRatio` dragged them onto
whichever known title looked closest:

- `"SSS-Class Suicide Hunter"` -> *Hunter x Hunter* (95) - 18 false mentions
- `"hero killer"` -> *The Name of the Wind*, via the alias `kingkiller` (90)
- `"Monsters We Make"` -> *Monster* (93)

Plain `ratio` scores every genuine misspelling 93-100 and every one of those
false hits below 76 - a clean, wide gap. Two changes followed:

1. `WRatio` -> `ratio` (`FUZZY_SCORER` in `extract.py`).
2. Fuzzy matching is refused when the commenter's own wording is ordinary
   English, because `"monsters"` is not a typo for *Monster*.

The fixtures did not regress, which is the point: this failure mode only
exists when the catalogue is smaller than reality, and a hand-written fixture
never is.

**Coverage is now the binding constraint, not matching.** Expanding the seed
from 47 to 77 titles took the same 957 comments from 206 mentions to 376 -
an 82% gain with no change to the matching logic. Titles the 47-title seed
missed entirely included One Piece (25 commenters), The Boxer (22),
Ember Knight (21) and Legend of the Northern Blade (20). A production
catalogue is worth far more here than any further tuning.

### Known failures

Both remaining errors are the same title, and both are genuinely ambiguous:

- `"bluelock my calendar for next week"` - false positive. A pun on "block".
- `"blue lock made me care about soccer"` - miss. Lowercase, no cue; correct
  only if you know Blue Lock is about football.

This is the intended failure shape: the lexical layer declines rather than
guesses. `llm.py` drafts the escalation path that settles these.

## Limitations

- `discover.py`'s frames are English only. A Russian-language statement of the
  same title on the Short was not picked up.
- The catalogue path is still capped by catalogue size (see below).
- `llm.py` is unexercised.

## Title-database confirmation (report v3)

`resolve.py` checks every report answer against AniList, English Wikipedia and
MangaDex, all free and keyless. A confirmed answer is shown under the work's
official name, every spelling and alternate name that resolves to the same
work is counted in one row, and the row gains a type and year ("K-drama,
2020"). The video's own search term and title say what the answer should be,
so a name the databases only know as something else is dropped: "Monster"
under a telenovela clip is a Japanese manga everywhere it is known. Alternates
are only shown if the database lists them or they are respellings, which
removes "saitama" from One Punch Man.

    python3 fetch_titledb.py anilist     # fills dbcache/, ~2s per title
    python3 fetch_titledb.py wikipedia   # exact article titles, 50 per request
    python3 fetch_titledb.py search      # leftovers only; slow
    python3 make_report.py               # offline rebuild
    python3 run_suites.py                # regression suites

Wikipedia rate-limits this environment's shared IP hard, which is why exact
titles are fetched in bulk and search is kept for the leftovers.

## Blockers

1. **No title database in the matcher.** AniList, MangaDex and Wikipedia are
   now reachable and the report uses them (above), but `gazetteer.py` still
   ships a 77-title hand-built seed for per-comment matching. This is now the main thing holding
   accuracy back - see "On live data". Production loads a real catalogue
   through `Gazetteer.from_json`; the trigram blocking was built for that
   scale.
2. **No LLM key**, so `llm.py` is unexercised.

Note: `www.youtube.com` itself is blocked here (403 on CONNECT), but
`www.googleapis.com` is reachable, so the Data API path works and is what
the tool uses. That is also the only terms-compliant route for a product.
