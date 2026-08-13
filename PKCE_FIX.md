# Google OAuth PKCE Fix

The prior OAuth callback failed with:

```text
InvalidGrantError: (invalid_grant) Missing code verifier.
```

## Cause

The Google OAuth authorization request used PKCE. The PKCE `code_verifier`
belongs to the authorization request that generated the `code_challenge`.

The application created one `Flow` during `/gmail/connect` and another `Flow`
during `/gmail/callback`, but the verifier was not preserved between those
HTTP requests.

## Fix

At `/gmail/connect`:

```python
flow = Flow.from_client_config(
    oauth_client_config(),
    scopes=GOOGLE_SCOPES,
    redirect_uri=redirect_uri(),
    autogenerate_code_verifier=True,
)

authorization_url, state = flow.authorization_url(...)
session["google_oauth_state"] = state
session["google_oauth_code_verifier"] = flow.code_verifier
```

At `/gmail/callback`:

```python
flow = Flow.from_client_config(
    oauth_client_config(),
    scopes=GOOGLE_SCOPES,
    state=expected_state,
    redirect_uri=redirect_uri(),
    code_verifier=code_verifier,
    autogenerate_code_verifier=False,
)

flow.fetch_token(authorization_response=request.url)
```

After a successful connection, both the OAuth state and PKCE verifier are
removed from the session.

## Deploy

For this correction you only need to replace `app.py` in the existing GitHub
repository and let Render redeploy.

Do not change:
- Google Client ID
- Google Client Secret
- redirect URI
- Render disk
- tasks.db
