# Smart 1 Client Action Center

Standalone, password-protected task dashboard for Smart 1 Marketing.

## Included features

- Separate **Client Items** and **Needs Paid** categories
- Color-coded task bubbles
- Automatic sorting by due date
- Status, Create Note, Add Deadline, See Email, Prepare Reply, Complete buttons
- Permanent note history
- Calendar deadline picker
- Completed-item history with restore
- SQLite persistence on a Render persistent disk
- Password-protected dashboard
- Current seed tasks, including TrimGlow GMB, Icon Solar GPT Ads, Icon Solar Wanda videos, Text Doctor and Home Loan

## GitHub repository structure

```text
smart1-client-action-center/
├── app.py
├── render.yaml
├── requirements.txt
├── .gitignore
├── .env.example
├── README.md
├── templates/
│   ├── index.html
│   └── login.html
└── static/
    ├── app.js
    └── style.css
```

## Render — Blueprint method

1. Create a GitHub repository named `smart1-client-action-center`.
2. Upload every file/folder from this package to the repo root.
3. Commit to `main`.
4. In Render choose **New → Blueprint**.
5. Connect the GitHub repository.
6. Render will read `render.yaml`.
7. When Render asks for `APP_PASSWORD`, enter the password you want to use for this private dashboard.
8. Apply the Blueprint.
9. Open the resulting `onrender.com` URL.

## Render — manual values

| Setting | Value |
|---|---|
| Service Type | Web Service |
| Name | `smart1-client-action-center` |
| Runtime | Python 3 |
| Region | Ohio |
| Branch | `main` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn app:app` |
| Health Check Path | `/health` |

### Environment variables

| Key | Value |
|---|---|
| `APP_PASSWORD` | Choose your login password |
| `SECRET_KEY` | Generate a long random value |
| `DATA_DIR` | `/var/data` |

### Persistent disk

| Setting | Value |
|---|---|
| Name | `smart1-action-data` |
| Mount Path | `/var/data` |
| Size | 1 GB |

The database is stored at `/var/data/tasks.db`. Do not deploy this as a free stateless service if you want notes/history to persist. The included Blueprint uses the Starter web-service plan because Render persistent disks require a paid compatible service.

## URL

Render will assign a URL similar to:

`https://smart1-client-action-center.onrender.com`

You can later attach a custom domain such as:

`tasks.smart1marketing.com`

## Gmail behavior

- **See Email** opens a stored Gmail thread URL when a task has one.
- **Prepare Reply** opens an editable reply and then a prefilled Gmail compose window.
- Direct send from the standalone app is not enabled yet.

To send directly from the site, add Google OAuth/Gmail API in the next version. Keep Google credentials in Render Environment Variables, never GitHub.

## Important persistence behavior

The starter tasks are inserted only when the SQLite database is empty. Once the site is live, future changes to the seed list in `app.py` will not overwrite your live task data.
