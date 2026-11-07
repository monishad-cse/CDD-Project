# EduTrack — Smart Attendance and Student Performance

A beginner-friendly CDD demonstration project built with Python Flask, MySQL, HTML, Bootstrap, Docker, GitHub, and GitHub Actions. SQLite is not used.

## Features
- Dashboard with student and record counts
- Add student details
- Mark daily attendance (Present/Absent)
- Add subject marks
- Report showing attendance percentage and average marks
- Admin, faculty, and student roles with role-checked login and logout
- Student self-registration assigned to a faculty account; Admin creates faculty accounts
- Admin role with system-wide faculty, student, attendance, marks, and reporting access
- Faculty-created student logins and role-specific pages
- Attendance tracked by subject, with low-attendance alerts below 75%
- Demo subjects and sample attendance for testing alerts
- Persistent MySQL database for Docker Compose
- Docker container support
- GitHub Actions workflow to check Python syntax and build the Docker image

## Run in VS Code without Docker
1. Install Python 3.10 or newer, VS Code, and MySQL 8.4.
2. Create a MySQL database and user using the values you plan to place in `.env`.
3. Create and activate a virtual environment: `py -m venv .venv` then `.venv\Scripts\Activate.ps1`.
4. Install packages: `pip install -r requirements.txt`.
5. Set `MYSQL_HOST=127.0.0.1`, `MYSQL_PORT=3306`, `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`, and `SECRET_KEY` in `.env`.
6. Start: `python app.py`.
7. Open `http://127.0.0.1:5000` in your browser.

EduTrack uses MySQL only. The first faculty account is provisioned by an Admin and receives the three demo subjects: Data Structures, Database Systems, and Operating Systems. Faculty can create student logins from the Students page, or students can use the Register page to create an account assigned to a faculty account. Students see their own subject attendance, alerts, and marks.

## Run using Docker
1. Install Docker Desktop and start it.
2. Open the project folder in VS Code terminal.
3. Create a local environment file and replace the example values with long random secrets:
   ```powershell
   Copy-Item .env.example .env
   ```
4. Run: `docker compose up --build -d`
5. Open `http://127.0.0.1:5000` and log in as the provisioned Admin, then create faculty accounts. Students can register from the link below the login form.
6. Stop with `docker compose down`. The named `mysql-data` volume keeps the MySQL database when the containers are stopped or recreated.

Compose runs MySQL 8.4 and waits for its health check before starting Flask. The app stores user accounts, subjects, attendance, and marks in MySQL. Passwords are stored as secure hashes. Choose the matching account role from the login dropdown; the selected role must match the role stored for that username. Faculty manage their own student records, students can view only their own attendance and marks, and admins can view/manage data across faculty accounts. The application connects directly to MySQL; no SQLite database or migration is used. The `.env` file is local-only and must not be committed. Set `COOKIE_SECURE=true` when serving the app over HTTPS.

To provision an administrator, set `ADMIN_USERNAME` and `ADMIN_PASSWORD` in `.env`; the app creates that account once during startup and never resets an existing admin password. The requested demo credentials are weak and should only be used locally; sign in and use **Change password** to set a unique, long password before exposing the app beyond your machine. Admins create faculty accounts from the Faculty page and assign subjects from the Subjects page. Public registration creates student accounts only and requires a faculty assignment.

To clear the database, run `docker compose down --volumes`; this permanently deletes all accounts and records stored in the MySQL volume.

## Push to GitHub
Create an empty GitHub repository named `smart-attendance-performance`. In the VS Code terminal run:
```bash
git init
git add .
git commit -m "Initial smart attendance project"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/smart-attendance-performance.git
git push -u origin main
```
Replace `YOUR-USERNAME` with your GitHub username. On every push, GitHub Actions runs the workflow in `.github/workflows/ci.yml`. Open the repository's Actions tab to see the result.

## Demo flow
1. Sign in as Admin and create faculty accounts from the Faculty page.
2. Assign subjects to faculty on the Subjects page.
3. Faculty can add students and create their accounts; students can also self-register from the Register link by selecting their faculty.
4. Sign in as a student to view personal attendance alerts and marks.
5. Faculty or Admin can mark attendance by student, subject, date, and status; the student's warning updates automatically.
6. Open Reports to review per-subject attendance and marks.

## Important limitations
This is a classroom/demo version. It does not include password recovery, email verification, biometric attendance, parent notifications, or cloud deployment. Do not enter real sensitive student data. Use a production WSGI server, HTTPS, and a securely managed signing key before any real institutional use.
