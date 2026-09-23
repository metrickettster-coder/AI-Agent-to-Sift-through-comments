"""Render the Product B report as a standalone page.

The buyer for this report is a licensing or acquisitions team, so the page
leads with the ranking and keeps the method underneath it, where someone who
wants to argue with a number can find how it was made.
"""
from __future__ import annotations

import html
import json

LANG_NAME = {
    "en/other": "English / Latin script", "ru": "Russian", "ar": "Arabic",
    "es": "Spanish", "pt": "Portuguese", "hi": "Hindi", "bn": "Bengali",
    "th": "Thai", "ko": "Korean", "ja": "Japanese", "zh": "Chinese",
    "tr": "Turkish", "id": "Indonesian", "fr": "French", "de": "German",
    "vi": "Vietnamese", "it": "Italian",
}


def lang(code: str) -> str:
    return LANG_NAME.get(code, code)


def _rows(titles: list[dict], limit: int, start: int = 1) -> str:
    out = []
    for i, t in enumerate(titles[:limit], start):
        langs = [lang(k) for k, v in sorted(t["languages"].items(),
                                            key=lambda kv: -kv[1])
                 if k != "en/other" and v >= 3][:3]
        if t.get("kind"):
            what = t["kind"] + (f', {t["year"]}' if t.get("year") else "")
        else:
            what = max(t["genres"], key=t["genres"].get) if t["genres"] else "—"
        alts = t.get("alternates") or []
        alt_html = ""
        if alts:
            alt_html = ('<span class="alt">also called '
                        + html.escape(", ".join(a for a, *_ in
                                                (x if isinstance(x, (list, tuple))
                                                 else (x,) for x in alts))[:90])
                        + "</span>")
        name = html.escape(t["title"])
        if t.get("url"):
            name = (f'<a href="{html.escape(t["url"])}" target="_blank" '
                    f'rel="noopener">{name}</a>')
        out.append(
            f'<tr><td class="rank">{i}</td>'
            f'<td class="name">{name}{alt_html}</td>'
            f'<td class="num">{t["videos"]}</td>'
            f'<td class="num">{t["commenters"]}</td>'
            f'<td class="num">{t["demand"]}</td>'
            f'<td class="tag">{html.escape(what)}</td>'
            f'<td class="langs">{html.escape(", ".join(langs)) or "—"}</td></tr>')
    return "\n".join(out)


def _bars(counts: dict, total: int, unit: str, limit: int = 10) -> str:
    if not counts:
        return ""
    top = max(counts.values())
    out = []
    for k, v in list(counts.items())[:limit]:
        pct = round(100 * v / top)
        out.append(f'<div class="bar"><span class="bl">{html.escape(k)}</span>'
                   f'<span class="bt"><i style="width:{pct}%"></i></span>'
                   f'<span class="bv">{v:,} {unit}</span></div>')
    return "\n".join(out)


def render(rep: dict, top: int = 40) -> str:
    gap_pct = (round(100 * rep["demand_gap"] / rep["videos_with_demand"])
               if rep["videos_with_demand"] else 0)
    ask_pct = (round(100 * rep["videos_with_demand"] / rep["videos"])
               if rep["videos"] else 0)
    ne = rep.get("non_english_comments", 0)
    reach = {lang(k): v for k, v in (rep.get("language_reach") or {}).items()}
    gaps = "\n".join(
        f'<li><span class="gt">{html.escape(g["title"][:88].rstrip("# "))}</span>'
        f'<span class="gn">{g["asked"]} asking</span></li>'
        for g in rep.get("gap_examples", []))

    return TEMPLATE.format(
        videos=f'{rep["videos"]:,}',
        comments=f'{rep["comments"]:,}',
        asking=f'{rep["people_asking"]:,}',
        titles=f'{rep["distinct_titles"]:,}',
        ask_pct=ask_pct,
        gap=f'{rep["demand_gap"]:,}',
        gap_pct=gap_pct,
        answered=f'{rep["videos_answered_high"]:,}',
        ne=f'{ne:,}',
        rows=_rows(rep["titles"], top),
        unconfirmed_rows=_rows(rep.get("unconfirmed", []), 15),
        n_unconfirmed=len(rep.get("unconfirmed", [])),
        n_rejected=len(rep.get("rejected", [])),
        kind_bars=_bars(rep.get("by_kind") or {}, rep["videos"], "videos"),
        shown=min(top, len(rep["titles"])),
        genre_bars=_bars(rep["by_genre"], rep["videos"], "videos"),
        lang_bars=_bars(reach, rep["videos"], "videos"),
        gaps=gaps,
    )


TEMPLATE = """<title>Title Demand Report</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {{
  --bg: #fbfaf7; --panel: #ffffff; --ink: #14110d; --muted: #6b6559;
  --line: #e6e1d6; --accent: #b4441f; --accent-soft: #f3e3dc; --pos: #2f6f4f;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #14120f; --panel: #1c1a16; --ink: #f2eee6; --muted: #9c9689;
    --line: #2e2a24; --accent: #e8794c; --accent-soft: #3a2419; --pos: #63b48c;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #14120f; --panel: #1c1a16; --ink: #f2eee6; --muted: #9c9689;
  --line: #2e2a24; --accent: #e8794c; --accent-soft: #3a2419; --pos: #63b48c;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--ink);
  font: 400 16px/1.55 Inter, system-ui, -apple-system, sans-serif;
  -webkit-font-smoothing: antialiased;
}}
.wrap {{ max-width: 960px; margin: 0 auto; padding: 48px 16px 80px; }}
.eyebrow {{
  font-size: 12px; letter-spacing: .14em; text-transform: uppercase;
  color: var(--accent); font-weight: 600; margin: 0 0 12px;
}}
h1 {{
  font-family: "Instrument Serif", Georgia, serif; font-weight: 400;
  font-size: clamp(34px, 7vw, 56px); line-height: 1.05; margin: 0 0 16px;
  letter-spacing: -.01em;
}}
.lede {{ font-size: 18px; color: var(--muted); margin: 0 0 40px; max-width: 62ch; }}
h2 {{
  font-family: "Instrument Serif", Georgia, serif; font-weight: 400;
  font-size: 28px; margin: 56px 0 8px; letter-spacing: -.01em;
}}
h2 + p {{ color: var(--muted); margin: 0 0 20px; max-width: 62ch; }}
.stats {{
  display: grid; gap: 1px; background: var(--line);
  border: 1px solid var(--line); border-radius: 12px; overflow: hidden;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
}}
.stat {{ background: var(--panel); padding: 18px 20px; }}
.stat b {{
  display: block; font-family: "Instrument Serif", Georgia, serif;
  font-weight: 400; font-size: 32px; line-height: 1.1; letter-spacing: -.02em;
}}
.stat span {{ font-size: 13px; color: var(--muted); }}
.panel {{
  background: var(--panel); border: 1px solid var(--line);
  border-radius: 12px; overflow: hidden;
}}
.scroll {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
table {{ border-collapse: collapse; width: 100%; min-width: 660px; font-size: 14px; }}
th {{
  text-align: left; font-weight: 600; font-size: 11px; letter-spacing: .08em;
  text-transform: uppercase; color: var(--muted); padding: 14px 12px;
  border-bottom: 1px solid var(--line); white-space: nowrap;
}}
td {{ padding: 12px; border-bottom: 1px solid var(--line); vertical-align: top; }}
tr:last-child td {{ border-bottom: 0; }}
.rank {{ color: var(--muted); width: 34px; font-variant-numeric: tabular-nums; }}
.name {{ font-weight: 500; }}
.name a {{ color: inherit; text-decoration: none; border-bottom: 1px solid var(--line); }}
.name a:hover {{ border-bottom-color: var(--accent); }}
.alt {{ display: block; font-weight: 400; font-size: 12px; color: var(--muted); margin-top: 2px; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.tag {{ color: var(--muted); white-space: nowrap; }}
.langs {{ color: var(--muted); font-size: 13px; }}
.bar {{ display: flex; align-items: center; gap: 12px; padding: 7px 0; font-size: 14px; }}
.bl {{ flex: 0 0 150px; }}
.bt {{ flex: 1; height: 8px; background: var(--accent-soft); border-radius: 99px; overflow: hidden; min-width: 60px; }}
.bt i {{ display: block; height: 100%; background: var(--accent); border-radius: 99px; }}
.bv {{ flex: 0 0 auto; color: var(--muted); font-size: 13px; font-variant-numeric: tabular-nums; }}
ul.gap {{ list-style: none; margin: 0; padding: 0; }}
ul.gap li {{
  display: flex; gap: 16px; justify-content: space-between; align-items: baseline;
  padding: 11px 16px; border-bottom: 1px solid var(--line); font-size: 14px;
}}
ul.gap li:last-child {{ border-bottom: 0; }}
.gn {{ color: var(--accent); font-weight: 500; white-space: nowrap; font-variant-numeric: tabular-nums; }}
.pull {{
  border-left: 3px solid var(--accent); padding: 4px 0 4px 18px;
  margin: 28px 0; font-family: "Instrument Serif", Georgia, serif;
  font-size: 22px; line-height: 1.4;
}}
.method {{ font-size: 14px; color: var(--muted); }}
.method li {{ margin-bottom: 8px; }}
footer {{ margin-top: 56px; padding-top: 20px; border-top: 1px solid var(--line);
  font-size: 13px; color: var(--muted); }}
@media (max-width: 560px) {{
  .wrap {{ padding: 32px 16px 60px; }}
  .bl {{ flex: 0 0 105px; font-size: 13px; }}
}}
</style>

<div class="wrap">
  <p class="eyebrow">Title demand intelligence</p>
  <h1>What audiences are asking to identify</h1>
  <p class="lede">Every day people watch a clip they like and ask, in the
  comments, what it was. Those questions are a demand signal nobody is
  reading. This report reads them: {comments} comments under {videos} videos,
  turned into a ranking of the titles audiences are chasing.</p>

  <div class="stats">
    <div class="stat"><b>{videos}</b><span>videos scanned</span></div>
    <div class="stat"><b>{comments}</b><span>comments read</span></div>
    <div class="stat"><b>{asking}</b><span>people asking what it is</span></div>
    <div class="stat"><b>{titles}</b><span>titles confirmed in a title database</span></div>
    <div class="stat"><b>{ask_pct}%</b><span>of videos had someone asking</span></div>
    <div class="stat"><b>{ne}</b><span>comments not in English</span></div>
  </div>

  <h2>The ranking</h2>
  <p>Every title here was checked against AniList, Wikipedia or MangaDex and
  is shown under its official name, with its alternate names folded into one
  row. Ordered by how many separate videos a title surfaced under, because
  one viral clip is a hit and ten clips is a demand curve. "People naming it"
  counts distinct commenters, not repeat posts. The last column is the
  non-English comment traffic on those videos, not the language each
  question was asked in.</p>
  <div class="panel scroll">
  <table>
    <thead><tr>
      <th>#</th><th>Title</th><th class="num">Videos</th>
      <th class="num">People naming it</th><th class="num">People asking</th>
      <th>What it is</th><th>Other comment languages</th>
    </tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
  <p class="method">Top {shown} of {titles} confirmed titles.</p>

  <h2>Named by viewers, not in any database</h2>
  <p>{n_unconfirmed} more titles were named by commenters but could not be
  found in AniList, Wikipedia or MangaDex. Some are misreadings; others are
  new or niche releases no database has caught up with yet, which is where an
  early signal is worth the most. The strongest are below.</p>
  <div class="panel scroll">
  <table>
    <thead><tr>
      <th>#</th><th>Title as written</th><th class="num">Videos</th>
      <th class="num">People naming it</th><th class="num">People asking</th>
      <th>Found under</th><th>Other comment languages</th>
    </tr></thead>
    <tbody>
{unconfirmed_rows}
    </tbody>
  </table>
  </div>

  <h2>What audiences are chasing</h2>
  <p>Videos per kind of title, for the confirmed titles above. The kind comes
  from the title database, not from how the video was found.</p>
  <div class="panel" style="padding:14px 18px">
{kind_bars}
  </div>

  <h2>Languages the asking happens in</h2>
  <p>Counted as videos carrying real comment volume in each language, not raw
  comment totals, so a single enormous video cannot invent a market.
</p>
  <div class="panel" style="padding:14px 18px">
{lang_bars}
  </div>

  <h2>The unanswered demand</h2>
  <p>{gap} of the {videos} videos had people asking and no confident answer
  anywhere in the thread, {gap_pct}% of all videos with demand. These are
  audiences actively trying to find something and failing.</p>
  <div class="panel"><ul class="gap">
{gaps}
  </ul></div>

  <p class="pull">A title nobody can name is a title nobody can sell. The
  questions are already public; the count is the product.</p>

  <h2>How this was measured</h2>
  <ul class="method">
    <li>Videos sampled through the YouTube Data API across clip, edit and
    recommendation searches in several languages, then every available comment
    thread read for each one.</li>
    <li>A title is counted once per distinct commenter, so a thread where one
    person repeats a name forty times counts once.</li>
    <li>Answers are graded. A title corroborated by both the video's own
    metadata and its comments is treated as confident; a single commenter's
    guess is kept but not counted toward the confident tier, and only
    confident and corroborated answers enter the ranking above.</li>
    <li>Every answer is looked up in free title databases (AniList for anime,
    manga and manhwa; Wikipedia for dramas and films; MangaDex as a
    fallback). A confirmed title is shown under its official name, and every
    spelling and alternate name that resolves to the same work is counted in
    one row.</li>
    <li>The lookup also has to fit the video: a name the databases only know
    as a Japanese manga is not accepted as the answer under a telenovela clip.
    {n_rejected} answers were dropped this way.</li>
    <li>An alternate name is shown only if the database lists it or it is a
    respelling of the title, so a character's name is never presented as
    another name for the show.</li>
    <li>Known limits: the sample is search-driven rather than random, and
    database coverage of new dramas is thinner than of anime, so some real
    titles sit in the unconfirmed list until the databases catch up.</li>
  </ul>

  <footer>Generated by titlesift from public YouTube comment data.</footer>
</div>
"""
