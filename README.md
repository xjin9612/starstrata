<p align="center">
  <img src="assets/icon.svg" width="128" height="128" alt="starstrata">
</p>

<h1 align="center">starstrata</h1>

<p align="center">English | <a href="README.zh-TW.md">繁體中文</a></p>

<p align="center">
  <a href="https://github.com/xjin9612/starstrata/actions/workflows/ci.yml"><img src="https://github.com/xjin9612/starstrata/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
</p>

starstrata collects GitHub trends every day and keeps them as a long-term history, so you can see what is rising, what is new, and which repos are suddenly taking off.

**Live dashboard: <https://xjin9612.github.io/starstrata/>** (interface in Traditional Chinese)

- Daily snapshots of GitHub Trending (overall plus six languages; daily, weekly and monthly), newly created repos from the Search API, and star counts of every tracked repo.
- A dashboard with trending cards (rank changes, 7-day sparklines, sort by stars gained), new entries, daily gainers, breakout alerts, the list of tracked repos, weekly and monthly reports, and per-repo star history with optional author notes. The interface, Telegram messages and reports are available in Traditional Chinese or English.
- A Telegram daily summary and breakout alerts; weekly and monthly reports with AI summaries.
- Repo descriptions translated into the language set in `translation.target` (Traditional Chinese here). Runs entirely on GitHub Actions: no server and no database host.

![Trending page](docs/screenshots/trending.png)

## Before you start

- **Cost**: standard GitHub-hosted runners are free in public repositories ([GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)). Keep your fork public.
- **API rate limit**: `GH_PAT` shares the 5,000 requests per hour limit with every other personal access token of your account ([rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)). One daily run uses up to about 1,000 REST requests, set by `backfill.daily_request_budget`, plus a few GraphQL and Search requests.
- **Gemini free tier**: Google uses content sent through unpaid services to improve its products, and human reviewers may read it ([Gemini API terms](https://aistudio.google.com/docs/terms)). starstrata only sends public repo descriptions and READMEs.

## Deploy your own

1. Fork this repo and keep it public. If the Actions tab asks you to enable workflows, enable them.
2. In `config.toml`, set `notify.site_url` to `https://<your-user>.github.io/<repo>/`, or to `""` to omit the dashboard button in Telegram and the page URL and image in the share preview tags. The footer link to the GitHub project is filled in automatically.
   Set `translation.target` to the language for repo descriptions (for example `ja`), or to `""` to skip translation. Set `i18n.locale` to `en` for an English dashboard, Telegram messages and reports.
   Empty `notes.toml`: its notes are the original author's personal comments.
3. Create repository secrets in Settings → Secrets and variables → Actions:

   | Name | Required | Purpose |
   |---|---|---|
   | `GH_PAT` | Yes | Fine-grained personal access token with no extra permissions; it can read public repos ([docs](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)) |
   | `TELEGRAM_BOT_TOKEN` | No | Telegram bot token; without it no messages are sent |
   | `TELEGRAM_CHAT_ID` | No | Chat that receives the messages; see [Telegram](#telegram) |
   | `GEMINI_API_KEY` | No | Description translation and report summaries (default provider). Create one for free in [Google AI Studio](https://aistudio.google.com). Without it, original descriptions are shown and reports have no summaries |
   | `ANTHROPIC_API_KEY` | No | Used instead of Gemini when `reports.provider` is `anthropic` |

   To send to one topic of a group, also create the repository variable `TELEGRAM_THREAD_ID` on the Variables tab of the same page; see [Telegram](#telegram). It is a variable, not a secret: GitHub hides secret values in logs, and a short number like `3` would hide every matching number in the logs.

4. In Settings → Pages → Build and deployment, set Source to **GitHub Actions**.
5. Run the first collection in Actions → `Daily` → Run workflow, or with `gh workflow run daily.yml -R <owner>/<repo>`.

The `Daily` workflow then runs every day at 21:17 UTC (05:17 in `Asia/Taipei`). GitHub may delay scheduled runs under high load. Each run:

- commits the day's snapshots to `data/` and reports to `reports/`;
- uploads the SQLite database to the release tagged `db`;
- sends the Telegram summary and alerts;
- deploys the dashboard to GitHub Pages.

The first day only sets the baseline. New entries, daily gainers and alerts appear from the second day.

## Telegram

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Add the bot to your group and send `/start@<bot username>` in the group. With privacy mode on, which is the default, a bot in a group only receives commands addressed to it ([privacy mode](https://core.telegram.org/bots/features#privacy-mode)).
3. Look up the chat ID: in the response, use `chat.id` of the message whose `chat.type` is `group` or `supergroup`.

   ```sh
   curl -s "https://api.telegram.org/bot<token>/getUpdates"
   ```

4. Save `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as secrets.

To send messages to one topic of a group with Topics enabled:

1. Turn on Topics in the group settings. A group with topics is a supergroup ([`is_forum`](https://core.telegram.org/bots/api#chat)), so a basic group becomes a supergroup and its chat ID changes. Update `TELEGRAM_CHAT_ID`.
2. Send `/start@<bot username>` in the target topic.
3. In the `getUpdates` response above, `message.message_thread_id` of that message is the topic ID. Save it as the repository variable `TELEGRAM_THREAD_ID`.

Without `TELEGRAM_THREAD_ID`, messages go to General. Any value that is not a positive integer makes `notify` exit with code `2` without sending anything.

The daily summary is sent silently. Breakout alerts notify with sound and include GitHub and dashboard buttons. If the group is later upgraded to a supergroup, its chat ID changes ([`migrate_to_chat_id`](https://core.telegram.org/bots/api#message)); update `TELEGRAM_CHAT_ID`.

## Configuration

All settings live in `config.toml`, with a comment on every key. Unknown keys and out-of-range values stop the run with exit code `2`.

| Section | Controls |
|---|---|
| `[schedule]` | Time zone used to decide the snapshot date |
| `[trending]` | Trending languages and periods |
| `[search]` | Which newly created repos to collect |
| `[tracking]` | When a repo becomes dormant or active again |
| `[alerts]` | Breakout threshold and cooldown |
| `[backfill]` | API requests per run for star history backfill |
| `[notify]` | Telegram summary length and dashboard URL |
| `[reports]` | AI provider, model, fallback models and how many repos to summarize |
| `[i18n]` | Language of the dashboard, Telegram messages and reports: `zh-TW` or `en` |
| `[translation]` | Target language for descriptions (`target`, empty to disable), batch size, per-run limit and request interval |

The run time is the `cron` line in `.github/workflows/daily.yml`.

`notes.toml` holds optional one-line author notes shown on repo pages and cards. Invalid entries stop `starstrata site` with exit code `2`; the format is in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) (Traditional Chinese).

## Running locally

Install [uv](https://docs.astral.sh/uv/) and run `uv sync`; details are in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) (Traditional Chinese). Then:

```sh
GH_PAT=<token> uv run starstrata collect    # fetch today's snapshots
uv run starstrata ingest                    # import snapshots into SQLite
GEMINI_API_KEY=<key> uv run starstrata translate
GH_PAT=<token> uv run starstrata backfill   # star history backfill
uv run starstrata report due                # weekly / monthly reports that are due
uv run starstrata site                      # build the dashboard into _site/
```

`notify` sends the Telegram messages and needs `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`; `TELEGRAM_THREAD_ID` is optional. `rebuild` recreates SQLite from all snapshots.

| Exit code | Meaning |
|---|---|
| `0` | Success |
| `1` | Partial failure: a data source failed, the API rate limit ran out, a report summary failed, or Telegram failed. Data already fetched is kept |
| `2` | Invalid configuration or a missing required environment variable; nothing was done |

`translate` exits with `0` unless the configuration is invalid; translation failures are logged as warnings.

## Troubleshooting and known limitations

- **Descriptions stay in English**: Gemini's free tier often answers `503 UNAVAILABLE` under load. Untranslated descriptions show the original text, and each daily run retries them. Descriptions that fail repeatedly move to the back of the queue. After you change `translation.target`, the new language starts from scratch at `translation.max_per_run` descriptions per run.
- **Trending stops updating**: Trending has no official API, so starstrata parses the HTML page. A GitHub page redesign can break it until the parser is updated; Search and repo snapshots keep working.
- **Star history looks lower than expected**: the star history API only counts users who still star the repo. Backfill is limited per run, so older repos fill in over several days.
- **A repo page shows "not found"**: repo pages use the current `owner/name`. After a rename, the old link stops working.
- **The workflow failed**: when Telegram is configured, the group receives a failure message with the run link. Snapshots collected before the failure are still committed.
- Archiving old snapshots is not implemented yet. Removing a day from `data/` removes that day from the database on the next rebuild.

## Development

Design, data formats and tests are documented in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) (Traditional Chinese). Requirements are in [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) (Traditional Chinese).

## License

[MIT](LICENSE)
