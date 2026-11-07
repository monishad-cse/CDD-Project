import os
import unittest
import uuid

import mysql.connector

import app as attendance_app


@unittest.skipUnless(
    os.environ.get("RUN_MYSQL_INTEGRATION") == "1"
    and attendance_app.DB_TYPE == "mysql",
    "Set RUN_MYSQL_INTEGRATION=1 to run against the Compose MySQL database.",
)
class MySQLBackendIntegrationTests(unittest.TestCase):
    def test_mysql_persists_subject_attendance_and_upserts(self):
        connection = attendance_app.MySQLConnection(mysql.connector.connect(
            host=os.environ["MYSQL_HOST"],
            port=int(os.environ.get("MYSQL_PORT", "3306")),
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            database=os.environ["MYSQL_DATABASE"],
            charset="utf8mb4",
        ))
        try:
            suffix = uuid.uuid4().hex[:16]
            connection.execute(
                """INSERT INTO users(username, password_hash, role)
                   VALUES (?, ?, 'admin')""",
                (f"mysql_admin_{suffix}", "test-hash"),
            )
            cursor = connection.execute(
                """INSERT INTO users(username, password_hash, role)
                   VALUES (?, ?, 'faculty')""",
                (f"mysql_test_{suffix}", "test-hash"),
            )
            faculty_id = cursor.lastrowid
            cursor = connection.execute(
                """INSERT INTO students
                   (user_id, name, roll_no, department, semester)
                   VALUES (?, ?, ?, ?, ?)""",
                (faculty_id, "MySQL Integration Student", f"IT{suffix}", "CSE", 3),
            )
            student_id = cursor.lastrowid
            cursor = connection.execute(
                "INSERT INTO subjects(user_id, name) VALUES (?, ?)",
                (faculty_id, f"MySQL Subject {suffix}"),
            )
            subject_id = cursor.lastrowid

            attendance_sql = """INSERT INTO attendance
                (student_id, subject_id, date, status) VALUES (?, ?, ?, ?)
                ON CONFLICT(student_id, subject_id, date)
                DO UPDATE SET status = excluded.status"""
            connection.execute(
                attendance_sql,
                (student_id, subject_id, "2026-10-31", "Present"),
            )
            connection.execute(
                attendance_sql,
                (student_id, subject_id, "2026-10-31", "Absent"),
            )
            row = connection.execute(
                """SELECT status FROM attendance
                   WHERE student_id = ? AND subject_id = ? AND date = ?""",
                (student_id, subject_id, "2026-10-31"),
            ).fetchone()
            self.assertEqual(row["status"], "Absent")
        finally:
            connection.rollback()
            connection.close()


if __name__ == "__main__":
    unittest.main()
