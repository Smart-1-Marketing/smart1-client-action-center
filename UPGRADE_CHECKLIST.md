# Upgrade Checklist

## GitHub

- Replace the existing repository code with this package.
- Commit/push to the same `main` branch.
- Do not commit `.env` or any API secret.

## Render

Keep the same service and persistent disk.

Do **not** delete `/var/data/tasks.db`.

Recommended new/changed variables:

```text
EMAIL_RESEARCH_MAX_MESSAGES=500
EMAIL_DISCOVERY_MAX_MESSAGES=100
EMAIL_DISCOVERY_LOOKBACK_DAYS=0
SENT_MONITOR_LOOKBACK_DAYS=30
SENT_FOLLOWUP_AFTER_DAYS=3
SENT_SCAN_MAX_MESSAGES=300
CHAT_SYNC_LOOKBACK_DAYS=30
CHAT_SCAN_MAX_SPACES=100
CHAT_SCAN_MAX_MESSAGES_PER_SPACE=100
```

Existing required secrets remain:

```text
APP_PASSWORD
SECRET_KEY
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_REDIRECT_URI
OPENAI_API_KEY
```

## Google Cloud

1. Enable **Google Chat API** in the same Cloud project.
2. In Google Auth Platform → Data Access, authorize:

```text
https://www.googleapis.com/auth/gmail.modify
https://www.googleapis.com/auth/chat.spaces.readonly
https://www.googleapis.com/auth/chat.messages.readonly
```

3. Leave the existing Authorized Redirect URI unchanged unless the Render hostname changed.
4. After Render deploys, open the Action Center and click **Reconnect Google for Chat** so the stored OAuth token receives the new Chat scopes.
5. Click **Sync Mail + Sent + Chat**.

## Quick acceptance test

- Dark mode toggles and persists.
- Client scorecards are clickable.
- Payment and invoice dashboards remain separate.
- Gmail Review finds recent incoming items.
- Chat Review shows actionable Chat messages.
- Sent Follow-ups shows an outbound message awaiting a reply.
- A new email in an existing task thread appears in the task communication log and changes it to Urgent.
- A likely resolution displays **Does this complete it?** rather than auto-closing.
- **Ask Email** can answer an older question using mail outside the 30-day automatic discovery window.
- **Ask Gmail for Tasks** returns selectable candidate tasks.
- **GPT Can Help** offers a prepared prompt when the task is suitable for GPT assistance.

## Gemini Meeting Recaps

- Open **Meeting Recaps** after the first sync.
- Confirm a Gemini / Google Meet recap is detected when the email includes the meeting summary / suggested next steps.
- When a recap has multiple action items, confirm **ASSIGNMENT REVIEW REQUIRED** appears.
- Select only the tasks you want and confirm/change each **Assign to** field before adding them.
- Meeting recap tasks must never be auto-added by the Auto-add AI detections setting.


## Chat + Finances update
- [ ] Push these files to the existing GitHub repository.
- [ ] Keep `/var/data/tasks.db`.
- [ ] Add Google OAuth scope: `https://www.googleapis.com/auth/chat.messages.create`
- [ ] Redeploy Render.
- [ ] Reconnect Google once.
- [ ] Leave **Auto-add incoming invoices to Finances** enabled.
- [ ] Test **Reply in Chat** on a Chat-linked task.
- [ ] Test **Mark Paid** and save a payment reference.
## Not-a-Task training update

For this update replace:

```text
app.py
static/app.js
static/style.css
README.md
UPGRADE_CHECKLIST.md
```

Do not delete `/var/data/tasks.db`.

After Render redeploys:
1. `xwf.google.com` is automatically registered as a hard ignore source.
2. Gmail Review and Chat Review show **Not a Task — Train**.
3. Use **Dismiss** only when an item is irrelevant once.
4. Use **Not a Task — Train** when you want the AI to learn that this type of communication should not become a task.
## Date + Invoice Register + GPT cost controls

Replace:
```text
app.py
templates/index.html
static/app.js
static/style.css
README.md
UPGRADE_CHECKLIST.md
```

Keep `/var/data/tasks.db`.

After redeploy:
1. Existing tasks will receive a backfilled source date.
2. Task cards show Received date and can sort by it.
3. Open **Invoice Register** to see unpaid + paid invoices.
4. **Finances** remains the open bills/payments working dashboard.
5. Full GPT prompts are generated only when **Prepare GPT Prompt** is clicked.
6. Use **Don't Suggest GPT Help for This Type** when a valid task should no longer get GPT-help suggestions.
## Chat Diagnostics + Memory update

Replace:
```text
app.py
templates/index.html
static/app.js
static/style.css
render.yaml
.python-version
README.md
UPGRADE_CHECKLIST.md
```

Do not delete `/var/data/tasks.db`.

After deploy:
1. Click **Check Chat**.
2. Confirm **Chat Read Permission = YES**.
3. Confirm Google returns at least one space.
4. Review any exact API errors shown.
5. Run **Sync Mail + Sent + Chat**.
6. Watch Render memory for several sync cycles.
## Compact Header + Background Sync release

Replace:
```text
app.py
templates/index.html
static/app.js
static/style.css
README.md
UPGRADE_CHECKLIST.md
```

Keep `render.yaml` and `.python-version` from the previous memory-optimized release.
Do not delete `/var/data/tasks.db`.

After deploy:
1. Header should be one compact row.
2. Sync should immediately say it is running in the background instead of holding the request open.
3. Diagnostics contains Gmail/Chat/AI connection information.
4. Settings contains Watch Domains and Dark/Light mode.
5. `Sales Team to Me` should disappear from active Chat review/tasks.
6. Gmail/Chat live tasks should show **Not a Task — Train Type**.
## Emergency SQLite recovery release

Replace/add:
```text
app.py
templates/recovery.html
repair_db.py
README.md
UPGRADE_CHECKLIST.md
```

Do **not** delete or replace `/var/data/tasks.db`.

After deploy:
1. The service should boot instead of crashing.
2. Sign in.
3. You should be redirected to **Database Recovery Required**.
4. Click **Create Safety Copy** first.
5. If the page says native sqlite3 recovery is available, click **Attempt Database Recovery**.
6. If sqlite3 recovery is unavailable, use the Render disk snapshot from before the corruption or recover the copied DB externally.
## One-row controls update

Replace:
```text
templates/index.html
static/app.js
static/style.css
README.md
UPGRADE_CHECKLIST.md
```

No database or Render setting change is required.
