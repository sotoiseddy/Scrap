# Class Event Tracker

A responsive Flask application for class schedules, documents, user management, and moderation. Supabase Postgres stores application records; Supabase Storage stores uploaded class documents; Vercel hosts the Flask app as a Python function.

## Features

- Public seven-day class schedule with week navigation and color-coded events.
- Account login for admins and class managers; passwords are bcrypt-hashed.
- Admin class management, user provisioning, global schedule, and manager-preview mode.
- Managers can create, edit, and delete events and upload or delete documents for their assigned class; admins can manage all classes.
- Class document library with PDF and image previews, 10 MB maximum file size.
- Dark/light theme switch.
- Profanity-filtered submissions appear in the admin moderation queue; admins can mark them reviewed and ban or unban accounts.
- Server-side access checks, signed sessions, and CSRF protection on all form submissions.

Documents are intentionally available to anyone with a document URL, matching the original app's public document behavior. The app redirects downloads/previews to short-lived signed Supabase URLs so files can be served without passing large payloads through Vercel. Do not store private student or school records without changing the document access policy.

## Local setup

Requirements: Python 3.11+ and a Supabase project.

1. Create a virtual environment and install the dependencies:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and set Supabase credentials and an application secret:

   ```powershell
   Copy-Item .env.example .env
   ```
3. Run `supabase/schema.sql` in the Supabase SQL Editor. The schema uses a private `class-documents` bucket; file reads are served through the Flask app and are public at the application level.
4. Create the initial admin account:

   ```powershell
   python seed_admin.py
   ```

5. Start the development server:

   ```powershell
   flask --app app run --debug
   ```

The app is available at <http://127.0.0.1:5000>.

Run the focused regression tests with:

```powershell
python -m unittest discover -s tests -v
```

## Environment variables

| Variable | Purpose |
| --- | --- |
| `SUPABASE_URL` | Project URL |
| `SUPABASE_ANON_KEY` | Public project anon key, used by the browser for signed uploads |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-only service role key; never expose it to browser code |
| `SUPABASE_STORAGE_BUCKET` | Optional Storage bucket name (default `class-documents`) |
| `FLASK_SECRET_KEY` | Long random secret for Flask sessions (or `AUTH_SECRET`) |
| `ADMIN_EMAIL` | Initial admin email used by `seed_admin.py` |
| `ADMIN_PASSWORD` | Initial admin password; at least 8 characters |
| `PORTAL_COOKIE` | Authenticated `mcsuna.in` session cookie used by `sync_homework.py` |
| `HOMEWORK_URL` | Optional homework list URL on `mcsuna.in` (defaults to the standard page) |

## Automated homework sync

The GitHub Actions workflow runs `sync_homework.py` on every push to `main` (including the initial deployment), every six hours (00:00, 06:00, 12:00, and 18:00 UTC), and manually from the repository's Actions tab. Scheduled runs may be delayed by GitHub. It runs in Actions rather than Flask startup so Vercel cold starts and autoscaling do not launch duplicate syncs or block web requests.

Before enabling it:

1. Run the updated `supabase/schema.sql` so the `homework` table exists.
2. In the GitHub repository settings, add Actions secrets named `PORTAL_COOKIE`, `SUPABASE_URL`, and `SUPABASE_SERVICE_ROLE_KEY`. Optionally add `HOMEWORK_URL` to override the default homework page. Keep the portal cookie and service-role key secret; the sync writes directly to Supabase.
3. Ensure GitHub Actions is enabled for the repository.

For a one-time local run, set those values in `.env` and run `python sync_homework.py`. The generated `homework_page.txt` may contain private student information and is excluded from Git.

## Deploy to Vercel

1. Import this repository as a Vercel project. Vercel detects the root-level `app.py` Flask entrypoint automatically.
2. Add `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `FLASK_SECRET_KEY`, `ADMIN_EMAIL`, and `ADMIN_PASSWORD` to the Vercel project environment.
3. Run `supabase/schema.sql` once in the project's Supabase SQL Editor.
4. Deploy. Vercel serves files from `public/` through its CDN and routes the Flask app through its Python runtime.

Run `python seed_admin.py` locally with the same production Supabase environment values to provision the first administrator. Never put the service role key in a `NEXT_PUBLIC_`/client variable or commit it.

## Project layout

```text
app.py                 Flask routes, authorization, database and storage operations
app.py                 Root-level Flask/Vercel entry point
sync_homework.py       Authenticated homework scraper and Supabase sync
templates/             Jinja pages
public/                Responsive styles and small browser enhancements
supabase/schema.sql    Supabase schema and private Storage bucket setup
seed_admin.py          Idempotent initial admin provisioning
.github/workflows/     Six-hour homework sync schedule
```
