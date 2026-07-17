# Amazon UK Job Watch

A small bot that checks [jobsatamazon.co.uk](https://www.jobsatamazon.co.uk/app#/jobSearch)
every 5 minutes and sends a Discord ping (with `@everyone`) the moment a new
job appears near East London. It runs for free on GitHub Actions — no computer
needs to be on.

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
