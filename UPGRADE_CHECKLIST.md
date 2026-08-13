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
