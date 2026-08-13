# Google OAuth diagnostic fix

This patch changes only the Google connection behavior.

## What changed

1. `include_granted_scopes` is now `false`.
   This prevents a reused Google OAuth client from automatically mixing previously
   granted Google Ads / Analytics / GTM / other scopes into this Action Center's
   Gmail + Chat authorization.

2. OAuth errors are caught and displayed in the browser.
   You should no longer receive only `Internal Server Error`.

3. OAuth callback state is checked explicitly.

4. Render proxy headers are honored so generated external URLs use HTTPS.

## Required Google scopes

```text
https://www.googleapis.com/auth/gmail.modify
https://www.googleapis.com/auth/chat.spaces.readonly
https://www.googleapis.com/auth/chat.messages.readonly
https://www.googleapis.com/auth/chat.messages.create
```

## Redirect URI for the current Render service

```text
https://smart1-client-action-center.onrender.com/gmail/callback
```

This exact value should be:
- an Authorized redirect URI on the Google OAuth Web client, and
- `GOOGLE_REDIRECT_URI` in Render.

## Deploy

For the smallest update, replace only `app.py` in GitHub with the patched version
in this package, commit, and let Render redeploy.

If the connection still fails, the browser will display the exact exception.
Copy that error message for the next troubleshooting step.
