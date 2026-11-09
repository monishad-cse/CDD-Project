# EduTrack – Smart Attendance and Student Performance Management System

A React + FastAPI + MySQL application for student profiles, faculty accounts, subject allocation, attendance, internal marks, examination eligibility, automated caution emails and semester-aware internship recommendations.

## Stack
- React 19 + Vite + Lucide icons
- FastAPI + Uvicorn
- MySQL 8.4
- Docker + Docker Compose
- GitHub Actions CI

## Run on Windows
1. Install and start Docker Desktop.
2. Extract this project and open its root folder in VS Code.
3. Copy `.env.example` to `.env`.
4. Set strong values for `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD`, `SECRET_KEY`, `ADMIN_USERNAME`, and `ADMIN_PASSWORD`.
5. Run `docker compose up --build -d`.
6. Open **http://localhost:8080**.

The first start creates the MySQL schema and bootstraps the admin account using `ADMIN_USERNAME` and `ADMIN_PASSWORD`. Use those credentials with the **Admin** role. Do not commit `.env` to GitHub.

### First-time setup inside the application
1. Sign in using the admin credentials from `.env`.
2. Open **Faculty** and create faculty accounts.
3. Open **Subjects** and assign subjects to faculty.
4. Open **Students** and create student profiles. Set each student's login username/password, email, semester and comma-separated skills. Admin must assign the responsible faculty member.
5. Faculty can now sign in, select their students and subjects, and record attendance and internal marks.
6. Admin can add internship opportunities. Semester 5/6 students see matching opportunities ranked by skill overlap.

## Main application modules
- Admin: faculty accounts, subject allocation, student records, internships and overview.
- Faculty: assigned students, attendance entry, internal marks and caution checks.
- Student: own profile, attendance, marks, eligibility, notifications and semester-matched internship opportunities.
- Eligibility: overall recorded attendance compared with `ATTENDANCE_THRESHOLD` (default 75%).
- Caution alerts: after attendance or marks are saved, the backend evaluates the student's record and creates an alert when configured thresholds are crossed. If SMTP is configured and the student has an email address, the alert is emailed automatically. A duplicate alert is suppressed for 24 hours. Configure SMTP below.
- Internship recommendations: students in the configured semester range see opportunities ranked using a 70% skill-overlap score and a 30% internal-marks average when marks are available.

## SMTP email setup
Set these values in `.env` to enable actual email delivery:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-sender@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM=your-sender@gmail.com
```

For Gmail, use an App Password with 2-Step Verification, not your normal account password. Student email addresses must be entered when creating student profiles. If SMTP is not configured, the application still works but emails are not sent.

## Local development without Docker
Backend (Python 3.12+):
```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
pip install -r backend/requirements.txt
uvicorn backend.main:api --reload --port 8000
```
Frontend (Node.js 22+):
```bash
cd frontend
npm install
npm run dev
```
The Vite dev server proxies `/api` to `http://localhost:8000`.

## Docker commands
```bash
docker compose up --build -d
docker compose ps
docker compose logs -f backend
docker compose down
```
- React app: http://localhost:8080
- FastAPI Swagger docs: http://localhost:8080/api/docs (or http://localhost:8000/api/docs)
- API health: http://localhost:8080/api/health

## GitHub Actions
The workflow installs frontend dependencies, creates a production React build, compiles backend Python files, runs structural checks, and builds both Docker images. Deployment is not automatic until hosting credentials and deployment secrets are configured. See `DEPLOYMENT.md`.

## Notes
- The original Flask templates are retained in the repository as a migration reference, but the active browser interface is now the React SPA and its JSON API is served by FastAPI.
- This package requires a real Docker build and end-to-end test on the target laptop before it can be considered production verified. Check logs with `docker compose logs -f` if startup fails.
