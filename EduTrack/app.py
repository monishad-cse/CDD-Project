from contextlib import contextmanager
from datetime import date, timedelta
from functools import wraps
import math
import os
import re
import secrets

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
import mysql.connector
from mysql.connector import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or "local-development-only-change-this",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "").lower() == "true",
)
DB_TYPE = "mysql"  # EduTrack uses MySQL exclusively.
DB_INTEGRITY_ERRORS = (IntegrityError,)
ATTENDANCE_THRESHOLD = 75
DEMO_SUBJECTS = (
    "Data Structures",
    "Database Systems",
    "Operating Systems",
)


class HybridRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


class MySQLCursor:
    def __init__(self, connection, query, params=(), many=False):
        self.cursor = connection.cursor(dictionary=True)
        query = query.replace("?", "%s")
        if query.lstrip().upper().startswith("INSERT OR IGNORE"):
            query = query.replace("INSERT OR IGNORE", "INSERT IGNORE", 1)
        if many:
            self.cursor.executemany(query, params)
        else:
            self.cursor.execute(query, params)

    @property
    def rowcount(self):
        return self.cursor.rowcount

    @property
    def lastrowid(self):
        return self.cursor.lastrowid

    def fetchone(self):
        row = self.cursor.fetchone()
        return HybridRow(row) if row is not None else None

    def fetchall(self):
        return [HybridRow(row) for row in self.cursor.fetchall()]


class MySQLConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, params=()):
        query = re.sub(
            r"ON CONFLICT\(student_id,\s*subject_id,\s*date\)\s+"
            r"DO UPDATE SET status = excluded\.status",
            "ON DUPLICATE KEY UPDATE status = VALUES(status)",
            query,
        )
        return MySQLCursor(self.connection, query, params)

    def executemany(self, query, params):
        return MySQLCursor(self.connection, query, params, many=True)

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


@contextmanager
def connect():
    """Open a MySQL connection and commit or roll back the transaction."""
    raw_connection = mysql.connector.connect(
        host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ.get("MYSQL_USER", "attendance"),
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ.get("MYSQL_DATABASE", "smart_attendance"),
        charset="utf8mb4",
        connection_timeout=10,
    )
    con = MySQLConnection(raw_connection)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def table_columns(con, table):
    """Return column names for a table in the selected MySQL database."""
    rows = con.execute(
        """SELECT COLUMN_NAME FROM information_schema.columns
           WHERE table_schema = DATABASE() AND table_name = ?""",
        (table,),
    ).fetchall()
    return {row["COLUMN_NAME"] for row in rows}

def init_mysql_db():
    with connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(32) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(16) NOT NULL DEFAULT 'faculty',
                CONSTRAINT users_role_check CHECK (role IN ('faculty', 'student', 'admin'))
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """)
        if "role" not in table_columns(con, "users"):
            con.execute(
                "ALTER TABLE users ADD COLUMN role VARCHAR(16) NOT NULL DEFAULT 'faculty'"
            )
        role_check = con.execute(
            """SELECT tc.CONSTRAINT_NAME, cc.CHECK_CLAUSE
               FROM information_schema.TABLE_CONSTRAINTS tc
               JOIN information_schema.CHECK_CONSTRAINTS cc
                 ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA
                AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
               WHERE tc.TABLE_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'users'
                 AND tc.CONSTRAINT_TYPE = 'CHECK'
                 AND tc.CONSTRAINT_NAME = 'users_role_check'"""
        ).fetchone()
        if role_check and "admin" not in role_check["CHECK_CLAUSE"].lower():
            con.execute("ALTER TABLE users DROP CHECK users_role_check")
            con.execute(
                """ALTER TABLE users ADD CONSTRAINT users_role_check
                   CHECK (role IN ('faculty', 'student', 'admin'))"""
            )
        con.execute("""
            CREATE TABLE IF NOT EXISTS students (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                user_id BIGINT UNSIGNED NULL,
                account_id BIGINT UNSIGNED NULL UNIQUE,
                name VARCHAR(255) NOT NULL,
                roll_no VARCHAR(64) NOT NULL,
                department VARCHAR(64) NOT NULL,
                semester TINYINT UNSIGNED NOT NULL,
                UNIQUE KEY students_user_roll_unique (user_id, roll_no),
                CONSTRAINT students_owner_fk FOREIGN KEY (user_id) REFERENCES users(id),
                CONSTRAINT students_account_fk FOREIGN KEY (account_id) REFERENCES users(id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS subjects (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                user_id BIGINT UNSIGNED NULL,
                name VARCHAR(100) NOT NULL,
                UNIQUE KEY subjects_owner_name_unique (user_id, name),
                CONSTRAINT subjects_owner_fk FOREIGN KEY (user_id) REFERENCES users(id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                student_id BIGINT UNSIGNED NOT NULL,
                subject_id BIGINT UNSIGNED NOT NULL,
                date DATE NOT NULL,
                status VARCHAR(16) NOT NULL,
                UNIQUE KEY attendance_student_subject_date_unique
                    (student_id, subject_id, date),
                CONSTRAINT attendance_status_check
                    CHECK (status IN ('Present', 'Absent')),
                CONSTRAINT attendance_student_fk
                    FOREIGN KEY (student_id) REFERENCES students(id),
                CONSTRAINT attendance_subject_fk
                    FOREIGN KEY (subject_id) REFERENCES subjects(id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS marks (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                student_id BIGINT UNSIGNED NOT NULL,
                subject VARCHAR(100) NOT NULL,
                marks DECIMAL(7, 2) NOT NULL,
                max_marks DECIMAL(7, 2) NOT NULL,
                CONSTRAINT marks_student_fk
                    FOREIGN KEY (student_id) REFERENCES students(id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """)


def init_db():
    """Create or update the MySQL schema used by EduTrack."""
    init_mysql_db()


def bootstrap_admin_account():
    username = os.environ.get("ADMIN_USERNAME", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not username and not password:
        return
    if not username or not password:
        raise RuntimeError("Set both ADMIN_USERNAME and ADMIN_PASSWORD to bootstrap an admin.")
    if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
        raise RuntimeError("ADMIN_USERNAME must be 3–32 letters, numbers, or underscores.")

    with connect() as con:
        existing = con.execute(
            "SELECT role FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing:
            if existing["role"] != "admin":
                raise RuntimeError(
                    "ADMIN_USERNAME is already assigned to a non-admin account."
                )
            return
        con.execute(
            """INSERT INTO users(username, password_hash, role)
               VALUES (?, ?, 'admin')""",
            (username, generate_password_hash(password)),
        )


def ensure_demo_subjects(con, faculty_id):
    con.executemany(
        "INSERT OR IGNORE INTO subjects(user_id, name) VALUES (?, ?)",
        [(faculty_id, name) for name in DEMO_SUBJECTS],
    )
    return con.execute(
        "SELECT id, name FROM subjects WHERE user_id = ? ORDER BY id",
        (faculty_id,),
    ).fetchall()


def seed_demo_attendance(con, faculty_id, students, subjects):
    recorded = con.execute(
        """SELECT COUNT(*) AS total FROM attendance a
           JOIN students s ON s.id = a.student_id
           WHERE s.user_id = ?""",
        (faculty_id,),
    ).fetchone()["total"]
    if recorded or not students:
        return

    dates = [(date.today() - timedelta(days=offset)).isoformat() for offset in range(4, 0, -1)]
    entries = []
    for student_index, student in enumerate(students):
        for subject in subjects:
            for day_index, recorded_date in enumerate(dates):
                is_present = student_index != 0 or day_index >= 2
                entries.append((
                    student["id"],
                    subject["id"],
                    recorded_date,
                    "Present" if is_present else "Absent",
                ))
    con.executemany(
        """INSERT OR IGNORE INTO attendance(student_id, subject_id, date, status)
           VALUES (?, ?, ?, ?)""",
        entries,
    )


def csrf_token():
    token = session.get("_csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@app.context_processor
def inject_template_values():
    return {
        "csrf_token": csrf_token,
        "current_username": session.get("username"),
        "current_role": session.get("role"),
        "attendance_threshold": ATTENDANCE_THRESHOLD,
    }


@app.before_request
def protect_post_requests():
    if request.method == "POST":
        submitted_token = request.form.get("_csrf_token", "")
        expected_token = session.get("_csrf_token", "")
        if not expected_token or not secrets.compare_digest(
            submitted_token, expected_token
        ):
            abort(400, description="The form expired. Reload the page and try again.")


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        return view(**kwargs)

    return wrapped_view


def role_required(role):
    allowed_roles = {role} if isinstance(role, str) else set(role)

    def decorate(view):
        @wraps(view)
        def wrapped_view(**kwargs):
            if "user_id" not in session:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login"))
            if session.get("role") not in allowed_roles:
                abort(403)
            return view(**kwargs)

        return wrapped_view

    return decorate


def create_student_account(con, faculty_id, student_id, username, password):
    if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
        raise ValueError("Username must be 3–32 letters, numbers, or underscores.")
    if len(password) < 8 or len(password) > 128:
        raise ValueError("Student password must be between 8 and 128 characters.")

    student = con.execute(
        """SELECT id, account_id FROM students
           WHERE id = ? AND user_id = ?""",
        (student_id, faculty_id),
    ).fetchone()
    if student is None:
        raise ValueError("Student was not found in your class.")
    if student["account_id"] is not None:
        raise ValueError("This student already has a login account.")

    cursor = con.execute(
        """INSERT INTO users(username, password_hash, role)
           VALUES (?, ?, 'student')""",
        (username, generate_password_hash(password)),
    )
    con.execute(
        "UPDATE students SET account_id = ? WHERE id = ?",
        (cursor.lastrowid, student_id),
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        faculty_id = request.form.get("faculty_id", "")
        name = request.form.get("name", "").strip()
        roll_no = request.form.get("roll_no", "").strip().upper()
        department = request.form.get("department", "").strip().upper()
        semester = request.form.get("semester", "")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        try:
            faculty_id = int(faculty_id)
        except ValueError:
            faculty_id = None
        try:
            semester = int(semester)
        except ValueError:
            semester = None

        if not name or len(name) > 255 or not roll_no or len(roll_no) > 64:
            flash("Enter your name and a valid roll number.", "danger")
        elif not department or len(department) > 64:
            flash("Enter a valid department.", "danger")
        elif semester is None or not 1 <= semester <= 8:
            flash("Choose a semester between 1 and 8.", "danger")
        elif not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
            flash("Username must be 3–32 letters, numbers, or underscores.", "danger")
        elif len(password) < 8 or len(password) > 128:
            flash("Password must be between 8 and 128 characters long.", "danger")
        elif password != confirm_password:
            flash("Passwords do not match.", "danger")
        else:
            try:
                with connect() as con:
                    faculty = con.execute(
                        "SELECT id FROM users WHERE id = ? AND role = 'faculty'",
                        (faculty_id,),
                    ).fetchone()
                    if faculty is None:
                        raise ValueError("Select an available faculty account.")
                    user_cursor = con.execute(
                        """INSERT INTO users(username, password_hash, role)
                           VALUES (?, ?, 'student')""",
                        (username, generate_password_hash(password)),
                    )
                    user_id = user_cursor.lastrowid
                    con.execute(
                        """INSERT INTO students
                           (user_id, account_id, name, roll_no, department, semester)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (faculty_id, user_id, name, roll_no, department, semester),
                    )
            except DB_INTEGRITY_ERRORS:
                flash("That username or roll number is already registered.", "danger")
            except ValueError as exc:
                flash(str(exc), "danger")
            else:
                session.clear()
                session["user_id"] = user_id
                session["username"] = username
                session["role"] = "student"
                flash("Your student account has been created.", "success")
                return redirect(url_for("dashboard"))

    with connect() as con:
        faculties = con.execute(
            "SELECT id, username FROM users WHERE role = 'faculty' ORDER BY username"
        ).fetchall()
    return render_template("register.html", faculties=faculties)


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        selected_role = request.form.get("role", "")
        with connect() as con:
            user = con.execute(
                "SELECT id, username, password_hash, role FROM users WHERE username = ?",
                (username,),
            ).fetchone()

        if (
            selected_role not in {"faculty", "admin", "student"}
            or user is None
            or user["role"] != selected_role
            or not check_password_hash(user["password_hash"], password)
        ):
            flash("Invalid username or password.", "danger")
        else:
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            flash("You are now logged in.", "success")
            return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        with connect() as con:
            user = con.execute(
                "SELECT password_hash FROM users WHERE id = ?",
                (session["user_id"],),
            ).fetchone()
            if user is None:
                session.clear()
                abort(403)
            if not check_password_hash(user["password_hash"], current_password):
                flash("Current password is incorrect.", "danger")
            elif len(new_password) < 8 or len(new_password) > 128:
                flash("New password must be between 8 and 128 characters long.", "danger")
            elif new_password != confirm_password:
                flash("New passwords do not match.", "danger")
            else:
                con.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (generate_password_hash(new_password), session["user_id"]),
                )
                flash("Your password has been updated.", "success")
                return redirect(url_for("dashboard"))
    return render_template("change_password.html")


def student_attendance_rows(con, faculty_id=None, student_id=None):
    query = """
        SELECT s.id AS student_id, s.name, s.roll_no, subjects.name AS subject,
               MIN(faculty.username) AS faculty_username,
               COUNT(a.id) AS total_classes,
               SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END) AS present_classes
        FROM students s
        JOIN subjects ON subjects.user_id = s.user_id
        LEFT JOIN users AS faculty ON faculty.id = s.user_id
        LEFT JOIN attendance a
            ON a.student_id = s.id AND a.subject_id = subjects.id
    """
    params = []
    if faculty_id is not None:
        query += " WHERE s.user_id = ?"
        params.append(faculty_id)
    if student_id is not None:
        query += " AND s.id = ?" if faculty_id is not None else " WHERE s.id = ?"
        params.append(student_id)
    query += " GROUP BY s.id, subjects.id ORDER BY s.roll_no, subjects.name"
    summary = []
    for row in con.execute(query, params).fetchall():
        total = row["total_classes"] or 0
        present = row["present_classes"] or 0
        percentage = round(present * 100 / total, 1) if total else 0
        summary.append({
            "student_id": row["student_id"],
            "name": row["name"],
            "roll_no": row["roll_no"],
            "faculty_username": row["faculty_username"],
            "subject": row["subject"],
            "total_classes": total,
            "present_classes": present,
            "percentage": percentage,
            "low": total > 0 and percentage < ATTENDANCE_THRESHOLD,
        })
    return summary


@app.route("/")
def home():
    """Public landing page; authentication happens on the dedicated login page."""
    return render_template("home.html")


@app.route("/dashboard")
@login_required
def dashboard():
    user_id = session["user_id"]
    role = session["role"]
    with connect() as con:
        if role == "student":
            student = con.execute(
                """SELECT id, user_id, name, roll_no, account_id
                   FROM students WHERE account_id = ?""",
                (user_id,),
            ).fetchone()
            if student is None:
                abort(403)
            summary = student_attendance_rows(
                con, student["user_id"], student["id"]
            )
            low_attendance = [row for row in summary if row["low"]]
            marks = con.execute(
                """SELECT subject, marks, max_marks
                   FROM marks WHERE student_id = ? ORDER BY subject""",
                (student["id"],),
            ).fetchall()
            return render_template(
                "dashboard.html",
                role=role,
                student=student,
                summary=summary,
                low_attendance=low_attendance,
                marks=marks,
                students=1,
                attendance_count=sum(row["total_classes"] for row in summary),
                marks_count=len(marks),
            )

        if role == "admin":
            students = con.execute(
                "SELECT COUNT(*) AS total FROM students"
            ).fetchone()["total"]
            attendance_count = con.execute(
                "SELECT COUNT(*) AS total FROM attendance"
            ).fetchone()["total"]
            marks_count = con.execute(
                "SELECT COUNT(*) AS total FROM marks"
            ).fetchone()["total"]
            faculty_count = con.execute(
                "SELECT COUNT(*) AS total FROM users WHERE role = 'faculty'"
            ).fetchone()["total"]
            summary = student_attendance_rows(con)
        else:
            students = con.execute(
                "SELECT COUNT(*) AS total FROM students WHERE user_id = ?", (user_id,)
            ).fetchone()["total"]
            attendance_count = con.execute(
                """SELECT COUNT(*) AS total FROM attendance a
                   JOIN students s ON s.id = a.student_id
                   WHERE s.user_id = ?""",
                (user_id,),
            ).fetchone()["total"]
            marks_count = con.execute(
                """SELECT COUNT(*) AS total FROM marks m
                   JOIN students s ON s.id = m.student_id
                   WHERE s.user_id = ?""",
                (user_id,),
            ).fetchone()["total"]
            faculty_count = 0
            summary = student_attendance_rows(con, user_id)
        low_attendance = [row for row in summary if row["low"]]

    return render_template(
        "dashboard.html",
        role=role,
        student=None,
        students=students,
        attendance_count=attendance_count,
        marks_count=marks_count,
        faculty_count=faculty_count,
        summary=summary,
        low_attendance=low_attendance,
        marks=[],
    )


@app.route("/students", methods=["GET", "POST"])
@role_required(("faculty", "admin"))
def students():
    is_admin = session["role"] == "admin"
    faculty_id = session["user_id"]
    if request.method == "POST":
        owner_id = faculty_id
        if is_admin:
            selected_faculty = request.form.get("faculty_id", "")
            try:
                owner_id = int(selected_faculty)
            except ValueError:
                owner_id = None
            with connect() as con:
                valid_owner = owner_id is not None and con.execute(
                    "SELECT 1 FROM users WHERE id = ? AND role = 'faculty'",
                    (owner_id,),
                ).fetchone()
            if not valid_owner:
                flash("Select a valid faculty account for this student.", "danger")
                return redirect(url_for("students"))

        name = request.form.get("name", "").strip()
        roll = request.form.get("roll_no", "").strip().upper()
        dept = request.form.get("department", "").strip().upper()
        semester = request.form.get("semester", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("student_password", "")

        if not name or not roll or not dept or not semester.isdigit():
            flash("Enter the student's details and a valid semester.", "danger")
        elif not 1 <= int(semester) <= 8:
            flash("Semester must be between 1 and 8.", "danger")
        else:
            try:
                with connect() as con:
                    cursor = con.execute(
                        """INSERT INTO students
                           (user_id, name, roll_no, department, semester)
                           VALUES (?, ?, ?, ?, ?)""",
                        (owner_id, name, roll, dept, int(semester)),
                    )
                    create_student_account(
                            con, owner_id, cursor.lastrowid, username, password
                    )
                flash(
                    f"Student added. Share login '{username}' and the password with them.",
                    "success",
                )
            except ValueError as exc:
                flash(str(exc), "danger")
            except DB_INTEGRITY_ERRORS:
                flash("That roll number or username already exists.", "danger")
        return redirect(url_for("students"))

    with connect() as con:
        query = """SELECT students.*, student_user.username AS student_username,
                          faculty.username AS faculty_username
                   FROM students
                   LEFT JOIN users AS student_user ON student_user.id = students.account_id
                   LEFT JOIN users AS faculty ON faculty.id = students.user_id"""
        params = ()
        if not is_admin:
            query += " WHERE students.user_id = ?"
            params = (faculty_id,)
        rows = con.execute(query + " ORDER BY students.roll_no", params).fetchall()
        faculties = (
            con.execute(
                "SELECT id, username FROM users WHERE role = 'faculty' ORDER BY username"
            ).fetchall()
            if is_admin else []
        )
    return render_template(
        "students.html", students=rows, faculties=faculties, role=session["role"]
    )


@app.route("/students/<int:student_id>/account", methods=["POST"])
@role_required(("faculty", "admin"))
def create_existing_student_account(student_id):
    username = request.form.get("username", "").strip()
    password = request.form.get("student_password", "")
    try:
        with connect() as con:
            faculty_id = session["user_id"]
            if session["role"] == "admin":
                student = con.execute(
                    "SELECT user_id FROM students WHERE id = ?", (student_id,)
                ).fetchone()
                if student is None or student["user_id"] is None:
                    raise ValueError("This student must be assigned to faculty first.")
                faculty_id = student["user_id"]
            create_student_account(
                con, faculty_id, student_id, username, password
            )
    except ValueError as exc:
        flash(str(exc), "danger")
    except DB_INTEGRITY_ERRORS:
        flash("That username is already taken.", "danger")
    else:
        flash(f"Student login '{username}' created.", "success")
    return redirect(url_for("students"))


@app.route("/attendance", methods=["GET", "POST"])
@role_required(("faculty", "admin"))
def attendance():
    faculty_id = session["user_id"]
    is_admin = session["role"] == "admin"
    if request.method == "POST":
        student_id = request.form.get("student_id", "")
        subject_id = request.form.get("subject_id", "")
        recorded_date = request.form.get("date", "")
        status = request.form.get("status", "")
        try:
            date.fromisoformat(recorded_date)
        except ValueError:
            flash("Enter a valid attendance date.", "danger")
        else:
            if status not in {"Present", "Absent"}:
                flash("Choose Present or Absent.", "danger")
            else:
                with connect() as con:
                    scope_filter = "" if is_admin else "AND students.user_id = ?"
                    params = [
                        recorded_date, status, student_id, subject_id
                    ]
                    if not is_admin:
                        params.append(faculty_id)
                    cursor = con.execute(
                        f"""INSERT INTO attendance(student_id, subject_id, date, status)
                           SELECT students.id, subjects.id, ?, ?
                           FROM students JOIN subjects
                               ON subjects.user_id = students.user_id
                           WHERE students.id = ? AND subjects.id = ? {scope_filter}
                           ON CONFLICT(student_id, subject_id, date)
                           DO UPDATE SET status = excluded.status""",
                        params,
                    )
                    attendance_saved = cursor.rowcount or con.execute(
                           """SELECT 1 FROM attendance
                              WHERE student_id = ? AND subject_id = ? AND date = ?""",
                           (student_id, subject_id, recorded_date),
                    ).fetchone()
                if attendance_saved:
                    flash("Subject attendance saved.", "success")
                else:
                    flash("Select one of your students and a valid subject.", "danger")
        return redirect(url_for("attendance"))

    with connect() as con:
        if not is_admin:
            subjects = ensure_demo_subjects(con, faculty_id)
        student_query = """SELECT students.*, faculty.username AS faculty_username
                           FROM students
                           LEFT JOIN users AS faculty ON faculty.id = students.user_id"""
        subject_query = """SELECT subjects.*, faculty.username AS faculty_username
                           FROM subjects
                           LEFT JOIN users AS faculty ON faculty.id = subjects.user_id"""
        attendance_query = """SELECT a.date, a.status, s.name, s.roll_no,
                                     subjects.name AS subject,
                                     faculty.username AS faculty_username
               FROM attendance a
               JOIN students s ON s.id = a.student_id
               JOIN subjects ON subjects.id = a.subject_id
               LEFT JOIN users AS faculty ON faculty.id = s.user_id"""
        params = ()
        if is_admin:
            student_rows = con.execute(student_query + " ORDER BY students.roll_no").fetchall()
            subjects = con.execute(subject_query + " ORDER BY subjects.name").fetchall()
            rows = con.execute(
                attendance_query + " ORDER BY a.date DESC, subjects.name, s.roll_no LIMIT 100"
            ).fetchall()
        else:
            student_rows = con.execute(
                student_query + " WHERE students.user_id = ? ORDER BY students.roll_no",
                (faculty_id,),
            ).fetchall()
            subjects = con.execute(
                subject_query + " WHERE subjects.user_id = ? ORDER BY subjects.name",
                (faculty_id,),
            ).fetchall()
            rows = con.execute(
                attendance_query
                + " WHERE s.user_id = ? ORDER BY a.date DESC, subjects.name, s.roll_no LIMIT 100",
                (faculty_id,),
            ).fetchall()
    return render_template(
        "attendance.html", students=student_rows, subjects=subjects, rows=rows,
        role=session["role"]
    )


@app.route("/performance", methods=["GET", "POST"])
@login_required
def performance():
    user_id = session["user_id"]
    role = session["role"]
    is_admin = role == "admin"
    if role == "student":
        if request.method == "POST":
            abort(403)
        with connect() as con:
            student = con.execute(
                "SELECT id FROM students WHERE account_id = ?", (user_id,)
            ).fetchone()
            if student is None:
                abort(403)
            rows = con.execute(
                """SELECT subject, marks, max_marks FROM marks
                   WHERE student_id = ? ORDER BY subject""",
                (student["id"],),
            ).fetchall()
        return render_template("performance.html", role=role, rows=rows)

    if request.method == "POST":
        student_id = request.form.get("student_id", "")
        subject = request.form.get("subject", "").strip()
        try:
            marks = float(request.form.get("marks", ""))
            maximum = float(request.form.get("max_marks", ""))
        except ValueError:
            marks = maximum = math.nan

        if not subject or not math.isfinite(marks) or not math.isfinite(maximum):
            flash("Enter a subject and valid numeric marks.", "danger")
        elif maximum <= 0 or marks < 0 or marks > maximum:
            flash("Enter valid marks (0 to maximum marks).", "danger")
        else:
            with connect() as con:
                scope_filter = "" if is_admin else "AND user_id = ?"
                params = [subject, marks, maximum, student_id]
                if not is_admin:
                    params.append(user_id)
                cursor = con.execute(
                    f"""INSERT INTO marks(student_id, subject, marks, max_marks)
                       SELECT id, ?, ?, ? FROM students
                       WHERE id = ? {scope_filter}""",
                    params,
                )
            if cursor.rowcount:
                flash("Marks saved.", "success")
            else:
                flash("Select a student from your account.", "danger")
        return redirect(url_for("performance"))

    with connect() as con:
        student_query = """SELECT students.*, faculty.username AS faculty_username
                           FROM students
                           LEFT JOIN users AS faculty ON faculty.id = students.user_id"""
        marks_query = """SELECT m.subject, m.marks, m.max_marks, s.name, s.roll_no,
                                faculty.username AS faculty_username
               FROM marks m JOIN students s ON s.id = m.student_id
               LEFT JOIN users AS faculty ON faculty.id = s.user_id"""
        if is_admin:
            student_rows = con.execute(
                student_query + " ORDER BY students.roll_no"
            ).fetchall()
            rows = con.execute(marks_query + " ORDER BY m.id DESC").fetchall()
        else:
            student_rows = con.execute(
                student_query + " WHERE students.user_id = ? ORDER BY students.roll_no",
                (user_id,),
            ).fetchall()
            rows = con.execute(
                marks_query + " WHERE s.user_id = ? ORDER BY m.id DESC",
                (user_id,),
            ).fetchall()
    return render_template(
        "performance.html", role=role, students=student_rows, rows=rows
    )


@app.route("/admin/faculty", methods=["GET", "POST"])
@role_required("admin")
def admin_faculty():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
            flash("Username must be 3–32 letters, numbers, or underscores.", "danger")
        elif len(password) < 8 or len(password) > 128:
            flash("Password must be between 8 and 128 characters long.", "danger")
        elif password != confirm_password:
            flash("Passwords do not match.", "danger")
        else:
            try:
                with connect() as con:
                    con.execute(
                        """INSERT INTO users(username, password_hash, role)
                           VALUES (?, ?, 'faculty')""",
                        (username, generate_password_hash(password)),
                    )
            except DB_INTEGRITY_ERRORS:
                flash("That username is already taken.", "danger")
            else:
                flash(f"Faculty account '{username}' created.", "success")
                return redirect(url_for("admin_faculty"))

    with connect() as con:
        faculties = con.execute(
            """SELECT users.username, COUNT(students.id) AS student_count
               FROM users LEFT JOIN students ON students.user_id = users.id
               WHERE users.role = 'faculty'
               GROUP BY users.id, users.username ORDER BY users.username"""
        ).fetchall()
    return render_template("faculty.html", faculties=faculties)


@app.route("/admin/subjects", methods=["GET", "POST"])
@role_required("admin")
def admin_subjects():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        faculty_id = request.form.get("faculty_id", "")
        try:
            faculty_id = int(faculty_id)
        except ValueError:
            faculty_id = None
        if not name or len(name) > 100:
            flash("Enter a subject name of 1–100 characters.", "danger")
        elif faculty_id is None:
            flash("Select a valid faculty account.", "danger")
        else:
            try:
                with connect() as con:
                    faculty = con.execute(
                        "SELECT 1 FROM users WHERE id = ? AND role = 'faculty'",
                        (faculty_id,),
                    ).fetchone()
                    if faculty is None:
                        raise ValueError("Select a valid faculty account.")
                    con.execute(
                        "INSERT INTO subjects(user_id, name) VALUES (?, ?)",
                        (faculty_id, name),
                    )
            except DB_INTEGRITY_ERRORS:
                flash("That subject already exists for the selected faculty.", "danger")
            except ValueError as exc:
                flash(str(exc), "danger")
            else:
                flash(f"Subject '{name}' added.", "success")
                return redirect(url_for("admin_subjects"))

    with connect() as con:
        faculties = con.execute(
            "SELECT id, username FROM users WHERE role = 'faculty' ORDER BY username"
        ).fetchall()
        subjects = con.execute(
            """SELECT subjects.name, users.username AS faculty_username
               FROM subjects LEFT JOIN users ON users.id = subjects.user_id
               ORDER BY users.username, subjects.name"""
        ).fetchall()
    return render_template(
        "subjects.html", faculties=faculties, subjects=subjects
    )


@app.route("/report")
@role_required(("faculty", "admin"))
def report():
    faculty_id = session["user_id"]
    is_admin = session["role"] == "admin"
    with connect() as con:
        student_query = """SELECT students.*, faculty.username AS faculty_username
                           FROM students
                           LEFT JOIN users AS faculty ON faculty.id = students.user_id"""
        if is_admin:
            students = con.execute(student_query + " ORDER BY students.roll_no").fetchall()
            subject_summary = student_attendance_rows(con)
        else:
            students = con.execute(
                student_query + " WHERE students.user_id = ? ORDER BY students.roll_no",
                (faculty_id,),
            ).fetchall()
            subject_summary = student_attendance_rows(con, faculty_id)
        report_rows = []
        for student in students:
            marks = con.execute(
                """SELECT AVG(marks * 100.0 / max_marks) AS average
                   FROM marks WHERE student_id = ?""",
                (student["id"],),
            ).fetchone()
            report_rows.append({
                "name": student["name"],
                "roll_no": student["roll_no"],
                "faculty_username": student["faculty_username"],
                "average": (
                    round(marks["average"], 1)
                    if marks["average"] is not None
                    else None
                ),
            })
    return render_template(
        "report.html", rows=report_rows, subject_summary=subject_summary,
        role=session["role"]
    )


if __name__ == "__main__":
    init_db()
    bootstrap_admin_account()
    app.run(host="0.0.0.0", port=5000, debug=False)
