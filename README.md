# ConvoGen AI

Express + Supabase authentication app: register, email login, Google OAuth,
forgot/reset password, and a protected dashboard. Sessions are stored in
httpOnly cookies (`convogen_access` / `convogen_refresh`) by the server.

## Project structure

```
convogen-ai/
  server.js          Express server + all auth routes
  supabase.js        Standalone Supabase client export (optional helper)
  package.json
  .env               Your real config (never commit this)
  .env.example       Template
  public/
    index.html           Register page (served at /)
    login.html           Login + Google sign-in
    auth-callback.html   Google OAuth callback
    forgot-password.html
    reset-password.html
    dashboard.html       Protected page (served at /dashboard)
    convogen-logo.webp
```

## Run it (Windows, cmd)

```
cd convogen-ai
npm install
npm start
```

Then open http://localhost:3000

For auto-restart while editing: `npm run dev`

## Configuration (.env)

| Key | Value |
| --- | --- |
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_PUBLISHABLE_KEY` | Your Supabase publishable (anon) key |
| `APP_URL` | Where the app runs, e.g. `http://localhost:3000` |
| `PORT` | `3000` |

Notes:
- Keep `.env` line endings as LF, not Windows CRLF — a trailing carriage
  return in `SUPABASE_URL` breaks Supabase requests and makes login hang.
- Supabase project settings (checked 2026-11-09): email auth on, Google
  provider on, signups open, email confirmation required — new accounts
  must verify their email before they can sign in.
- In Supabase Auth > URL Configuration, the redirect allow list needs
  `APP_URL/auth/callback` (Google) and `APP_URL/reset-password`
  (password reset) for those flows to land back on the app.
