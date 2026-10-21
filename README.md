# DenTech

A Python/Django dental clinic application with a public website, a patient portal, and a role-based clinic workspace.

## Run locally on Windows

Requires Python 3.12 or newer. From PowerShell:

```powershell
cd C:\Git\DenTech
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_demo
.\.venv\Scripts\python.exe manage.py runserver
```

Open http://127.0.0.1:8000. Alternatively, run `powershell -ExecutionPolicy Bypass -File .\start.ps1` after installing dependencies.

`seed_demo` is optional. It creates **fictional** records and four accounts: `demo.admin`, `demo.reception`, `demo.dentist`, and `demo.patient`. A random password is printed once in the terminal. Existing demo accounts are never overwritten. Reset a forgotten local demo password using `python manage.py changepassword demo.admin` with the virtual environment Python.

For an empty clinic, skip demo seeding and run `python manage.py createsuperuser`. Superusers have full clinic access even if their role label is Patient; set their role to Administrator in the admin interface. Add services and staff accounts in `/admin/`. Assign staff a role; only administrators maintaining system configuration should receive Django `is_staff` access. Receptionists and dentists use the custom workspace without `is_staff`. The built-in admin is restricted to explicitly assigned admin permissions; ordinary workspace roles do not grant admin-site access.

## Patient activation

1. Reception searches existing patients before registering a new one.
2. Open the patient record and select **Issue activation code**.
3. Print the invitation. It contains the Patient ID, activation URL, and a random code displayed once.
4. The patient enters Patient ID + code at `/activate/`.
5. The patient chooses an email address, username, and password.
6. A verification email is sent. The account remains inactive until the patient confirms it.
7. Subsequent visits use the same record and login account.

Codes expire after 48 hours, are stored as password hashes, stop accepting attempts after five guesses, and are invalidated by replacement or use. Account setup grants expire after 10 minutes. Email verification links expire after 24 hours; resending replaces the old link. Activation, login, password reset, and resend requests are rate limited in the database. The code grants control over the linked record: hand invitations only to the verified patient or authorized guardian. Guardian-managed family accounts are not implemented.

**Local emails print in the server terminal.** This includes verification and password reset links; no email is delivered until SMTP is configured. See `.env.example`. It is a reference for environment variables, not an automatically loaded file. For PowerShell, set variables as `$env:EMAIL_HOST = 'smtp.example.com'` before starting the server.

## What is included

- Responsive public home, services, about, and contact pages; editable clinic contact details.
- Username/password sign-in, POST logout, email verification, password reset/change, patient contact updates.
- Staff dashboard; patient search and registration with a duplicate name/birth-date check.
- Printable one-time portal invitations and billing summaries.
- Appointment requests, dentist assignment, confirmation, rescheduling, completion, and cancellation.
- Exact-time confirmed dentist booking conflict prevention. Appointment duration, overlapping intervals, clinic availability, holiday calendars, and reminders are **not yet modeled**; staff must check these before confirmation.
- Dentist visit records, private staff notes, release controls, permanent/primary FDI dental charts.
- Treatment plans and restricted PDF/PNG/JPEG attachments, up to 10 MB each.
- Patient-owned released visits, plans, and documents. Reception cannot access clinical notes or attachments.
- Invoices, partial payments, balances, payment history, printable receipts. Overpayments and negative payments are rejected.
- Administrator totals, activity log, clinic settings, staff/service administration.
- SQLite local development, PostgreSQL environment configuration, migrations, and regression tests.

The chart displays the current state of each tooth; it is not a surface-level odontogram or a complete history of tooth changes. Visit summaries should document clinical changes over time. The audit log records important reads/downloads and mutations, but is not a tamper-proof external audit service.

## Project structure

```text
config/                 Django settings and entry points
clinic/models.py        Patient, identity, clinical, scheduling, billing records
clinic/services.py      Activation, email, throttling, audit events
clinic/forms.py         Validation and editable fields
clinic/views.py         Role and record access checks; page workflows
clinic/migrations/      Database schema history
clinic/tests.py         Activation, authorization, billing and workflow tests
templates/              Server-rendered HTML
static/                 Responsive CSS and small JavaScript enhancements
```

## Validation

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test clinic
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

## Deployment configuration

This is a working first version for local review, not a configured live clinic deployment. Before entering real patient information:

- Set `DJANGO_DEBUG=0`, a strong `DJANGO_SECRET_KEY`, explicit `DJANGO_ALLOWED_HOSTS`, and an HTTPS `PUBLIC_BASE_URL`. Production settings fail closed if secret/HTTPS URL is missing.
- Configure PostgreSQL with the `DB_*` variables and test concurrent workflows there. SQLite is for a single local development process; its row locking semantics differ from PostgreSQL.
- Configure authenticated SMTP, a legitimate sender, and delivery monitoring. Console email is for local use only. Staff must assist failed deliveries.
- Serve static files using your host after `collectstatic`. Use a production WSGI server and HTTPS reverse proxy; `runserver` is only for development. Preserve the actual client address for rate limiting; do not trust client-supplied forwarded IP headers.
- Keep `private-media/` out of public web-server routes. Documents must go through the authenticated download endpoint. Enable upload scanning, backup/restore, storage encryption, monitoring, and appropriate retention practices for your deployment.
- Review staff permissions, access logs, privacy/consent, receipt requirements, and clinical workflows with the clinic. Configure real contact details and prices. Demo prices and all demo records are fictional.
- Do not deploy demo accounts or the development database. Billing summaries are not configured as official tax receipts. Online payment processing, clinical e-signatures, inventory, SMS, account email changes, and guardian accounts are outside this version.

No credentials or patient databases are tracked in Git. This project does not automatically commit, push, or deploy changes.
