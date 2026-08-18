# Morning Brief → LINE OA (automatic, daily)

Every weekday at **7:00 AM Bangkok time**, GitHub runs a script that pulls a
markets snapshot and top news headlines, asks Claude to write a short morning
brief, and **broadcasts it to your LINE Official Account**. No computer needs to
be on. No copy-paste.

You only set this up once. It takes about 10 minutes.

---

## What you need first

Two secret keys. Get both, then paste them into GitHub in Step 3.

**A) Claude API key**
1. Go to https://console.anthropic.com → **API Keys** → **Create Key**.
2. Copy it (starts with `sk-ant-...`). You will need billing set up on that account.

**B) LINE channel access token**
1. Open your LINE Official Account in https://manager.line.biz
   → **Settings → Messaging API → Enable** (links it to a channel).
2. Go to https://developers.line.biz → open that channel → **Messaging API** tab.
3. Under **Channel access token**, issue a **long-lived token** and copy it.

> Treat both keys like passwords. You paste them into GitHub's encrypted
> **Secrets** box only — never into the code, and never into chat.

---

## Step 1 — Create the repository

1. Go to https://github.com/new and create a repo (private is fine),
   e.g. `line-morning-brief`.
2. On the new repo page, click **uploading an existing file**.
3. Unzip the package I sent you and drag **all** of its contents in
   (including the `.github` folder). Commit.

The files are:

```
morning_brief.py
requirements.txt
.github/workflows/morning-brief.yml
README.md
```

> If dragging the `.github` folder is awkward in the browser, use
> **Add file → Create new file**, type `.github/workflows/morning-brief.yml`
> as the name, and paste the contents. GitHub creates the folders for you.

---

## Step 2 — Turn on Actions

Open the **Actions** tab of your repo. If prompted, click
**"I understand my workflows, go ahead and enable them."**

---

## Step 3 — Add your two secrets

Repo **Settings → Secrets and variables → Actions → New repository secret**.
Add these two (names must match exactly):

| Name                        | Value                          |
|-----------------------------|--------------------------------|
| `ANTHROPIC_API_KEY`         | your Claude API key            |
| `LINE_CHANNEL_ACCESS_TOKEN` | your LINE channel access token |

---

## Step 4 — Test it now (don't wait for tomorrow)

1. **Actions** tab → **Morning Brief to LINE** (left side) → **Run workflow** →
   **Run workflow**.
2. Wait ~30 seconds, click into the run, open the **send** job to watch the log.
   You'll see the brief printed, then `Broadcast sent OK`.
3. Check your LINE app — the message appears in the chat with your OA.

To receive it yourself, make sure you've **added your own OA as a friend** in
LINE. Broadcast goes to all friends of the OA.

---

## Changing things later

**The time / days.** Edit the `cron` line in
`.github/workflows/morning-brief.yml`. It is always in **UTC**, and Bangkok is
UTC+7, so subtract 7 hours from your desired local time:

| You want (Bangkok) | cron line              |
|--------------------|------------------------|
| 7:00 AM, Mon–Fri   | `0 0 * * 1-5`          |
| 6:30 AM, every day | `30 23 * * *`          |
| 8:00 AM, Mon–Fri   | `0 1 * * 1-5`          |

**What's in the brief.** Edit the `prompt` text inside `write_brief()` in
`morning_brief.py`, or the tickers in `get_markets()` / feeds in `get_news()`.

**Which Claude model.** Uncomment `ANTHROPIC_MODEL` in the workflow file and set
it to a model you have access to (see
https://docs.claude.com/en/docs/about-claude/models).

---

## Good to know

- GitHub's free tier easily covers a once-a-day job.
- Scheduled runs can occasionally start a few minutes late when GitHub is busy —
  normal, not a failure.
- If the repo sees **no commits for 60 days**, GitHub auto-pauses scheduled
  workflows and emails you; just click to re-enable, or make any small commit.
- To pause the brief, disable the workflow in the **Actions** tab. To stop
  entirely, delete the repo.

## Run it on your own computer instead (optional)

You don't need this if you're using GitHub Actions, but to test locally:

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
export LINE_CHANNEL_ACCESS_TOKEN=...
export DRY_RUN=1          # prints the brief without sending
python morning_brief.py
```

Remove `DRY_RUN` to actually broadcast.
