# Job Watch

Two small bots that run for free on GitHub Actions (no computer needs to be
on) and ping Discord when a matching job appears:

1. **Amazon watcher** ([`check_jobs.py`](check_jobs.py)) — watches
   jobsatamazon.co.uk and pings within minutes; details below.
2. **All-jobs watcher** ([`alljobs.py`](alljobs.py)) — the wide net. Uses the
   free [Adzuna](https://developer.adzuna.com) API to watch entry-level,
   no-CV, no-driving jobs (warehouse, retail, hospitality, cleaning) across
   every employer and agency near Ilford, checked every ~15 minutes. Edit the
   **CONFIG** block at the top of `alljobs.py` to change the area
   (`WHERE`/`DISTANCE_MILES`), the `CATEGORIES`, or the words that filter jobs
   out (`EXCLUDE_TITLE_WORDS`). It posts to a separate Discord channel
   (secret `DISCORD_WEBHOOK_JOBS`) and needs `ADZUNA_APP_ID` / `ADZUNA_APP_KEY`
   secrets. Adzuna re-indexes other boards so its data lags a few hours — fine
   for these longer-lived roles.

## Amazon UK Job Watch

A small bot that checks [jobsatamazon.co.uk](https://www.jobsatamazon.co.uk/app#/jobSearch)
every 5 minutes and sends a Discord ping (with `@everyone`) the moment a new
job appears near East London. It runs for free on GitHub Actions — no computer
needs to be on.

Amazon blocks requests coming straight from cloud servers (like GitHub's),
so the bot forwards its request through a free public relay
(`proxy.cors.sh`) whose address Amazon accepts. If that relay ever stops
working, the bot automatically tries the site directly and then a headless
browser, and it will post a ⚠️ warning in Discord if every method fails 4
checks in a row (so a silent breakage can't go unnoticed).

## Change what you get alerted about

Edit the **CONFIG** block at the top of [`check_jobs.py`](check_jobs.py)
(tap the pencil icon on GitHub, edit, then "Commit changes"):

- `LOCATION_TERMS` — list of place words to match (title, city, postcode or
  site name). Make it `[]` to get **every** UK job.
- `TITLE_KEYWORDS` — words the job title must contain. `[]` = all roles.
- `PING_EVERYONE` — set to `False` to stop the `@everyone` pings.
- `MAX_ALERTS_PER_RUN` — max individual alerts per check; the rest are
  summarised in one message.

## Is it still alive?

- **Actions tab** on GitHub → runs should appear every ~5–10 minutes with a
  green tick.
- The bot commits its memory file `state.json` at least once a day
  ("Update job memory" commits) — recent commits mean it's healthy.
- If the jobs site can't be read 4 checks in a row, the bot posts a ⚠️
  warning in Discord, and a ✅ message when it recovers.

## Silence is normal

Amazon UK often lists **zero** warehouse jobs for days or weeks, then drops a
batch that fills within hours. No messages usually means no new jobs — that's
exactly why this bot exists: when a batch lands, you'll know within minutes.
