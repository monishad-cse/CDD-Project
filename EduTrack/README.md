# EduTrack – Smart Attendance and Student Performance Management System

A React + FastAPI + MySQL application for student profiles, faculty accounts, subject allocation, attendance, internal marks, examination eligibility, automated caution emails and semester-aware internship recommendations.

## Technology stack
- React 19 + Vite + Lucide icons
- FastAPI + Uvicorn
- MySQL 8.4
- Docker + Docker Compose
- GitHub Actions CI

## Run the project on Windows (Docker – recommended)

### Requirements
1. Windows 10/11 with Docker Desktop installed.
2. Docker Desktop started and running (WSL 2 backend enabled if Docker Desktop requests it).
3. Git, if you are cloning the project from GitHub.

### Get the project

Clone the team's repository, or download and extract the project ZIP. If cloning, open PowerShell or Git Bash and run:

```bash
git clone https://github.com/monishad-cse/CDD-Project.git
cd CDD-Project/EduTrack
```

If you downloaded a ZIP instead, extract it and open the `EduTrack` folder that contains `docker-compose.yml`.

### Configure environment variables

1. In the `EduTrack` folder, copy `.env.example` and name the copy `.env`.
   - PowerShell: `Copy-Item .env.example .env`
   - Git Bash: `cp .env.example .env`
2. Open `.env` in a text editor.
3. Set your own strong values for `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD`, `SECRET_KEY`, `ADMIN_USERNAME` and `ADMIN_PASSWORD`.
4. Save the file. Never commit or share `.env`; it contains local credentials and secrets.
5. SMTP settings are optional. Configure them only if you want email notifications to be sent from your own authorized sender account. For Gmail, use a Google App Password, not your normal Gmail password.

### Build and start

Make sure your terminal is in the `EduTrack` folder (the folder containing `docker-compose.yml`), then run:

```bash
docker compose up --build -d
```

Check that the containers are running:

```bash
docker compose ps
```

Open the application:

- React application: http://localhost:8080
- API health check: http://localhost:8080/api/health
- FastAPI Swagger documentation: http://localhost:8080/api/docs

### First-time application setup

1. Sign in using the admin username and password configured in your local `.env`, with the **Admin** role.
2. In **Faculty**, create faculty accounts.
3. In **Subjects**, create/assign subjects to faculty.
4. In **Students**, create student profiles and their login accounts. Enter each student's name, roll number, department, semester, email and skills, and assign the responsible faculty member as required by the form.
5. Faculty members can sign in using the accounts created for them and record attendance and internal marks for their assigned students/subjects.
6. Admin can create internship opportunities and set the minimum and maximum eligible semesters for each opportunity. To make an opportunity available only to 7th- and 8th-semester students, set its semester range to **7 through 8**. Student visibility follows the semester range configured for the opportunity.

## Important: each teammate's local data and accounts are separate

When each teammate runs Docker on their own laptop, each laptop creates/uses its **own local MySQL database and Docker volume**. GitHub shares the source code; it does not share database records or login accounts.

- Each teammate must configure their own local `.env` and initialize their own local database.
- The admin account is bootstrapped from that teammate's `ADMIN_USERNAME` and `ADMIN_PASSWORD` when their database is initialized for the first time.
- Student and faculty accounts, attendance, marks, internships and other records created on one laptop do **not** automatically appear on another teammate's laptop.
- If the team needs identical demo data on every laptop, arrange a database export/import privately and securely. Do not commit database backups, real credentials, or personal student data to the public GitHub repository. Share only data that the team is authorized to use.

## Main application modules
- **Admin:** faculty accounts, subject allocation, student records, internships and overview.
- **Faculty:** assigned students, attendance entry, internal marks and caution checks.
- **Student:** own profile, attendance, marks, eligibility, notifications and semester-matched internship opportunities.
- **Eligibility:** recorded attendance is compared with `ATTENDANCE_THRESHOLD` (default 75%).
- **Caution alerts:** after attendance or marks are saved, the backend evaluates the student's record and creates an alert when configured thresholds are crossed. If SMTP is configured and the student has an email address, the alert can be emailed. Duplicate alerts are suppressed for 24 hours.
- **Internship recommendations:** student visibility is filtered by the minimum and maximum semester configured for each opportunity. Recommendations are ranked using skill overlap and, when available, internal-marks average.

## Optional: Gmail SMTP email setup

Set these values in `.env` if you want to enable email delivery:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-sender@gmail.com
SMTP_PASSWORD=your-google-app-password
SMTP_FROM=your-sender@gmail.com
```

For Gmail, enable 2-Step Verification and generate an App Password. Do not use your normal Gmail password. Enter student email addresses in their profiles. If SMTP is not configured, the application can still be used, but email notifications will not be delivered.

## Useful Docker commands

Run these commands from the folder containing `docker-compose.yml`:

```bash
# Start/rebuild the application
docker compose up --build -d

# Check container status
docker compose ps

# View backend logs
docker compose logs -f backend

# View logs for all services
docker compose logs -f

# Stop containers (keeps the database volume)
docker compose down
```

**Database warning:** Do not run `docker compose down -v` unless you intentionally want to delete the local database volume and its data.

## Local development without Docker (optional)

Backend (Python 3.12+):

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
uvicorn backend.main:api --reload --port 8000
```

Frontend (Node.js 22+), in a separate terminal:

```bash
cd frontend
npm install
npm run dev
```

The Vite development server proxies `/api` to `http://localhost:8000`.

## GitHub Actions and deployment

The GitHub Actions workflow installs frontend dependencies, creates a production React build, compiles backend Python files, runs structural checks and builds both Docker images. Deployment is not automatic until hosting credentials and deployment secrets are configured. See `DEPLOYMENT.md` for deployment-related information.

## Troubleshooting

- **`no configuration file provided: not found`:** your terminal is not in the folder containing `docker-compose.yml`. Use `cd` to enter the `EduTrack` project folder, then retry.
- **Docker daemon/engine error:** start Docker Desktop and wait until it reports that Docker is running.
- **Port already in use:** stop the other process/container using the required port, then retry.
- **Backend or database startup issue:** inspect logs with `docker compose logs -f backend` and `docker compose logs -f mysql`.
- **Login fails:** check the account role and credentials. The first admin credentials come from `.env`; changing `.env` later does not necessarily reset an already-created admin account.

## Notes

- The original Flask templates are retained in the repository as a migration reference; the active browser interface is the React SPA and its JSON API is served by FastAPI.
- Each teammate should test the Docker build and application on their own laptop. A successful local run is not the same as a public deployment; `localhost` is accessible only from the computer running the application.
