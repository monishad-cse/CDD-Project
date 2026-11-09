"""EduTrack REST API: React frontend + FastAPI + MySQL."""
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from email.message import EmailMessage
import os
import smtplib
import sys

from fastapi import FastAPI, Depends, HTTPException, Response, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import check_password_hash, generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import app as legacy_app

TOKEN_COOKIE = "edutrack_session"
serializer = URLSafeTimedSerializer(os.getenv("SECRET_KEY", "local-development-change-this"), salt="edutrack-api")


def ensure_extra_schema():
    with legacy_app.connect() as con:
        cols = legacy_app.table_columns(con, "students")
        if "email" not in cols:
            con.execute("ALTER TABLE students ADD COLUMN email VARCHAR(255) NULL")
        if "skills" not in cols:
            con.execute("ALTER TABLE students ADD COLUMN skills VARCHAR(1000) NULL")
        con.execute("""CREATE TABLE IF NOT EXISTS internships (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            title VARCHAR(180) NOT NULL, company VARCHAR(180) NOT NULL,
            description TEXT NOT NULL, skills VARCHAR(500) NOT NULL,
            min_semester TINYINT UNSIGNED NOT NULL DEFAULT 5,
            max_semester TINYINT UNSIGNED NOT NULL DEFAULT 6,
            application_url VARCHAR(500) NULL, active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        con.execute("""CREATE TABLE IF NOT EXISTS notification_log (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            student_id BIGINT UNSIGNED NOT NULL, category VARCHAR(40) NOT NULL,
            message VARCHAR(500) NOT NULL, sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT notification_student_fk FOREIGN KEY (student_id) REFERENCES students(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")


@asynccontextmanager
async def lifespan(_: FastAPI):
    legacy_app.init_db()
    ensure_extra_schema()
    legacy_app.bootstrap_admin_account()
    yield

api = FastAPI(title="EduTrack API", version="2.0.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
api.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:8080").split(","), allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

class LoginBody(BaseModel):
    username: str
    password: str
    role: str

class StudentBody(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    roll_no: str = Field(min_length=1, max_length=64)
    department: str = Field(min_length=1, max_length=64)
    semester: int = Field(ge=1, le=8)
    email: str | None = None
    skills: str | None = None
    username: str | None = None
    password: str | None = None
    faculty_id: int | None = None

class FacultyBody(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)

class SubjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    faculty_id: int

class AttendanceBody(BaseModel):
    student_id: int
    subject_id: int
    class_date: date
    status: str

class AttendanceBatchBody(BaseModel):
    records: list[AttendanceBody] = Field(min_length=1, max_length=200)

class MarksBody(BaseModel):
    student_id: int
    subject: str = Field(min_length=1, max_length=100)
    marks: float = Field(ge=0)
    max_marks: float = Field(gt=0)

class InternshipBody(BaseModel):
    title: str
    company: str
    description: str
    skills: str
    min_semester: int = Field(default=5, ge=1, le=8)
    max_semester: int = Field(default=6, ge=1, le=8)
    application_url: str | None = None


def current_user(request: Request):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        raise HTTPException(401, "Please sign in")
    try:
        data = serializer.loads(token, max_age=60 * 60 * 12)
    except (BadSignature, SignatureExpired):
        raise HTTPException(401, "Session expired. Please sign in again.")
    return data


def require_staff(user=Depends(current_user)):
    if user["role"] not in ("admin", "faculty"):
        raise HTTPException(403, "Faculty or admin access required")
    return user


def student_scope(con, user):
    if user["role"] == "student":
        row = con.execute("SELECT id FROM students WHERE account_id = ?", (user["id"],)).fetchone()
        if not row:
            raise HTTPException(403, "Student profile not found")
        return row["id"]
    return None


def scoped_student_clause(user, alias="s"):
    return (f" AND {alias}.user_id = ?", (user["id"],)) if user["role"] == "faculty" else ("", ())

@api.get("/api/health")
def health():
    return {"status": "ok", "service": "edutrack-backend"}

@api.post("/api/auth/login")
def login(body: LoginBody, response: Response):
    if body.role not in ("admin", "faculty", "student"):
        raise HTTPException(400, "Invalid role")
    with legacy_app.connect() as con:
        user = con.execute("SELECT id, username, password_hash, role FROM users WHERE username = ?", (body.username.strip(),)).fetchone()
        semester = None
        if user and user["role"] == "student":
            student = con.execute("SELECT semester FROM students WHERE account_id = ?", (user["id"],)).fetchone()
            semester = student["semester"] if student else None
    if not user or user["role"] != body.role or not check_password_hash(user["password_hash"], body.password):
        raise HTTPException(401, "Incorrect username, password or role")
    token = serializer.dumps({"id": user["id"], "username": user["username"], "role": user["role"], "semester": semester})
    response.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite="lax", secure=os.getenv("COOKIE_SECURE", "false").lower() == "true", max_age=43200, path="/")
    return {"id": user["id"], "username": user["username"], "role": user["role"], "semester": semester}

@api.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(TOKEN_COOKIE)
    return {"message": "Signed out"}

@api.get("/api/auth/me")
def me(user=Depends(current_user)):
    if user["role"] == "student":
        with legacy_app.connect() as con:
            student = con.execute("SELECT semester FROM students WHERE account_id = ?", (user["id"],)).fetchone()
        return {**user, "semester": student["semester"] if student else None}
    return user

@api.get("/api/dashboard")
def dashboard(user=Depends(current_user)):
    with legacy_app.connect() as con:
        sid = student_scope(con, user)
        if sid:
            attendance = legacy_app.student_attendance_rows(con, student_id=sid)
            marks = con.execute("SELECT subject, marks, max_marks FROM marks WHERE student_id = ? ORDER BY id DESC", (sid,)).fetchall()
            profile = con.execute("SELECT name, roll_no, department, semester, email FROM students WHERE id = ?", (sid,)).fetchone()
            students_count = 1
        else:
            clause, params = scoped_student_clause(user)
            students_count = con.execute("SELECT COUNT(*) AS n FROM students s WHERE 1=1" + clause, params).fetchone()["n"]
            attendance = legacy_app.student_attendance_rows(con, faculty_id=user["id"] if user["role"] == "faculty" else None)
            marks = con.execute("""SELECT m.subject, m.marks, m.max_marks, s.name, s.roll_no FROM marks m JOIN students s ON s.id=m.student_id WHERE 1=1""" + clause + " ORDER BY m.id DESC LIMIT 30", params).fetchall()
            profile = None
        avg_att = round(sum(x["percentage"] for x in attendance) / len(attendance), 1) if attendance else 0
        avg_marks = round(sum(float(x["marks"]) / float(x["max_marks"]) * 100 for x in marks) / len(marks), 1) if marks else 0
        low_count = sum(1 for x in attendance if x["low"])
        eligible = avg_att >= legacy_app.ATTENDANCE_THRESHOLD
    return {"student_count": students_count, "attendance_average": avg_att, "marks_average": avg_marks, "low_attendance_count": low_count, "eligible": eligible, "attendance": attendance[:12], "marks": [dict(x) for x in marks[:12]], "profile": dict(profile) if profile else None}

@api.get("/api/students")
def get_students(user=Depends(current_user)):
    with legacy_app.connect() as con:
        sid = student_scope(con, user)
        if sid:
            rows = con.execute("SELECT id, name, roll_no, department, semester, email, skills FROM students WHERE id=?", (sid,)).fetchall()
        else:
            clause, params = scoped_student_clause(user)
            rows = con.execute("SELECT s.id,s.name,s.roll_no,s.department,s.semester,s.email,s.skills,u.username AS faculty FROM students s LEFT JOIN users u ON u.id=s.user_id WHERE 1=1" + clause + " ORDER BY s.roll_no", params).fetchall()
    return [dict(r) for r in rows]

@api.post("/api/students")
def add_student(body: StudentBody, user=Depends(require_staff)):
    if user["role"] != "admin":
        faculty_id = user["id"]
    else:
        faculty_id = body.faculty_id
        if not faculty_id:
            raise HTTPException(400, "Select the faculty member responsible for this student")
    username = (body.username or "").strip()
    password = body.password or ""
    if not username or len(password) < 8:
        raise HTTPException(400, "Student login username and password (at least 8 characters) are required")
    try:
        with legacy_app.connect() as con:
            faculty = con.execute("SELECT id FROM users WHERE id=? AND role='faculty'", (faculty_id,)).fetchone()
            if not faculty:
                raise HTTPException(400, "Select a valid faculty account")
            account = con.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,'student')", (username, generate_password_hash(password)))
            account_id = account.lastrowid
            cur = con.execute("INSERT INTO students(user_id,account_id,name,roll_no,department,semester,email,skills) VALUES(?,?,?,?,?,?,?,?)", (faculty_id, account_id, body.name.strip(), body.roll_no.strip().upper(), body.department.strip().upper(), body.semester, body.email, body.skills))
    except legacy_app.DB_INTEGRITY_ERRORS:
        raise HTTPException(409, "That student username or roll number is already in use")
    return {"id": cur.lastrowid, "account_created": True, "message": "Student and login account created"}

@api.get("/api/subjects")
def get_subjects(user=Depends(current_user)):
    with legacy_app.connect() as con:
        if user["role"] == "admin": rows=con.execute("SELECT subjects.id,subjects.name,users.username AS faculty FROM subjects LEFT JOIN users ON users.id=subjects.user_id ORDER BY subjects.name").fetchall()
        elif user["role"] == "faculty": rows=con.execute("SELECT id,name FROM subjects WHERE user_id=? ORDER BY name",(user["id"],)).fetchall()
        else:
            sid=student_scope(con,user)
            rows=con.execute("SELECT subjects.id,subjects.name FROM subjects JOIN students ON students.user_id=subjects.user_id WHERE students.id=?",(sid,)).fetchall()
    return [dict(r) for r in rows]

@api.get("/api/attendance")
def get_attendance(user=Depends(current_user)):
    with legacy_app.connect() as con:
        sid=student_scope(con,user)
        if sid: rows=con.execute("SELECT a.id,a.date,a.status,s.name,s.roll_no,sub.name AS subject FROM attendance a JOIN students s ON s.id=a.student_id JOIN subjects sub ON sub.id=a.subject_id WHERE a.student_id=? ORDER BY a.date DESC",(sid,)).fetchall()
        else:
            clause,params=scoped_student_clause(user)
            rows=con.execute("SELECT a.id,a.date,a.status,s.id AS student_id,s.name,s.roll_no,sub.name AS subject FROM attendance a JOIN students s ON s.id=a.student_id JOIN subjects sub ON sub.id=a.subject_id WHERE 1=1"+clause+" ORDER BY a.date DESC LIMIT 300",params).fetchall()
    return [dict(r) for r in rows]

def send_student_caution(student_id: int):
    """Automatically email a caution when attendance/marks cross configured limits."""
    smtp_host=os.getenv("SMTP_HOST"); smtp_user=os.getenv("SMTP_USER"); smtp_pass=os.getenv("SMTP_PASSWORD"); sender=os.getenv("SMTP_FROM") or smtp_user or ""
    with legacy_app.connect() as con:
        student=con.execute("SELECT id,name,email FROM students WHERE id=?",(student_id,)).fetchone()
        if not student: return
        attendance=legacy_app.student_attendance_rows(con,student_id=student_id)
        low_att=any(x["total_classes"] and x["percentage"]<legacy_app.ATTENDANCE_THRESHOLD for x in attendance)
        avg=con.execute("SELECT AVG(marks*100.0/max_marks) AS avg FROM marks WHERE student_id=?",(student_id,)).fetchone()["avg"]
        low_marks=avg is not None and float(avg)<float(os.getenv("LOW_MARKS_THRESHOLD","40"))
        reasons=[]
        if low_att: reasons.append(f"attendance is below {legacy_app.ATTENDANCE_THRESHOLD}%")
        if low_marks: reasons.append(f"internal performance is below {os.getenv('LOW_MARKS_THRESHOLD','40')}%")
        if not reasons: return
        recent=con.execute("SELECT id FROM notification_log WHERE student_id=? AND category='caution' AND sent_at >= DATE_SUB(NOW(), INTERVAL 1 DAY) LIMIT 1",(student_id,)).fetchone()
        if recent: return
        message=f"Dear {student['name']}, your EduTrack record shows that {' and '.join(reasons)}. Please contact your faculty advisor to discuss an improvement plan."
        if student["email"] and smtp_host and smtp_user and smtp_pass and sender:
            msg=EmailMessage(); msg["Subject"]="EduTrack academic caution alert"; msg["From"]=sender; msg["To"]=student["email"]; msg.set_content(message)
            try:
                with smtplib.SMTP(smtp_host,int(os.getenv("SMTP_PORT","587")),timeout=15) as server:
                    server.starttls(); server.login(smtp_user,smtp_pass); server.send_message(msg)
            except (OSError, smtplib.SMTPException):
                pass
        con.execute("INSERT INTO notification_log(student_id,category,message) VALUES(?,?,?)",(student_id,"caution",message))

@api.get("/api/attendance/summary")
def attendance_summary(user=Depends(current_user)):
    with legacy_app.connect() as con:
        sid=student_scope(con,user)
        summary=legacy_app.student_attendance_rows(con, student_id=sid) if sid else legacy_app.student_attendance_rows(con, faculty_id=user["id"] if user["role"]=="faculty" else None)
    return summary

@api.post("/api/attendance")
def save_attendance(body: AttendanceBody,background_tasks: BackgroundTasks,user=Depends(require_staff)):
    if body.status not in ("Present","Absent"): raise HTTPException(400,"Status must be Present or Absent")
    with legacy_app.connect() as con:
        student=con.execute("SELECT id,user_id FROM students WHERE id=?",(body.student_id,)).fetchone()
        if not student or (user["role"]=="faculty" and student["user_id"]!=user["id"]): raise HTTPException(404,"Student not found in your class")
        subject=con.execute("SELECT id,user_id FROM subjects WHERE id=?",(body.subject_id,)).fetchone()
        if not subject or (user["role"]=="faculty" and subject["user_id"]!=user["id"]): raise HTTPException(404,"Subject not found")
        if subject["user_id"] != student["user_id"]: raise HTTPException(400,"This subject is not assigned to the selected student's faculty")
        con.execute("INSERT INTO attendance(student_id,subject_id,date,status) VALUES(?,?,?,?) ON DUPLICATE KEY UPDATE status=VALUES(status)",(body.student_id,body.subject_id,body.class_date,body.status))
    background_tasks.add_task(send_student_caution, body.student_id)
    return {"message":"Attendance saved. Academic caution check queued."}

@api.post("/api/attendance/bulk")
def save_attendance_bulk(body: AttendanceBatchBody,background_tasks: BackgroundTasks,user=Depends(require_staff)):
    student_ids=set()
    with legacy_app.connect() as con:
        for record in body.records:
            if record.status not in ("Present","Absent"): raise HTTPException(400,"Status must be Present or Absent")
            student=con.execute("SELECT id,user_id FROM students WHERE id=?",(record.student_id,)).fetchone()
            if not student or (user["role"]=="faculty" and student["user_id"]!=user["id"]): raise HTTPException(404,"Student not found in your class")
            subject=con.execute("SELECT id,user_id FROM subjects WHERE id=?",(record.subject_id,)).fetchone()
            if not subject or (user["role"]=="faculty" and subject["user_id"]!=user["id"]): raise HTTPException(404,"Subject not found")
            if subject["user_id"] != student["user_id"]: raise HTTPException(400,"This subject is not assigned to the selected student's faculty")
            con.execute("INSERT INTO attendance(student_id,subject_id,date,status) VALUES(?,?,?,?) ON DUPLICATE KEY UPDATE status=VALUES(status)",(record.student_id,record.subject_id,record.class_date,record.status))
            student_ids.add(record.student_id)
    for student_id in student_ids:
        background_tasks.add_task(send_student_caution, student_id)
    return {"message":f"Attendance saved for {len(student_ids)} students. Academic caution checks queued."}

@api.get("/api/marks")
def get_marks(user=Depends(current_user)):
    with legacy_app.connect() as con:
        sid=student_scope(con,user)
        clause,params=scoped_student_clause(user)
        if sid: rows=con.execute("SELECT m.id,m.subject,m.marks,m.max_marks FROM marks m WHERE m.student_id=? ORDER BY m.id DESC",(sid,)).fetchall()
        else: rows=con.execute("SELECT m.id,m.subject,m.marks,m.max_marks,s.id AS student_id,s.name,s.roll_no FROM marks m JOIN students s ON s.id=m.student_id WHERE 1=1"+clause+" ORDER BY m.id DESC",params).fetchall()
    return [dict(r) for r in rows]

@api.post("/api/marks")
def save_marks(body: MarksBody,background_tasks: BackgroundTasks,user=Depends(require_staff)):
    if body.marks>body.max_marks: raise HTTPException(400,"Marks cannot exceed maximum marks")
    with legacy_app.connect() as con:
        student=con.execute("SELECT id,user_id FROM students WHERE id=?",(body.student_id,)).fetchone()
        if not student or (user["role"]=="faculty" and student["user_id"]!=user["id"]): raise HTTPException(404,"Student not found in your class")
        con.execute("INSERT INTO marks(student_id,subject,marks,max_marks) VALUES(?,?,?,?)",(body.student_id,body.subject.strip(),body.marks,body.max_marks))
    background_tasks.add_task(send_student_caution, body.student_id)
    return {"message":"Marks saved. Academic caution check queued."}

@api.get("/api/reports")
def reports(user=Depends(current_user)):
    with legacy_app.connect() as con:
        sid=student_scope(con,user)
        if sid:
            students=con.execute("SELECT id,name,roll_no,semester FROM students WHERE id=?",(sid,)).fetchall()
        else:
            clause,params=scoped_student_clause(user)
            students=con.execute("SELECT s.id,s.name,s.roll_no,s.semester FROM students s WHERE 1=1"+clause+" ORDER BY s.roll_no",params).fetchall()
        result=[]
        for student in students:
            att=legacy_app.student_attendance_rows(con,student_id=student["id"])
            total=sum(x["total_classes"] for x in att); present=sum(x["present_classes"] for x in att)
            marks=con.execute("SELECT AVG(marks*100.0/max_marks) AS avg,COUNT(*) AS n FROM marks WHERE student_id=?",(student["id"],)).fetchone()
            result.append({"student_id":student["id"],"name":student["name"],"roll_no":student["roll_no"],"semester":student["semester"],"attendance":round(100*present/total,1) if total else 0,"classes":total,"marks_average":round(float(marks["avg"]),1) if marks["avg"] is not None else None,"assessments":marks["n"]})
    return result

@api.get("/api/eligibility")
def eligibility(user=Depends(current_user)):
    with legacy_app.connect() as con:
        sid=student_scope(con,user)
        clause,params=scoped_student_clause(user)
        query="SELECT s.id,s.name,s.roll_no,s.semester,COUNT(a.id) AS total,SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END) AS present FROM students s LEFT JOIN attendance a ON a.student_id=s.id WHERE 1=1"+clause+" GROUP BY s.id,s.name,s.roll_no,s.semester"
        rows=con.execute(query,params).fetchall()
        if sid: rows=[r for r in rows if r["id"]==sid]
    result=[]
    for r in rows:
        total=int(r["total"] or 0); present=int(r["present"] or 0); pct=round(100*present/total,1) if total else 0
        result.append({"student_id":r["id"],"name":r["name"],"roll_no":r["roll_no"],"semester":r["semester"],"attendance":pct,"classes":total,"status":"Eligible" if total and pct>=legacy_app.ATTENDANCE_THRESHOLD else "Not eligible"})
    return {"threshold":legacy_app.ATTENDANCE_THRESHOLD,"students":result}

@api.get("/api/internships")
def get_internships(user=Depends(current_user)):
    with legacy_app.connect() as con:
        if user["role"]=="student":
            sid=student_scope(con,user); student=con.execute("SELECT semester,skills FROM students WHERE id=?",(sid,)).fetchone(); sem=student["semester"]
            if int(sem) not in (7, 8):
                return []
            rows=con.execute("SELECT * FROM internships WHERE active=1 AND min_semester<=? AND max_semester>=? ORDER BY id DESC",(sem,sem)).fetchall()
            skills={x.strip().lower() for x in (student["skills"] or "").split(",") if x.strip()}
            academic=con.execute("SELECT AVG(marks*100.0/max_marks) AS avg FROM marks WHERE student_id=?",(sid,)).fetchone()["avg"]
            academic_score=float(academic) if academic is not None else 0.0
            ranked=[]
            for row in rows:
                item=dict(row); required={x.strip().lower() for x in (item.get("skills") or "").split(",") if x.strip()}
                matched=sorted(skills & required); skill_score=100*len(matched)/len(required) if required else 0
                item["matched_skills"]=matched; item["skill_match_score"]=round(skill_score,1)
                item["academic_average"]=round(academic_score,1) if academic is not None else None
                item["match_score"]=round(skill_score*0.7 + academic_score*0.3) if academic is not None else round(skill_score)
                ranked.append(item)
            ranked.sort(key=lambda x:(x["match_score"],x["id"]),reverse=True)
            return ranked
        else: rows=con.execute("SELECT * FROM internships ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]

@api.post("/api/internships")
def add_internship(body: InternshipBody,user=Depends(require_staff)):
    if user["role"]!="admin": raise HTTPException(403,"Only admin can manage internship opportunities")
    if body.min_semester>body.max_semester: raise HTTPException(400,"Minimum semester must not exceed maximum semester")
    with legacy_app.connect() as con:
        cur=con.execute("INSERT INTO internships(title,company,description,skills,min_semester,max_semester,application_url) VALUES(?,?,?,?,?,?,?)",(body.title,body.company,body.description,body.skills,body.min_semester,body.max_semester,body.application_url))
    return {"id":cur.lastrowid,"message":"Internship added"}

@api.get("/api/faculty")
def get_faculty(user=Depends(require_staff)):
    if user["role"] != "admin": raise HTTPException(403, "Admin access required")
    with legacy_app.connect() as con:
        rows=con.execute("SELECT u.id,u.username,COUNT(s.id) AS student_count FROM users u LEFT JOIN students s ON s.user_id=u.id WHERE u.role='faculty' GROUP BY u.id,u.username ORDER BY u.username").fetchall()
    return [dict(r) for r in rows]

@api.post("/api/faculty")
def add_faculty(body: FacultyBody,user=Depends(require_staff)):
    if user["role"] != "admin": raise HTTPException(403, "Admin access required")
    import re
    if not re.fullmatch(r"[A-Za-z0-9_]{3,32}",body.username): raise HTTPException(400,"Username must be 3–32 letters, numbers or underscores")
    with legacy_app.connect() as con:
        try: con.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,'faculty')",(body.username,generate_password_hash(body.password)))
        except legacy_app.DB_INTEGRITY_ERRORS: raise HTTPException(409,"Username already exists")
    return {"message":"Faculty account created"}

@api.post("/api/subjects")
def add_subject(body: SubjectBody,user=Depends(require_staff)):
    if user["role"] != "admin": raise HTTPException(403, "Admin access required")
    with legacy_app.connect() as con:
        faculty=con.execute("SELECT id FROM users WHERE id=? AND role='faculty'",(body.faculty_id,)).fetchone()
        if not faculty: raise HTTPException(404,"Faculty account not found")
        try: con.execute("INSERT INTO subjects(user_id,name) VALUES(?,?)",(body.faculty_id,body.name.strip()))
        except legacy_app.DB_INTEGRITY_ERRORS: raise HTTPException(409,"Subject already exists for this faculty")
    return {"message":"Subject created"}

@api.post("/api/notifications/check")
def check_notifications(user=Depends(require_staff)):
    """Evaluate low attendance / low marks and send email when SMTP is configured."""
    sent=[]; smtp_host=os.getenv("SMTP_HOST"); smtp_user=os.getenv("SMTP_USER"); smtp_pass=os.getenv("SMTP_PASSWORD"); sender=os.getenv("SMTP_FROM") or smtp_user or ""
    with legacy_app.connect() as con:
        clause,params=scoped_student_clause(user)
        students=con.execute("SELECT s.id,s.name,s.email,s.roll_no FROM students s WHERE 1=1"+clause,params).fetchall()
        for st in students:
            attendance=legacy_app.student_attendance_rows(con,student_id=st["id"])
            low_att=[a for a in attendance if a["total_classes"] and a["percentage"]<legacy_app.ATTENDANCE_THRESHOLD]
            avg=con.execute("SELECT AVG(marks*100.0/max_marks) AS avg FROM marks WHERE student_id=?",(st["id"],)).fetchone()["avg"]
            reasons=[]
            if low_att: reasons.append(f"Attendance below {legacy_app.ATTENDANCE_THRESHOLD}%")
            if avg is not None and float(avg)<float(os.getenv("LOW_MARKS_THRESHOLD", "40")): reasons.append(f"Internal performance below {os.getenv('LOW_MARKS_THRESHOLD', '40')}%")
            if not reasons: continue
            message=f"Dear {st['name']}, EduTrack caution: {'; '.join(reasons)}. Please contact your faculty advisor."
            if st["email"] and smtp_host and smtp_user and smtp_pass:
                msg=EmailMessage(); msg["Subject"]="EduTrack academic caution alert"; msg["From"]=sender; msg["To"]=st["email"]; msg.set_content(message)
                with smtplib.SMTP(smtp_host,int(os.getenv("SMTP_PORT","587")),timeout=15) as server:
                    server.starttls(); server.login(smtp_user,smtp_pass); server.send_message(msg)
                con.execute("INSERT INTO notification_log(student_id,category,message) VALUES(?,?,?)",(st["id"],"caution",message)); sent.append({"student":st["name"],"email":st["email"],"status":"sent"})
            else: sent.append({"student":st["name"],"email":st["email"],"status":"not sent: email/SMTP not configured","message":message})
    return {"evaluated":len(students),"alerts":sent,"smtp_configured":bool(smtp_host and smtp_user and smtp_pass)}

@api.get("/api/notifications")
def notifications(user=Depends(current_user)):
    with legacy_app.connect() as con:
        sid=student_scope(con,user)
        clause,params=scoped_student_clause(user)
        if sid: rows=con.execute("SELECT n.*,s.name,s.roll_no FROM notification_log n JOIN students s ON s.id=n.student_id WHERE n.student_id=? ORDER BY n.sent_at DESC",(sid,)).fetchall()
        else: rows=con.execute("SELECT n.*,s.name,s.roll_no FROM notification_log n JOIN students s ON s.id=n.student_id WHERE 1=1"+clause+" ORDER BY n.sent_at DESC LIMIT 100",params).fetchall()
    return [dict(r) for r in rows]

# The original Flask app remains available at legacy URLs during migration, but the
# React SPA uses the JSON API above for all academic workflows.
"""EduTrack REST API: React frontend + FastAPI + MySQL."""
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from email.message import EmailMessage
import os
import smtplib
import sys

from fastapi import FastAPI, Depends, HTTPException, Response, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import check_password_hash, generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import app as legacy_app

TOKEN_COOKIE = "edutrack_session"
serializer = URLSafeTimedSerializer(os.getenv("SECRET_KEY", "local-development-change-this"), salt="edutrack-api")


def ensure_extra_schema():
    with legacy_app.connect() as con:
        cols = legacy_app.table_columns(con, "students")
        if "email" not in cols:
            con.execute("ALTER TABLE students ADD COLUMN email VARCHAR(255) NULL")
        if "skills" not in cols:
            con.execute("ALTER TABLE students ADD COLUMN skills VARCHAR(1000) NULL")
        con.execute("""CREATE TABLE IF NOT EXISTS internships (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            title VARCHAR(180) NOT NULL, company VARCHAR(180) NOT NULL,
            description TEXT NOT NULL, skills VARCHAR(500) NOT NULL,
            min_semester TINYINT UNSIGNED NOT NULL DEFAULT 5,
            max_semester TINYINT UNSIGNED NOT NULL DEFAULT 6,
            application_url VARCHAR(500) NULL, active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        con.execute("""CREATE TABLE IF NOT EXISTS notification_log (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            student_id BIGINT UNSIGNED NOT NULL, category VARCHAR(40) NOT NULL,
            message VARCHAR(500) NOT NULL, sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT notification_student_fk FOREIGN KEY (student_id) REFERENCES students(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")


@asynccontextmanager
async def lifespan(_: FastAPI):
    legacy_app.init_db()
    ensure_extra_schema()
    legacy_app.bootstrap_admin_account()
    yield

api = FastAPI(title="EduTrack API", version="2.0.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
api.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:8080").split(","), allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

class LoginBody(BaseModel):
    username: str
    password: str
    role: str

class StudentBody(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    roll_no: str = Field(min_length=1, max_length=64)
    department: str = Field(min_length=1, max_length=64)
    semester: int = Field(ge=1, le=8)
    email: str | None = None
    skills: str | None = None
    username: str | None = None
    password: str | None = None
    faculty_id: int | None = None

class FacultyBody(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)

class SubjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    faculty_id: int

class AttendanceBody(BaseModel):
    student_id: int
    subject_id: int
    class_date: date
    status: str

class AttendanceBatchBody(BaseModel):
    records: list[AttendanceBody] = Field(min_length=1, max_length=200)

class MarksBody(BaseModel):
    student_id: int
    subject: str = Field(min_length=1, max_length=100)
    marks: float = Field(ge=0)
    max_marks: float = Field(gt=0)

class InternshipBody(BaseModel):
    title: str
    company: str
    description: str
    skills: str
    min_semester: int = Field(default=5, ge=1, le=8)
    max_semester: int = Field(default=6, ge=1, le=8)
    application_url: str | None = None


def current_user(request: Request):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        raise HTTPException(401, "Please sign in")
    try:
        data = serializer.loads(token, max_age=60 * 60 * 12)
    except (BadSignature, SignatureExpired):
        raise HTTPException(401, "Session expired. Please sign in again.")
    return data


def require_staff(user=Depends(current_user)):
    if user["role"] not in ("admin", "faculty"):
        raise HTTPException(403, "Faculty or admin access required")
    return user


def student_scope(con, user):
    if user["role"] == "student":
        row = con.execute("SELECT id FROM students WHERE account_id = ?", (user["id"],)).fetchone()
        if not row:
            raise HTTPException(403, "Student profile not found")
        return row["id"]
    return None


def scoped_student_clause(user, alias="s"):
    return (f" AND {alias}.user_id = ?", (user["id"],)) if user["role"] == "faculty" else ("", ())

@api.get("/api/health")
def health():
    return {"status": "ok", "service": "edutrack-backend"}

@api.post("/api/auth/login")
def login(body: LoginBody, response: Response):
    if body.role not in ("admin", "faculty", "student"):
        raise HTTPException(400, "Invalid role")
    with legacy_app.connect() as con:
        user = con.execute("SELECT id, username, password_hash, role FROM users WHERE username = ?", (body.username.strip(),)).fetchone()
        semester = None
        if user and user["role"] == "student":
            student = con.execute("SELECT semester FROM students WHERE account_id = ?", (user["id"],)).fetchone()
            semester = student["semester"] if student else None
    if not user or user["role"] != body.role or not check_password_hash(user["password_hash"], body.password):
        raise HTTPException(401, "Incorrect username, password or role")
    token = serializer.dumps({"id": user["id"], "username": user["username"], "role": user["role"], "semester": semester})
    response.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite="lax", secure=os.getenv("COOKIE_SECURE", "false").lower() == "true", max_age=43200, path="/")
    return {"id": user["id"], "username": user["username"], "role": user["role"], "semester": semester}

@api.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(TOKEN_COOKIE)
    return {"message": "Signed out"}

@api.get("/api/auth/me")
def me(user=Depends(current_user)):
    if user["role"] == "student":
        with legacy_app.connect() as con:
            student = con.execute("SELECT semester FROM students WHERE account_id = ?", (user["id"],)).fetchone()
        return {**user, "semester": student["semester"] if student else None}
    return user

@api.get("/api/dashboard")
def dashboard(user=Depends(current_user)):
    with legacy_app.connect() as con:
        sid = student_scope(con, user)
        if sid:
            attendance = legacy_app.student_attendance_rows(con, student_id=sid)
            marks = con.execute("SELECT subject, marks, max_marks FROM marks WHERE student_id = ? ORDER BY id DESC", (sid,)).fetchall()
            profile = con.execute("SELECT name, roll_no, department, semester, email FROM students WHERE id = ?", (sid,)).fetchone()
            students_count = 1
        else:
            clause, params = scoped_student_clause(user)
            students_count = con.execute("SELECT COUNT(*) AS n FROM students s WHERE 1=1" + clause, params).fetchone()["n"]
            attendance = legacy_app.student_attendance_rows(con, faculty_id=user["id"] if user["role"] == "faculty" else None)
            marks = con.execute("""SELECT m.subject, m.marks, m.max_marks, s.name, s.roll_no FROM marks m JOIN students s ON s.id=m.student_id WHERE 1=1""" + clause + " ORDER BY m.id DESC LIMIT 30", params).fetchall()
            profile = None
        avg_att = round(sum(x["percentage"] for x in attendance) / len(attendance), 1) if attendance else 0
        avg_marks = round(sum(float(x["marks"]) / float(x["max_marks"]) * 100 for x in marks) / len(marks), 1) if marks else 0
        low_count = sum(1 for x in attendance if x["low"])
        eligible = avg_att >= legacy_app.ATTENDANCE_THRESHOLD
    return {"student_count": students_count, "attendance_average": avg_att, "marks_average": avg_marks, "low_attendance_count": low_count, "eligible": eligible, "attendance": attendance[:12], "marks": [dict(x) for x in marks[:12]], "profile": dict(profile) if profile else None}

@api.get("/api/students")
def get_students(user=Depends(current_user)):
    with legacy_app.connect() as con:
        sid = student_scope(con, user)
        if sid:
            rows = con.execute("SELECT id, name, roll_no, department, semester, email, skills FROM students WHERE id=?", (sid,)).fetchall()
        else:
            clause, params = scoped_student_clause(user)
            rows = con.execute("SELECT s.id,s.name,s.roll_no,s.department,s.semester,s.email,s.skills,u.username AS faculty FROM students s LEFT JOIN users u ON u.id=s.user_id WHERE 1=1" + clause + " ORDER BY s.roll_no", params).fetchall()
    return [dict(r) for r in rows]

@api.post("/api/students")
def add_student(body: StudentBody, user=Depends(require_staff)):
    if user["role"] != "admin":
        faculty_id = user["id"]
    else:
        faculty_id = body.faculty_id
        if not faculty_id:
            raise HTTPException(400, "Select the faculty member responsible for this student")
    username = (body.username or "").strip()
    password = body.password or ""
    if not username or len(password) < 8:
        raise HTTPException(400, "Student login username and password (at least 8 characters) are required")
    try:
        with legacy_app.connect() as con:
            faculty = con.execute("SELECT id FROM users WHERE id=? AND role='faculty'", (faculty_id,)).fetchone()
            if not faculty:
                raise HTTPException(400, "Select a valid faculty account")
            account = con.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,'student')", (username, generate_password_hash(password)))
            account_id = account.lastrowid
            cur = con.execute("INSERT INTO students(user_id,account_id,name,roll_no,department,semester,email,skills) VALUES(?,?,?,?,?,?,?,?)", (faculty_id, account_id, body.name.strip(), body.roll_no.strip().upper(), body.department.strip().upper(), body.semester, body.email, body.skills))
    except legacy_app.DB_INTEGRITY_ERRORS:
        raise HTTPException(409, "That student username or roll number is already in use")
    return {"id": cur.lastrowid, "account_created": True, "message": "Student and login account created"}

@api.get("/api/subjects")
def get_subjects(user=Depends(current_user)):
    with legacy_app.connect() as con:
        if user["role"] == "admin": rows=con.execute("SELECT subjects.id,subjects.name,users.username AS faculty FROM subjects LEFT JOIN users ON users.id=subjects.user_id ORDER BY subjects.name").fetchall()
        elif user["role"] == "faculty": rows=con.execute("SELECT id,name FROM subjects WHERE user_id=? ORDER BY name",(user["id"],)).fetchall()
        else:
            sid=student_scope(con,user)
            rows=con.execute("SELECT subjects.id,subjects.name FROM subjects JOIN students ON students.user_id=subjects.user_id WHERE students.id=?",(sid,)).fetchall()
    return [dict(r) for r in rows]

@api.get("/api/attendance")
def get_attendance(user=Depends(current_user)):
    with legacy_app.connect() as con:
        sid=student_scope(con,user)
        if sid: rows=con.execute("SELECT a.id,a.date,a.status,s.name,s.roll_no,sub.name AS subject FROM attendance a JOIN students s ON s.id=a.student_id JOIN subjects sub ON sub.id=a.subject_id WHERE a.student_id=? ORDER BY a.date DESC",(sid,)).fetchall()
        else:
            clause,params=scoped_student_clause(user)
            rows=con.execute("SELECT a.id,a.date,a.status,s.id AS student_id,s.name,s.roll_no,sub.name AS subject FROM attendance a JOIN students s ON s.id=a.student_id JOIN subjects sub ON sub.id=a.subject_id WHERE 1=1"+clause+" ORDER BY a.date DESC LIMIT 300",params).fetchall()
    return [dict(r) for r in rows]

def send_student_caution(student_id: int):
    """Automatically email a caution when attendance/marks cross configured limits."""
    smtp_host=os.getenv("SMTP_HOST"); smtp_user=os.getenv("SMTP_USER"); smtp_pass=os.getenv("SMTP_PASSWORD"); sender=os.getenv("SMTP_FROM") or smtp_user or ""
    with legacy_app.connect() as con:
        student=con.execute("SELECT id,name,email FROM students WHERE id=?",(student_id,)).fetchone()
        if not student: return
        attendance=legacy_app.student_attendance_rows(con,student_id=student_id)
        low_att=any(x["total_classes"] and x["percentage"]<legacy_app.ATTENDANCE_THRESHOLD for x in attendance)
        avg=con.execute("SELECT AVG(marks*100.0/max_marks) AS avg FROM marks WHERE student_id=?",(student_id,)).fetchone()["avg"]
        low_marks=avg is not None and float(avg)<float(os.getenv("LOW_MARKS_THRESHOLD","40"))
        reasons=[]
        if low_att: reasons.append(f"attendance is below {legacy_app.ATTENDANCE_THRESHOLD}%")
        if low_marks: reasons.append(f"internal performance is below {os.getenv('LOW_MARKS_THRESHOLD','40')}%")
        if not reasons: return
        recent=con.execute("SELECT id FROM notification_log WHERE student_id=? AND category='caution' AND sent_at >= DATE_SUB(NOW(), INTERVAL 1 DAY) LIMIT 1",(student_id,)).fetchone()
        if recent: return
        message=f"Dear {student['name']}, your EduTrack record shows that {' and '.join(reasons)}. Please contact your faculty advisor to discuss an improvement plan."
        if student["email"] and smtp_host and smtp_user and smtp_pass and sender:
            msg=EmailMessage(); msg["Subject"]="EduTrack academic caution alert"; msg["From"]=sender; msg["To"]=student["email"]; msg.set_content(message)
            try:
                with smtplib.SMTP(smtp_host,int(os.getenv("SMTP_PORT","587")),timeout=15) as server:
                    server.starttls(); server.login(smtp_user,smtp_pass); server.send_message(msg)
            except (OSError, smtplib.SMTPException):
                pass
        con.execute("INSERT INTO notification_log(student_id,category,message) VALUES(?,?,?)",(student_id,"caution",message))

@api.get("/api/attendance/summary")
def attendance_summary(user=Depends(current_user)):
    with legacy_app.connect() as con:
        sid=student_scope(con,user)
        summary=legacy_app.student_attendance_rows(con, student_id=sid) if sid else legacy_app.student_attendance_rows(con, faculty_id=user["id"] if user["role"]=="faculty" else None)
    return summary

@api.post("/api/attendance")
def save_attendance(body: AttendanceBody,background_tasks: BackgroundTasks,user=Depends(require_staff)):
    if body.status not in ("Present","Absent"): raise HTTPException(400,"Status must be Present or Absent")
    with legacy_app.connect() as con:
        student=con.execute("SELECT id,user_id FROM students WHERE id=?",(body.student_id,)).fetchone()
        if not student or (user["role"]=="faculty" and student["user_id"]!=user["id"]): raise HTTPException(404,"Student not found in your class")
        subject=con.execute("SELECT id,user_id FROM subjects WHERE id=?",(body.subject_id,)).fetchone()
        if not subject or (user["role"]=="faculty" and subject["user_id"]!=user["id"]): raise HTTPException(404,"Subject not found")
        if subject["user_id"] != student["user_id"]: raise HTTPException(400,"This subject is not assigned to the selected student's faculty")
        con.execute("INSERT INTO attendance(student_id,subject_id,date,status) VALUES(?,?,?,?) ON DUPLICATE KEY UPDATE status=VALUES(status)",(body.student_id,body.subject_id,body.class_date,body.status))
    background_tasks.add_task(send_student_caution, body.student_id)
    return {"message":"Attendance saved. Academic caution check queued."}

@api.post("/api/attendance/bulk")
def save_attendance_bulk(body: AttendanceBatchBody,background_tasks: BackgroundTasks,user=Depends(require_staff)):
    student_ids=set()
    with legacy_app.connect() as con:
        for record in body.records:
            if record.status not in ("Present","Absent"): raise HTTPException(400,"Status must be Present or Absent")
            student=con.execute("SELECT id,user_id FROM students WHERE id=?",(record.student_id,)).fetchone()
            if not student or (user["role"]=="faculty" and student["user_id"]!=user["id"]): raise HTTPException(404,"Student not found in your class")
            subject=con.execute("SELECT id,user_id FROM subjects WHERE id=?",(record.subject_id,)).fetchone()
            if not subject or (user["role"]=="faculty" and subject["user_id"]!=user["id"]): raise HTTPException(404,"Subject not found")
            if subject["user_id"] != student["user_id"]: raise HTTPException(400,"This subject is not assigned to the selected student's faculty")
            con.execute("INSERT INTO attendance(student_id,subject_id,date,status) VALUES(?,?,?,?) ON DUPLICATE KEY UPDATE status=VALUES(status)",(record.student_id,record.subject_id,record.class_date,record.status))
            student_ids.add(record.student_id)
    for student_id in student_ids:
        background_tasks.add_task(send_student_caution, student_id)
    return {"message":f"Attendance saved for {len(student_ids)} students. Academic caution checks queued."}

@api.get("/api/marks")
def get_marks(user=Depends(current_user)):
    with legacy_app.connect() as con:
        sid=student_scope(con,user)
        clause,params=scoped_student_clause(user)
        if sid: rows=con.execute("SELECT m.id,m.subject,m.marks,m.max_marks FROM marks m WHERE m.student_id=? ORDER BY m.id DESC",(sid,)).fetchall()
        else: rows=con.execute("SELECT m.id,m.subject,m.marks,m.max_marks,s.id AS student_id,s.name,s.roll_no FROM marks m JOIN students s ON s.id=m.student_id WHERE 1=1"+clause+" ORDER BY m.id DESC",params).fetchall()
    return [dict(r) for r in rows]

@api.post("/api/marks")
def save_marks(body: MarksBody,background_tasks: BackgroundTasks,user=Depends(require_staff)):
    if body.marks>body.max_marks: raise HTTPException(400,"Marks cannot exceed maximum marks")
    with legacy_app.connect() as con:
        student=con.execute("SELECT id,user_id FROM students WHERE id=?",(body.student_id,)).fetchone()
        if not student or (user["role"]=="faculty" and student["user_id"]!=user["id"]): raise HTTPException(404,"Student not found in your class")
        con.execute("INSERT INTO marks(student_id,subject,marks,max_marks) VALUES(?,?,?,?)",(body.student_id,body.subject.strip(),body.marks,body.max_marks))
    background_tasks.add_task(send_student_caution, body.student_id)
    return {"message":"Marks saved. Academic caution check queued."}

@api.get("/api/reports")
def reports(user=Depends(current_user)):
    with legacy_app.connect() as con:
        sid=student_scope(con,user)
        if sid:
            students=con.execute("SELECT id,name,roll_no,semester FROM students WHERE id=?",(sid,)).fetchall()
        else:
            clause,params=scoped_student_clause(user)
            students=con.execute("SELECT s.id,s.name,s.roll_no,s.semester FROM students s WHERE 1=1"+clause+" ORDER BY s.roll_no",params).fetchall()
        result=[]
        for student in students:
            att=legacy_app.student_attendance_rows(con,student_id=student["id"])
            total=sum(x["total_classes"] for x in att); present=sum(x["present_classes"] for x in att)
            marks=con.execute("SELECT AVG(marks*100.0/max_marks) AS avg,COUNT(*) AS n FROM marks WHERE student_id=?",(student["id"],)).fetchone()
            result.append({"student_id":student["id"],"name":student["name"],"roll_no":student["roll_no"],"semester":student["semester"],"attendance":round(100*present/total,1) if total else 0,"classes":total,"marks_average":round(float(marks["avg"]),1) if marks["avg"] is not None else None,"assessments":marks["n"]})
    return result

@api.get("/api/eligibility")
def eligibility(user=Depends(current_user)):
    with legacy_app.connect() as con:
        sid=student_scope(con,user)
        clause,params=scoped_student_clause(user)
        query="SELECT s.id,s.name,s.roll_no,s.semester,COUNT(a.id) AS total,SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END) AS present FROM students s LEFT JOIN attendance a ON a.student_id=s.id WHERE 1=1"+clause+" GROUP BY s.id,s.name,s.roll_no,s.semester"
        rows=con.execute(query,params).fetchall()
        if sid: rows=[r for r in rows if r["id"]==sid]
    result=[]
    for r in rows:
        total=int(r["total"] or 0); present=int(r["present"] or 0); pct=round(100*present/total,1) if total else 0
        result.append({"student_id":r["id"],"name":r["name"],"roll_no":r["roll_no"],"semester":r["semester"],"attendance":pct,"classes":total,"status":"Eligible" if total and pct>=legacy_app.ATTENDANCE_THRESHOLD else "Not eligible"})
    return {"threshold":legacy_app.ATTENDANCE_THRESHOLD,"students":result}

@api.get("/api/internships")
def get_internships(user=Depends(current_user)):
    with legacy_app.connect() as con:
        if user["role"]=="student":
            sid=student_scope(con,user); student=con.execute("SELECT semester,skills FROM students WHERE id=?",(sid,)).fetchone(); sem=student["semester"]
            if int(sem) not in (7, 8):
                return []
            rows=con.execute("SELECT * FROM internships WHERE active=1 AND min_semester<=? AND max_semester>=? ORDER BY id DESC",(sem,sem)).fetchall()
            skills={x.strip().lower() for x in (student["skills"] or "").split(",") if x.strip()}
            academic=con.execute("SELECT AVG(marks*100.0/max_marks) AS avg FROM marks WHERE student_id=?",(sid,)).fetchone()["avg"]
            academic_score=float(academic) if academic is not None else 0.0
            ranked=[]
            for row in rows:
                item=dict(row); required={x.strip().lower() for x in (item.get("skills") or "").split(",") if x.strip()}
                matched=sorted(skills & required); skill_score=100*len(matched)/len(required) if required else 0
                item["matched_skills"]=matched; item["skill_match_score"]=round(skill_score,1)
                item["academic_average"]=round(academic_score,1) if academic is not None else None
                item["match_score"]=round(skill_score*0.7 + academic_score*0.3) if academic is not None else round(skill_score)
                ranked.append(item)
            ranked.sort(key=lambda x:(x["match_score"],x["id"]),reverse=True)
            return ranked
        else: rows=con.execute("SELECT * FROM internships ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]

@api.post("/api/internships")
def add_internship(body: InternshipBody,user=Depends(require_staff)):
    if user["role"]!="admin": raise HTTPException(403,"Only admin can manage internship opportunities")
    if body.min_semester>body.max_semester: raise HTTPException(400,"Minimum semester must not exceed maximum semester")
    with legacy_app.connect() as con:
        cur=con.execute("INSERT INTO internships(title,company,description,skills,min_semester,max_semester,application_url) VALUES(?,?,?,?,?,?,?)",(body.title,body.company,body.description,body.skills,body.min_semester,body.max_semester,body.application_url))
    return {"id":cur.lastrowid,"message":"Internship added"}

@api.get("/api/faculty")
def get_faculty(user=Depends(require_staff)):
    if user["role"] != "admin": raise HTTPException(403, "Admin access required")
    with legacy_app.connect() as con:
        rows=con.execute("SELECT u.id,u.username,COUNT(s.id) AS student_count FROM users u LEFT JOIN students s ON s.user_id=u.id WHERE u.role='faculty' GROUP BY u.id,u.username ORDER BY u.username").fetchall()
    return [dict(r) for r in rows]

@api.post("/api/faculty")
def add_faculty(body: FacultyBody,user=Depends(require_staff)):
    if user["role"] != "admin": raise HTTPException(403, "Admin access required")
    import re
    if not re.fullmatch(r"[A-Za-z0-9_]{3,32}",body.username): raise HTTPException(400,"Username must be 3–32 letters, numbers or underscores")
    with legacy_app.connect() as con:
        try: con.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,'faculty')",(body.username,generate_password_hash(body.password)))
        except legacy_app.DB_INTEGRITY_ERRORS: raise HTTPException(409,"Username already exists")
    return {"message":"Faculty account created"}

@api.post("/api/subjects")
def add_subject(body: SubjectBody,user=Depends(require_staff)):
    if user["role"] != "admin": raise HTTPException(403, "Admin access required")
    with legacy_app.connect() as con:
        faculty=con.execute("SELECT id FROM users WHERE id=? AND role='faculty'",(body.faculty_id,)).fetchone()
        if not faculty: raise HTTPException(404,"Faculty account not found")
        try: con.execute("INSERT INTO subjects(user_id,name) VALUES(?,?)",(body.faculty_id,body.name.strip()))
        except legacy_app.DB_INTEGRITY_ERRORS: raise HTTPException(409,"Subject already exists for this faculty")
    return {"message":"Subject created"}

@api.post("/api/notifications/check")
def check_notifications(user=Depends(require_staff)):
    """Evaluate low attendance / low marks and send email when SMTP is configured."""
    sent=[]; smtp_host=os.getenv("SMTP_HOST"); smtp_user=os.getenv("SMTP_USER"); smtp_pass=os.getenv("SMTP_PASSWORD"); sender=os.getenv("SMTP_FROM") or smtp_user or ""
    with legacy_app.connect() as con:
        clause,params=scoped_student_clause(user)
        students=con.execute("SELECT s.id,s.name,s.email,s.roll_no FROM students s WHERE 1=1"+clause,params).fetchall()
        for st in students:
            attendance=legacy_app.student_attendance_rows(con,student_id=st["id"])
            low_att=[a for a in attendance if a["total_classes"] and a["percentage"]<legacy_app.ATTENDANCE_THRESHOLD]
            avg=con.execute("SELECT AVG(marks*100.0/max_marks) AS avg FROM marks WHERE student_id=?",(st["id"],)).fetchone()["avg"]
            reasons=[]
            if low_att: reasons.append(f"Attendance below {legacy_app.ATTENDANCE_THRESHOLD}%")
            if avg is not None and float(avg)<float(os.getenv("LOW_MARKS_THRESHOLD", "40")): reasons.append(f"Internal performance below {os.getenv('LOW_MARKS_THRESHOLD', '40')}%")
            if not reasons: continue
            message=f"Dear {st['name']}, EduTrack caution: {'; '.join(reasons)}. Please contact your faculty advisor."
            if st["email"] and smtp_host and smtp_user and smtp_pass:
                msg=EmailMessage(); msg["Subject"]="EduTrack academic caution alert"; msg["From"]=sender; msg["To"]=st["email"]; msg.set_content(message)
                with smtplib.SMTP(smtp_host,int(os.getenv("SMTP_PORT","587")),timeout=15) as server:
                    server.starttls(); server.login(smtp_user,smtp_pass); server.send_message(msg)
                con.execute("INSERT INTO notification_log(student_id,category,message) VALUES(?,?,?)",(st["id"],"caution",message)); sent.append({"student":st["name"],"email":st["email"],"status":"sent"})
            else: sent.append({"student":st["name"],"email":st["email"],"status":"not sent: email/SMTP not configured","message":message})
    return {"evaluated":len(students),"alerts":sent,"smtp_configured":bool(smtp_host and smtp_user and smtp_pass)}

@api.get("/api/notifications")
def notifications(user=Depends(current_user)):
    with legacy_app.connect() as con:
        sid=student_scope(con,user)
        clause,params=scoped_student_clause(user)
        if sid: rows=con.execute("SELECT n.*,s.name,s.roll_no FROM notification_log n JOIN students s ON s.id=n.student_id WHERE n.student_id=? ORDER BY n.sent_at DESC",(sid,)).fetchall()
        else: rows=con.execute("SELECT n.*,s.name,s.roll_no FROM notification_log n JOIN students s ON s.id=n.student_id WHERE 1=1"+clause+" ORDER BY n.sent_at DESC LIMIT 100",params).fetchall()
    return [dict(r) for r in rows]

# The original Flask app remains available at legacy URLs during migration, but the
# React SPA uses the JSON API above for all academic workflows.
