# Deploying CareCircle

Netlify and Vercel host static sites and short serverless functions. CareCircle needs a
long-running Python server (the reminder agent loops in the background), so use a host that
runs web services: Render, Railway or Fly.io. Steps for Render:

1. Push this folder to a GitHub repo.
2. Create a free Postgres database (Render, Neon or Supabase) and copy its connection string.
3. Render > New > Blueprint > select the repo (reads `render.yaml`).
4. Set `DATABASE_URL` to the connection string. Optionally set SMTP variables for real emails:
   `SMTP_HOST` (e.g. smtp-relay.brevo.com / smtp.gmail.com), `SMTP_PORT` (587), `SMTP_USER`,
   `SMTP_PASSWORD` (an app password), `MAIL_FROM`.
5. Deploy, then open the URL Render gives you.

Why Postgres: free web services have an ephemeral disk, so SQLite data is lost on every redeploy/restart.

Free-tier caveat: free instances sleep when idle, which pauses the reminder agent. Use an always-on plan,
or ping the URL every few minutes with a free uptime monitor, for reminders to arrive on time.
