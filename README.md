# Daily UK Legal PR Coverage Digest

This repository contains a small Python 3.10+ tool that emails a daily UK legal PR coverage digest. It pulls recent items from a FreshRSS server using its Google Reader–compatible API, filters the headlines for configured law-firm clients, tags the sentiment, and sends one HTML email with the coverage summary.

## FreshRSS prerequisites

If you already run FreshRSS, skim the checklist below to make sure the API user is prepared. If you are starting from scratch, follow the mini-setup guide first and then come back to the checklist.

### Quick-start guide (for new FreshRSS users)
1. **Install FreshRSS.** You can use the official Docker image (`freshrss/freshrss`) or follow the [FreshRSS installation guide](https://freshrss.github.io/FreshRSS/en/admins/02_Installation.html) for a regular LAMP/LEMP stack. During setup you will create an admin account.
2. **Create a dedicated user account for the digest (optional but recommended).** Sign in as the admin, go to `Administration → Users`, and add a user such as `digest-bot`.
3. **Subscribe to your sources.** While logged in as the digest user, add RSS feeds manually or import an OPML file via `Profile → Import/Export → Import`. These feeds are what the script will search for the law-firm names.
4. **Enable the Google Reader API.** Still logged in, visit `Profile → Reading → API` (or `Administration → System → Authentication` in older versions) and check “Enable Google Reader API”. Save the change.
5. **Generate an API password.** From the same API page, click “(Re)generate API password” and copy the resulting string. This is *not* your normal login password; it is the password you will place in `.env`.
6. **Verify the API works (optional but useful).** Run a quick curl command replacing the placeholders with your server URL, username, and API password:
   ```bash
   curl -u "USERNAME:API_PASSWORD" \
     "https://your-freshrss.example/api/greader.php/reader/api/0/stream/contents/reading-list?n=1"
   ```
   A successful response returns JSON containing the latest items. If you see `401 Unauthorized`, double-check the username/API password combination and that the Google Reader API is enabled.

### Checklist (for existing FreshRSS installations)
- Google Reader API enabled for the user account that will run the digest.
- API password generated for that account (Admin → API).
- The account is subscribed to the feeds you want to monitor (import OPML if needed).
- You know the base URL (e.g., `https://news.example.com`) and it is reachable from the machine that will run the digest.

## Setup
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Copy the environment template and fill in your settings:
   ```bash
   cp .env.example .env
   ```
3. Edit `.env` with your FreshRSS base URL, credentials, SMTP settings, and any digest customisation.
4. Run the digest:
   ```bash
   python pr_daily_digest.py
   ```

## Scheduling

### Cron (Linux/macOS)
Schedule the digest to run daily at 08:00 Europe/London time:
```cron
0 8 * * * cd /path/to/News-Coverage && /usr/bin/env python3 pr_daily_digest.py >> digest.log 2>&1
```
Ensure the environment variables in `.env` are loaded (e.g., by using a wrapper script or `direnv`).

### Windows Task Scheduler
1. Create a new *Basic Task*.
2. Trigger: Daily at 08:00.
3. Action: *Start a Program* → Program/script: `python`, Add arguments: `C:\path\to\pr_daily_digest.py`.
4. Start in: repository directory path.
5. Ensure the `.env` file is accessible to the script (e.g., run via a batch file that sets `PYTHONPATH` and working directory).

## Customising the digest
- Edit `CLIENTS` in `pr_daily_digest.py` to add or adjust law-firm aliases and context keywords.
- Add comma-separated terms to `BLOCKLIST_PHRASES` in `.env` to filter out irrelevant headlines (e.g., `football,sports`).
- Tweak `LOOKBACK_HOURS` or `MAX_ITEMS` in `.env` to control the FreshRSS fetch window.
- Optional CLI flags:
  - `--hours 48` overrides the lookback window for a run.
  - `--dry-run` prints the email HTML to stdout instead of sending.
  - `--opml matches.opml` writes the current matches to an OPML file so you can import the coverage list into readers such as FreshRSS.

### Exporting a dynamic OPML snapshot

If you want to review the matched coverage inside another reader, generate an OPML file on demand:

```bash
python pr_daily_digest.py --dry-run --opml matches.opml
```

The command still honours the lookback window, label filter, and blocklist, but instead of emailing it produces an OPML document whose top-level outlines are clients and whose child outlines link to the matching articles. Import the resulting `matches.opml` into FreshRSS (Profile → Import/Export → Import) or another tool that understands OPML link bundles.

## Running manually with FreshRSS labels
If you provide `FRESHRSS_LABEL` in `.env`, the script will only include items tagged with that label by FreshRSS. Leave it blank to include all items in the reading list.

## Notes
- The script deduplicates coverage by canonical URL hash before filtering for clients.
- Sentiment tagging is lightweight, based on simple word lists in the headline.
- When no client matches are found, an email is still sent with a "No coverage found" message so recipients know the job ran.
