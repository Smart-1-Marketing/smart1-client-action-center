# Smart 1 Client Action Center — Communications Intelligence

This version expands the Smart 1 Client Action Center into a communications-driven task dashboard using Gmail, Sent mail, Google Chat, Gemini/Google Meet recap emails, and OpenAI.

## Dashboard tabs

- **Client Tasks** — normal client work only.
- **Payments** — items Smart 1 needs to pay, fund, reconcile, or cure.
- **Invoices to Pay** — open payment items marked Invoice Sent.
- **Sent Follow-ups** — sent messages that appear to need a reply or future follow-up.
- **Gmail Review** — possible new tasks detected from incoming Gmail.
- **Chat Review** — possible new tasks detected from Google Chat.
- **Meeting Recaps** — Gemini/Google Meet recap emails containing proposed action items.
- **Completed** — completed tasks and paid items.

## 1. Gmail automatic task window vs. historical research

Automatic new-task detection intentionally focuses on the last 30 days.

Default query:

```text
newer_than:30d -in:sent -in:drafts -in:spam -in:trash -category:promotions -category:social -category:forums
```

However, **Ask Email** inside a task and **Ask Gmail for Tasks** are not limited to that 30-day window unless your question itself asks for a date range. They are designed to search the full Gmail history available to the connected account.

## 2. Sent mail monitoring

Sent mail is scanned separately. The app looks for messages where Todd/Smart 1:

- asked someone for information, approval, or confirmation;
- promised to send, fix, prepare, check, or complete something;
- sent a proposal or deliverable that reasonably needs a response;
- left another open loop that should be followed up.

Default settings:

```text
SENT_MONITOR_LOOKBACK_DAYS=30
SENT_FOLLOWUP_AFTER_DAYS=3
SENT_SCAN_MAX_MESSAGES=300
```

A sent follow-up can be linked to an existing task or converted into a new task.

## 3. Resolution monitoring

Incoming mail, Sent mail, and Google Chat updates can be attached to an existing task.

When OpenAI believes the communication chain now looks resolved, the task displays a confirmation box:

```text
This communication may have resolved the task.
[AI summary of the apparent resolution]

Yes — Complete It
No — Keep Open
```

The app does not automatically close the task.

If you choose **Yes — Complete It**:

- the task is completed;
- the resolution summary is stored in the task notes;
- the supporting email/chat update and source link remain stored with the task/history.

For multi-recipient Gmail threads, the resolution analyzer uses the entire thread context and task participants rather than relying only on the latest sender.

## 4. Multi-recipient chains

The app tracks addresses from:

- From
- To
- Cc
- Bcc when available

Tasks created from multi-recipient messages show a **MULTI-PERSON** bubble.

Actionable multi-recipient Gmail items are elevated to at least High priority. New material messages in an existing task chain are attached to that task and normally make it Urgent.

Sent mail also records recipient count so important group follow-ups can be filtered quickly.

## 5. Google Chat

The app can scan Google Chat spaces, group chats, and direct messages visible to the connected user.

Possible Chat action items appear under **Chat Review**. Approve or dismiss them just like Gmail suggestions.

If a new Chat message clearly continues an existing task:

- the message is attached under the task's Google Chat Updates;
- the task becomes Urgent;
- the communication can be checked for a possible resolution.

### Google Cloud requirements

Enable both:

```text
Gmail API
Google Chat API
```

The application requests these OAuth scopes:

```text
https://www.googleapis.com/auth/gmail.modify
https://www.googleapis.com/auth/chat.spaces.readonly
https://www.googleapis.com/auth/chat.messages.readonly
```

If Gmail was connected before this version was deployed, click **Reconnect Google for Chat** once so Google can grant the two additional Chat read scopes.

## 6. Gemini / Google Meet meeting summaries

The Gmail analyzer looks for Gemini / Google Meet **Take notes for me** recap emails and extracts the meeting summary and every concrete action item / Suggested next step it can find.

Meeting tasks are **never auto-added**, even if Auto-add AI detections is turned on. The communications sync also performs a recap-specific re-scan of the current 30-day Gmail window, so meeting recap messages that an older version may already have marked as processed can still be discovered and moved into Meeting Recaps.

They appear under **Meeting Recaps**.

When a recap contains multiple tasks, the dashboard displays **ASSIGNMENT REVIEW REQUIRED**. Each proposed task has:

- a checkbox;
- task title and summary;
- priority;
- due date if present;
- suggested assignee if Gemini named one;
- an editable **Assign to** field;
- GPT Can Help indicator when applicable.

Choose the tasks you want, confirm/change the assignee, then click **Add Selected Tasks**.

The source Gemini recap email remains linked to the created task.

## 7. Ask Email inside a task

Every active task includes **Ask Email**.

Examples:

```text
What deadline did they give us?
Did they approve the revised video?
What budget did I promise?
What did I tell them I would have done by Friday?
What is the latest amount due?
```

The app searches relevant Gmail history, answers from the matching messages, and permanently stores:

- your question;
- the answer;
- confidence;
- supporting email links;
- timestamp.

This appears under the task's **Email Research Log**.

## 8. Ask Gmail for Tasks

Use **Ask Gmail for Tasks** for natural-language discovery, for example:

```text
Find anything I still need to do for Icon Solar.
Look through all my email for unresolved items with Pillar Media.
What tasks are still open with Schmidt's?
```

The app searches Gmail history, uses OpenAI to consolidate repeated messages about the same issue, and presents a checklist. You choose which possible tasks get added.

The browser voice button can be used when Web Speech Recognition is available.

## 9. GPT Help

OpenAI classification now identifies tasks that GPT could materially help complete itself, including:

- drafting content or responses;
- analysis;
- planning;
- coding or troubleshooting;
- structured research;
- preparing a technical fix prompt.

Those tasks display:

```text
GPT can probably help complete this task.
Prepare GPT Prompt
```

The prompt is stored with the task. Existing/manual tasks also have **Can GPT Help?** so you can request an assessment later.

## 10. Highly Watched Domains

Use **Watch Domains** for important client/vendor domains such as:

```text
iconsolar.com
pillarmedia.com
lgracebrands.com
```

Watched domains receive an additional scan and actionable mail receives at least High priority.

## 11. Dark mode

The Dark Mode / Light Mode toggle remembers your choice in the browser. With no saved choice, the dashboard defaults to dark mode at night.

## 12. OpenAI configuration

Required Render environment variable:

```text
OPENAI_API_KEY
```

Default model:

```text
OPENAI_MODEL=gpt-5-mini
```

OpenAI calls use the Responses API, structured JSON schemas where appropriate, and `store=False`.

The API key is server-side only and must not be committed to GitHub.

## 13. Render environment variables

Private / required:

```text
APP_PASSWORD
SECRET_KEY
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_REDIRECT_URI
OPENAI_API_KEY
```

Recommended:

```text
DATA_DIR=/var/data
OPENAI_MODEL=gpt-5-mini
AUTO_GMAIL_SYNC_MINUTES=15
GMAIL_SYNC_QUERY=newer_than:30d -in:sent -in:drafts -in:spam -in:trash -category:promotions -category:social -category:forums
GMAIL_SCAN_MAX_MESSAGES=500
GMAIL_ANALYZE_MAX_NEW=75
OPENAI_EMAIL_BODY_CHARS=12000
EMAIL_RESEARCH_MAX_MESSAGES=500
EMAIL_DISCOVERY_MAX_MESSAGES=100
EMAIL_DISCOVERY_LOOKBACK_DAYS=0
SENT_MONITOR_LOOKBACK_DAYS=30
SENT_FOLLOWUP_AFTER_DAYS=3
SENT_SCAN_MAX_MESSAGES=300
CHAT_SYNC_LOOKBACK_DAYS=30
CHAT_SCAN_MAX_SPACES=100
CHAT_SCAN_MAX_MESSAGES_PER_SPACE=100
WATCH_DOMAIN_LOOKBACK_DAYS=90
AI_CONTEXT_CHAR_BUDGET=90000
```

`EMAIL_DISCOVERY_LOOKBACK_DAYS=0` means manual Gmail discovery is not given an artificial date limit by the app.

## 14. Updating the existing GitHub / Render deployment

Use the same GitHub repository and same Render service.

Replace the application files with this package, commit/push to `main`, and allow Render to redeploy.

Do **not** delete:

```text
/var/data/tasks.db
```

The app performs additive SQLite migrations for the new communication features.

New/expanded storage includes:

```text
task_research_logs
task_email_updates
task_participants
task_resolution_reviews
sent_monitors
chat_processed
chat_suggestions
task_chat_updates
meeting_reviews
watch_domains
```

## 15. After deployment

1. Confirm Gmail shows **Connected**.
2. If Google Chat says **Reconnect Google**, click it and approve the new Chat scopes.
3. Confirm OpenAI shows **AI Analyzer On**.
4. Confirm the Google Chat API is enabled in the same Google Cloud project.
5. Click **Sync Mail + Sent + Chat**.
6. Review **Gmail Review**, **Chat Review**, **Sent Follow-ups**, and **Meeting Recaps**.
7. Add important client/vendor domains to **Watch Domains**.


# Chat replies and Finances

## Chat resolution
Google Chat remains part of the resolution monitor. Related Chat messages are attached to the task, and AI can create a **Possible Resolution Detected** card. The app never auto-completes a task from Chat; Todd confirms Yes or No.

## Reply in Chat
Chat-linked tasks now have **Reply in Chat**. Replies are sent as the authenticated Google user, stored in the task communication log, and move the task to Waiting. If the outgoing reply appears to satisfy the request, the app asks whether the task is complete.

Add this Google OAuth scope and reconnect Google once:

```text
https://www.googleapis.com/auth/chat.messages.create
```

## Finances / Bills to Pay
The Payments tab is now labeled **Finances**.

Incoming invoice emails are automatically added to Finances when OpenAI identifies:
- payment category
- invoice sent/presented for payment
- high or medium confidence

The dashboard has an **Auto-add incoming invoices to Finances** toggle.

## Mark Paid
Payment items now collect:
- Paid Amount
- Payment Reference
- Payment Note
- Paid timestamp

Then the item moves to Completed while keeping the invoice email and history.
## NOT A TASK training

The Gmail Review and Google Chat Review queues now include:

```text
Not a Task — Train
```

This is different from **Dismiss**:

- **Dismiss** removes the current suggestion only.
- **Not a Task — Train** removes the suggestion and stores it as a negative AI training example.
- Future OpenAI classification prompts include recent user-trained negative examples so similar automated/informational communications are less likely to become tasks.
- Training examples do **not** automatically blacklist an entire customer domain.

### Permanent ignore: xwf.google.com

`xwf.google.com` is seeded as a hard Gmail ignore domain.

Messages from that domain:
- are marked processed
- are not sent to OpenAI for task classification
- do not create Gmail Review suggestions
- do not make an existing task urgent simply because the message is in a related thread

Existing unapproved Gmail suggestions from `@xwf.google.com` are automatically moved out of the review queue during database initialization.

The app intentionally does not delete any already-approved/live task automatically.
## Task received date + sorting

Every task card now shows:

```text
Received: Aug 13, 2026, 2:15 PM
```

For Gmail tasks this is the original email timestamp.
For Google Chat tasks this is the original Chat message timestamp.
For manual tasks it is the task creation time.

The task toolbar can sort by:
- Due Date
- Date Received — Newest
- Date Received — Oldest
- Priority

Older live records are backfilled automatically from their Gmail/Chat source or task creation time.

## Complete Invoice Register

The former **Invoices to Pay** tab is now **Invoice Register**.

It shows all invoice records:
- unpaid
- overdue
- paid

Scorecards:
- All Invoices
- Unpaid Invoices
- Overdue Invoices
- Paid Invoices

The **Finances** tab remains the working list of open payment obligations.
The **Invoice Register** is the complete invoice history.

Paid invoice cards retain:
- paid amount
- paid date/time
- payment reference
- invoice email/history

## GPT prompt cost control

Automatic Gmail classification still asks the AI whether GPT could help, but it no longer asks the model to write the full ready-to-use GPT prompt during the automatic scan.

The full prompt is generated only when Todd clicks:

```text
Prepare GPT Prompt
```

The app also adds:

```text
Don't Suggest GPT Help for This Type
```

This saves a subject-template + sender-domain suppression rule so similar emails stop showing the GPT-help recommendation.

This is separate from **Not a Task — Train**:
- Not a Task = the communication itself should not become a task.
- Don't Suggest GPT Help = it can remain a valid task, but stop recommending GPT assistance for that type.
