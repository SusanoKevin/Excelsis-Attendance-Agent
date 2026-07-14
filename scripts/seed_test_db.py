#!/usr/bin/env python3
"""
Seed a local SQL Server test database with realistic education and financial data.

Creates two databases:
  education_db  — 16 tables, up to ~2.7M rows
  finance_db    — 18 tables, up to ~2.5M rows

Usage:
    python scripts/seed_test_db.py [--scale small|medium|large] [--auth-method windows|sql]

Prerequisites:
    pip install faker
"""
from __future__ import annotations

import argparse
import random
from datetime import date, timedelta

import numpy as np
import pandas as pd
from faker import Faker
from sqlalchemy import text

from _db_utils import build_engine

# ── Connection defaults (overridable via CLI args) ─────────────────────────────

SERVER      = "localhost,1433"
USERNAME    = "sa"
PASSWORD    = "ExcelsisTest_2024!"
DRIVER      = "{ODBC Driver 18 for SQL Server}"
AUTH_METHOD = "sql"

# ── Scale tiers ────────────────────────────────────────────────────────────────

SCALES: dict[str, dict] = {
    "small": {
        "n_students": 1_000,
        "n_attendance": 100_000,
        "n_enrollments": 6_000,
        "n_grades": 25_000,
        "n_transactions": 50_000,
        "n_je_lines": 30_000,
        "n_inv_lines": 20_000,
        "n_po_lines": 6_000,
        "n_expense_items": 4_000,
        "n_payments": 8_000,
    },
    "medium": {
        "n_students": 5_000,
        "n_attendance": 500_000,
        "n_enrollments": 30_000,
        "n_grades": 125_000,
        "n_transactions": 250_000,
        "n_je_lines": 150_000,
        "n_inv_lines": 100_000,
        "n_po_lines": 30_000,
        "n_expense_items": 20_000,
        "n_payments": 40_000,
    },
    "large": {
        "n_students": 20_000,
        "n_attendance": 2_000_000,
        "n_enrollments": 120_000,
        "n_grades": 500_000,
        "n_transactions": 1_000_000,
        "n_je_lines": 600_000,
        "n_inv_lines": 300_000,
        "n_po_lines": 120_000,
        "n_expense_items": 75_000,
        "n_payments": 150_000,
    },
}

BATCH_SIZE = 50_000

# ── RNG setup ─────────────────────────────────────────────────────────────────

fake = Faker()
Faker.seed(42)
random.seed(42)
rng = np.random.default_rng(42)

# ── Domain constants ──────────────────────────────────────────────────────────

GRADES = ["9", "10", "11", "12"]
CLASS_LETTERS = list("ABCDE")
ATTEND_STATUSES = ["present", "absent", "late", "excused"]
ATTEND_WEIGHTS = [0.88, 0.05, 0.04, 0.03]

DEPT_NAMES = [
    "Mathematics", "Science", "English Language Arts", "History & Social Studies",
    "Visual Arts", "Physical Education", "Computer Science", "Music", "World Languages",
    "Psychology", "Economics & Business", "Vocational Education", "Special Education",
    "Drama & Theater", "Environmental Science", "Engineering & Technology",
    "Health Sciences", "Library & Media", "Administration", "Athletics",
    "Philosophy & Ethics", "Media & Journalism", "Architecture & Design",
    "Culinary Arts", "Counseling & Wellness",
]

ROOM_TYPES = ["Classroom", "Science Lab", "Computer Lab", "Gymnasium", "Auditorium",
              "Library", "Music Room", "Art Studio", "Conference Room", "Workshop"]
BUILDINGS   = ["Main Building", "Science Wing", "Arts Center", "Gymnasium Complex",
               "Technology Hub", "Administration Block", "Library Annex"]

COURSE_LEVELS = ["Introductory", "Standard", "Advanced", "AP", "Honors", "Remedial"]
ASSIGNMENT_TYPES = ["Homework", "Quiz", "Midterm Exam", "Final Exam", "Project",
                    "Lab Report", "Essay", "Presentation", "Group Project", "Portfolio"]
ENROLL_STATUSES = ["active", "withdrawn", "completed", "incomplete"]
GRADE_LETTERS   = ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D", "F"]
GRADE_POINTS    = [4.0, 4.0, 3.7, 3.3, 3.0, 2.7, 2.3, 2.0, 1.7, 1.0, 0.0]

ACTIVITY_NAMES = [
    "Basketball", "Soccer", "Chess Club", "Drama Club", "Science Olympiad",
    "Math Team", "Debate Club", "Art Club", "Concert Band", "Concert Choir",
    "Robotics", "Environmental Club", "Student Government", "Yearbook", "School Newspaper",
    "Swim Team", "Track & Field", "Volleyball", "Tennis", "Coding Club",
    "Photography Club", "Film Society", "Book Club", "Community Garden",
    "FIRST Robotics", "Model UN", "Future Business Leaders", "National Honor Society",
    "Key Club", "Spanish Club", "French Club", "Japanese Club", "Cultural Exchange",
    "Peer Tutoring", "Anime Club", "E-Sports Team", "Astronomy Club",
    "3D Printing Club", "Culinary Club", "Entrepreneurship Club", "Red Cross Club",
    "Mock Trial", "Investment Club", "Engineering Design Team", "Volunteer Corps",
    "Podcast Club", "Creative Writing Club", "Poetry Slam", "Ethics Bowl",
    "Pre-Med Society", "Wildlife Conservation Club", "Zero Waste Club",
    "Young Entrepreneurs", "Junior Achievement", "Future Teachers Club",
    "Cybersecurity Club", "Web Design Club", "Esports League", "Wellness Council",
    "Peer Mediation", "Literacy Mentors", "STEM for Girls", "Minecraft Education",
    "Music Production Club", "Fashion & Design Club", "Architecture Club",
    "Latin Club", "Classical Studies Club", "Marine Biology Club", "Geology Club",
    "Chemistry Olympiad Team", "Physics Bowl Team", "Geography Bee", "Spelling Bee",
    "Trivia Team", "Crossword Club", "Improv Comedy Club", "Gaming Club",
    "Hiking Club", "Cycling Club", "Martial Arts Club",
]
ACTIVITY_TYPES  = ["Sports", "Academic", "Arts", "Community Service", "STEM", "Social", "Wellness"]
DISC_TYPES      = ["Tardiness", "Unexcused Absence", "Misconduct", "Dress Code Violation",
                   "Phone Violation", "Bullying", "Fighting", "Academic Dishonesty",
                   "Property Damage", "Insubordination", "Other"]
DISC_SEVERITY   = ["Minor", "Moderate", "Major"]
RELATIONSHIPS   = ["Mother", "Father", "Guardian", "Grandparent", "Sibling", "Other"]

ACCOUNT_TYPES_DATA = [
    ("Asset",     "Debit",  "Balance Sheet"),
    ("Liability", "Credit", "Balance Sheet"),
    ("Equity",    "Credit", "Balance Sheet"),
    ("Revenue",   "Credit", "Income Statement"),
    ("Expense",   "Debit",  "Income Statement"),
    ("COGS",      "Debit",  "Income Statement"),
]
ACCOUNT_RANGES = {
    "Asset":     (1000, 1999),
    "Liability": (2000, 2999),
    "Equity":    (3000, 3999),
    "Revenue":   (4000, 4999),
    "Expense":   (5000, 7999),
    "COGS":      (8000, 8999),
}
VENDOR_CATEGORIES  = ["Technology", "Office Supplies", "Facilities", "Professional Services",
                       "Utilities", "Food & Beverage", "Marketing", "HR & Staffing",
                       "Legal", "Insurance", "Transportation", "Training", "Software", "Hardware"]
PAYMENT_TERMS      = ["Net 15", "Net 30", "Net 60", "Net 90", "COD", "Immediate"]
CUSTOMER_INDUSTRIES = ["Education", "Healthcare", "Retail", "Manufacturing", "Finance",
                        "Technology", "Non-Profit", "Government", "Hospitality", "Construction"]
TXN_TYPES     = ["Debit", "Credit"]
TXN_STATUSES  = ["Cleared", "Pending", "Reconciled", "Voided"]
TXN_STATUS_W  = [0.70, 0.15, 0.12, 0.03]
PAYMENT_METHODS = ["ACH", "Wire", "Check", "Credit Card", "Debit Card", "Cash", "PayPal"]
INV_STATUSES    = ["paid", "unpaid", "overdue", "void", "draft"]
INV_STATUS_W    = [0.55, 0.20, 0.15, 0.05, 0.05]
PO_STATUSES     = ["approved", "pending", "received", "partial", "cancelled"]
PO_STATUS_W     = [0.40, 0.20, 0.25, 0.10, 0.05]
EXP_CATEGORIES  = ["Travel", "Meals & Entertainment", "Office Supplies", "Software",
                    "Training", "Equipment", "Utilities", "Professional Development", "Other"]
EXP_STATUSES    = ["approved", "pending", "rejected", "paid"]
EXP_STATUS_W    = [0.50, 0.25, 0.10, 0.15]

# ── Engine / insert helpers ────────────────────────────────────────────────────

def _engine(database: str):
    return build_engine(
        database, SERVER, driver=DRIVER, auth_method=AUTH_METHOD,
        username=USERNAME, password=PASSWORD, fast_executemany=True,
    )


def _insert(engine, table: str, df: pd.DataFrame) -> None:
    if df.empty:
        return
    print(f"    -> {table}: {len(df):,} rows")
    df.to_sql(table, engine, if_exists="append", index=False, chunksize=1_000)


def _insert_batched(engine, table: str, build_fn, total: int) -> None:
    inserted = 0
    while inserted < total:
        count = min(BATCH_SIZE, total - inserted)
        df = build_fn(inserted, count)
        print(f"    -> {table}: batch {inserted:,}-{inserted+count:,} / {total:,}")
        df.to_sql(table, engine, if_exists="append", index=False, chunksize=1_000)
        inserted += count


def _exec(engine, sql: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(sql))


def _rand_date(start: date, end: date) -> date:
    return start + timedelta(days=random.randint(0, (end - start).days))


def _school_days(start: date, end: date) -> list[date]:
    d, out = start, []
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# ── Database bootstrap ─────────────────────────────────────────────────────────

def create_databases() -> None:
    print("\n[Step 1] Creating databases in master...")
    eng = _engine("master")
    with eng.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT")
        for db in ("education_db", "finance_db"):
            conn.execute(text(f"IF DB_ID(N'{db}') IS NULL CREATE DATABASE [{db}]"))
            print(f"  '{db}' ready")
    eng.dispose()


# ══════════════════════════════════════════════════════════════════════════════
# EDUCATION DB
# ══════════════════════════════════════════════════════════════════════════════

_EDU_DROPS = [
    "disciplinary_records", "student_activities", "extracurricular_activities",
    "student_grades", "assignments", "attendance", "enrollments",
    "schedules", "class_sections", "courses", "parent_contacts",
    "students", "teachers", "rooms", "departments", "academic_terms",
]

_EDU_CREATES = [
    """
    CREATE TABLE academic_terms (
        term_id       INT IDENTITY(1,1) PRIMARY KEY,
        name          NVARCHAR(100) NOT NULL,
        academic_year NVARCHAR(10)  NOT NULL,
        term_number   TINYINT       NOT NULL,
        start_date    DATE          NOT NULL,
        end_date      DATE          NOT NULL,
        is_current    BIT           NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE departments (
        department_id   INT IDENTITY(1,1) PRIMARY KEY,
        name            NVARCHAR(150) NOT NULL,
        head_teacher_id INT NULL,
        budget          DECIMAL(14,2) NULL
    )
    """,
    """
    CREATE TABLE rooms (
        room_id   INT IDENTITY(1,1) PRIMARY KEY,
        number    NVARCHAR(20)  NOT NULL,
        building  NVARCHAR(100) NOT NULL,
        capacity  INT           NOT NULL,
        room_type NVARCHAR(50)  NOT NULL
    )
    """,
    """
    CREATE TABLE teachers (
        teacher_id    INT IDENTITY(1,1) PRIMARY KEY,
        name          NVARCHAR(200) NOT NULL,
        email         NVARCHAR(200) NOT NULL,
        department_id INT NULL REFERENCES departments(department_id),
        hire_date     DATE          NOT NULL,
        salary        DECIMAL(10,2) NOT NULL,
        is_active     BIT           NOT NULL DEFAULT 1,
        phone         NVARCHAR(30)  NULL
    )
    """,
    """
    CREATE TABLE students (
        student_id      NVARCHAR(20)  NOT NULL PRIMARY KEY,
        student_name    NVARCHAR(200) NOT NULL,
        grade           NVARCHAR(5)   NOT NULL,
        class_section   NVARCHAR(20)  NOT NULL,
        date_of_birth   DATE          NOT NULL,
        enrollment_date DATE          NOT NULL,
        email           NVARCHAR(200) NULL,
        gpa             DECIMAL(4,3)  NULL,
        is_active       BIT           NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE parent_contacts (
        contact_id   INT IDENTITY(1,1) PRIMARY KEY,
        student_id   NVARCHAR(20)  NOT NULL REFERENCES students(student_id),
        name         NVARCHAR(200) NOT NULL,
        relationship NVARCHAR(50)  NOT NULL,
        phone        NVARCHAR(30)  NOT NULL,
        email        NVARCHAR(200) NULL,
        is_primary   BIT           NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE courses (
        course_id     INT IDENTITY(1,1) PRIMARY KEY,
        code          NVARCHAR(20)  NOT NULL,
        name          NVARCHAR(200) NOT NULL,
        department_id INT NULL REFERENCES departments(department_id),
        credits       TINYINT       NOT NULL,
        level         NVARCHAR(50)  NOT NULL,
        course_type   NVARCHAR(50)  NOT NULL
    )
    """,
    """
    CREATE TABLE class_sections (
        section_id   INT IDENTITY(1,1) PRIMARY KEY,
        course_id    INT NOT NULL REFERENCES courses(course_id),
        teacher_id   INT NOT NULL REFERENCES teachers(teacher_id),
        term_id      INT NOT NULL REFERENCES academic_terms(term_id),
        room_id      INT NOT NULL REFERENCES rooms(room_id),
        section_code NVARCHAR(30)  NOT NULL,
        capacity     INT           NOT NULL,
        enrolled     INT           NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE schedules (
        schedule_id   INT IDENTITY(1,1) PRIMARY KEY,
        section_id    INT          NOT NULL REFERENCES class_sections(section_id),
        day_of_week   NVARCHAR(15) NOT NULL,
        start_time    TIME         NOT NULL,
        end_time      TIME         NOT NULL,
        period_number TINYINT      NOT NULL
    )
    """,
    """
    CREATE TABLE enrollments (
        enrollment_id INT IDENTITY(1,1) PRIMARY KEY,
        student_id    NVARCHAR(20) NOT NULL REFERENCES students(student_id),
        section_id    INT          NOT NULL REFERENCES class_sections(section_id),
        enrolled_date DATE         NOT NULL,
        status        NVARCHAR(20) NOT NULL DEFAULT 'active',
        grade_letter  NVARCHAR(5)  NULL,
        grade_points  DECIMAL(4,3) NULL
    )
    """,
    """
    CREATE TABLE attendance (
        attendance_id INT IDENTITY(1,1) PRIMARY KEY,
        student_id    NVARCHAR(20)  NOT NULL,
        student_name  NVARCHAR(200) NOT NULL,
        date          DATE          NOT NULL,
        status        NVARCHAR(20)  NOT NULL,
        class_section NVARCHAR(20)  NOT NULL,
        grade         NVARCHAR(5)   NOT NULL,
        section_id    INT           NULL,
        notes         NVARCHAR(500) NULL
    )
    """,
    """
    CREATE TABLE assignments (
        assignment_id   INT IDENTITY(1,1) PRIMARY KEY,
        section_id      INT           NOT NULL REFERENCES class_sections(section_id),
        title           NVARCHAR(300) NOT NULL,
        assignment_type NVARCHAR(50)  NOT NULL,
        due_date        DATE          NOT NULL,
        max_points      DECIMAL(6,2)  NOT NULL,
        weight          DECIMAL(5,4)  NOT NULL DEFAULT 0.1
    )
    """,
    """
    CREATE TABLE student_grades (
        grade_id      INT IDENTITY(1,1) PRIMARY KEY,
        student_id    NVARCHAR(20) NOT NULL REFERENCES students(student_id),
        assignment_id INT          NOT NULL REFERENCES assignments(assignment_id),
        score         DECIMAL(6,2) NOT NULL,
        submitted_at  DATETIME2    NOT NULL,
        is_late       BIT          NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE extracurricular_activities (
        activity_id        INT IDENTITY(1,1) PRIMARY KEY,
        name               NVARCHAR(200) NOT NULL,
        activity_type      NVARCHAR(50)  NOT NULL,
        sponsor_teacher_id INT NULL REFERENCES teachers(teacher_id),
        meeting_schedule   NVARCHAR(200) NULL,
        max_members        INT NULL
    )
    """,
    """
    CREATE TABLE student_activities (
        id          INT IDENTITY(1,1) PRIMARY KEY,
        student_id  NVARCHAR(20) NOT NULL REFERENCES students(student_id),
        activity_id INT          NOT NULL REFERENCES extracurricular_activities(activity_id),
        role        NVARCHAR(50) NOT NULL DEFAULT 'Member',
        joined_date DATE         NOT NULL
    )
    """,
    """
    CREATE TABLE disciplinary_records (
        record_id     INT IDENTITY(1,1) PRIMARY KEY,
        student_id    NVARCHAR(20)   NOT NULL REFERENCES students(student_id),
        date          DATE           NOT NULL,
        incident_type NVARCHAR(100)  NOT NULL,
        severity      NVARCHAR(20)   NOT NULL,
        description   NVARCHAR(1000) NULL,
        action_taken  NVARCHAR(500)  NULL,
        resolved      BIT            NOT NULL DEFAULT 0,
        resolved_date DATE           NULL
    )
    """,
]


def _create_edu_tables(engine) -> None:
    print("  Dropping old tables (if any)...")
    for tbl in _EDU_DROPS:
        _exec(engine, f"IF OBJECT_ID('{tbl}','U') IS NOT NULL DROP TABLE [{tbl}]")
    print("  Creating tables...")
    for ddl in _EDU_CREATES:
        _exec(engine, ddl)


def seed_education(cfg: dict) -> None:
    print("\n[Step 2] Seeding education_db...")
    engine = _engine("education_db")
    _create_edu_tables(engine)

    # academic_terms — 12 rows (4 quarters × 3 years)
    terms = []
    for yr in range(2022, 2025):
        for q in range(1, 5):
            start = date(yr, 1 + (q - 1) * 3, 1)
            end   = (date(yr, 3 + (q - 1) * 3, 1) - timedelta(days=1)) if q < 4 else date(yr, 12, 31)
            terms.append({
                "name":          f"Q{q} {yr}–{yr+1}",
                "academic_year": f"{yr}–{yr+1}",
                "term_number":   q,
                "start_date":    start,
                "end_date":      end,
                "is_current":    1 if (yr == 2024 and q == 2) else 0,
            })
    _insert(engine, "academic_terms", pd.DataFrame(terms))
    with engine.connect() as conn:
        term_ids = pd.read_sql("SELECT term_id, start_date, end_date FROM academic_terms", conn)
    # departments — 25 rows
    df_depts = pd.DataFrame({
        "name":            DEPT_NAMES,
        "head_teacher_id": [None] * len(DEPT_NAMES),
        "budget":          rng.integers(50_000, 500_000, size=len(DEPT_NAMES)).tolist(),
    })
    _insert(engine, "departments", df_depts)
    with engine.connect() as conn:
        dept_ids = pd.read_sql("SELECT department_id FROM departments", conn)["department_id"].tolist()

    # rooms — 150 rows
    rooms_rows = []
    for i in range(150):
        rooms_rows.append({
            "number":    f"{BUILDINGS[i % len(BUILDINGS)][0]}{100 + i}",
            "building":  BUILDINGS[i % len(BUILDINGS)],
            "capacity":  random.choice([20, 24, 28, 30, 32, 36, 40, 200, 400]),
            "room_type": ROOM_TYPES[i % len(ROOM_TYPES)],
        })
    _insert(engine, "rooms", pd.DataFrame(rooms_rows))
    with engine.connect() as conn:
        room_ids = pd.read_sql("SELECT room_id FROM rooms", conn)["room_id"].tolist()

    # teachers — 800 rows
    teacher_rows = []
    for _ in range(800):
        teacher_rows.append({
            "name":          fake.name(),
            "email":         fake.unique.email(),
            "department_id": random.choice(dept_ids),
            "hire_date":     _rand_date(date(2000, 1, 1), date(2023, 12, 31)),
            "salary":        round(random.uniform(40_000, 120_000), 2),
            "is_active":     1 if random.random() > 0.05 else 0,
            "phone":         fake.phone_number()[:30],
        })
    _insert(engine, "teachers", pd.DataFrame(teacher_rows))
    with engine.connect() as conn:
        teacher_ids = pd.read_sql("SELECT teacher_id FROM teachers", conn)["teacher_id"].tolist()

    with engine.begin() as conn:
        for did in dept_ids:
            conn.execute(
                text("UPDATE departments SET head_teacher_id=:tid WHERE department_id=:did"),
                {"tid": random.choice(teacher_ids), "did": did},
            )

    # students — n_students rows
    n_stu = cfg["n_students"]
    fake.unique.clear()
    student_rows = []
    for i in range(n_stu):
        grade   = random.choice(GRADES)
        section = f"{grade}-{random.choice(CLASS_LETTERS)}"
        student_rows.append({
            "student_id":      f"STU{i+1:07d}",
            "student_name":    fake.name(),
            "grade":           grade,
            "class_section":   section,
            "date_of_birth":   _rand_date(date(2005, 1, 1), date(2010, 12, 31)),
            "enrollment_date": _rand_date(date(2019, 8, 1), date(2023, 9, 1)),
            "email":           fake.email(),
            "gpa":             round(random.uniform(1.5, 4.0), 3),
            "is_active":       1 if random.random() > 0.03 else 0,
        })
    df_students = pd.DataFrame(student_rows)
    _insert(engine, "students", df_students)

    stu_ids      = df_students["student_id"].tolist()
    stu_names    = dict(zip(df_students["student_id"], df_students["student_name"]))
    stu_grades   = dict(zip(df_students["student_id"], df_students["grade"]))
    stu_sections = dict(zip(df_students["student_id"], df_students["class_section"]))

    # parent_contacts — ~2 per student
    contact_rows = []
    for sid in stu_ids:
        n = random.randint(1, 3)
        for j in range(n):
            contact_rows.append({
                "student_id":   sid,
                "name":         fake.name(),
                "relationship": random.choice(RELATIONSHIPS),
                "phone":        fake.phone_number()[:30],
                "email":        fake.email() if random.random() > 0.3 else None,
                "is_primary":   1 if j == 0 else 0,
            })
    _insert(engine, "parent_contacts", pd.DataFrame(contact_rows))

    # courses — 300 rows
    subjects = [
        "Algebra", "Geometry", "Calculus", "Statistics", "Biology", "Chemistry",
        "Physics", "Earth Science", "Literature", "Composition", "World History",
        "US History", "Government", "Economics", "Spanish", "French", "Japanese",
        "Mandarin", "Art History", "Studio Art", "Music Theory", "Band", "Choir",
        "Physical Education", "Health", "Computer Science", "AP Computer Science",
        "Data Science", "Robotics", "Engineering Design", "Environmental Science",
        "Anatomy", "Psychology", "Sociology", "Philosophy", "Media Literacy",
        "Creative Writing", "Drama", "Film Studies", "Culinary Arts", "Architecture",
    ]
    course_rows = []
    for i in range(300):
        subj  = subjects[i % len(subjects)]
        level = random.choice(COURSE_LEVELS)
        course_rows.append({
            "code":          f"{subj[:3].upper()}{100 + i}",
            "name":          f"{level} {subj}" if level != "Standard" else subj,
            "department_id": random.choice(dept_ids),
            "credits":       random.choice([1, 2, 3, 4, 5]),
            "level":         level,
            "course_type":   random.choice(["Core", "Elective", "Lab", "Seminar"]),
        })
    _insert(engine, "courses", pd.DataFrame(course_rows))
    with engine.connect() as conn:
        course_ids = pd.read_sql("SELECT course_id FROM courses", conn)["course_id"].tolist()

    # class_sections — 900 rows
    section_rows = []
    for i in range(900):
        tid = int(term_ids.iloc[i % len(term_ids)]["term_id"])
        section_rows.append({
            "course_id":    random.choice(course_ids),
            "teacher_id":   random.choice(teacher_ids),
            "term_id":      tid,
            "room_id":      random.choice(room_ids),
            "section_code": f"SEC-{i+1:04d}",
            "capacity":     random.choice([20, 25, 28, 30]),
            "enrolled":     0,
        })
    _insert(engine, "class_sections", pd.DataFrame(section_rows))
    with engine.connect() as conn:
        section_ids = pd.read_sql("SELECT section_id FROM class_sections", conn)["section_id"].tolist()

    # schedules — ~5 per section
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    periods = [("08:00:00", "08:50:00", 1), ("09:00:00", "09:50:00", 2),
               ("10:00:00", "10:50:00", 3), ("11:00:00", "11:50:00", 4),
               ("12:30:00", "13:20:00", 5), ("13:30:00", "14:20:00", 6),
               ("14:30:00", "15:20:00", 7)]
    sched_rows = []
    for sid in section_ids:
        for day in random.sample(days, random.randint(2, 5)):
            st, et, pn = random.choice(periods)
            sched_rows.append({
                "section_id":    sid,
                "day_of_week":   day,
                "start_time":    st,
                "end_time":      et,
                "period_number": pn,
            })
    _insert(engine, "schedules", pd.DataFrame(sched_rows))

    # enrollments
    n_enroll = cfg["n_enrollments"]
    enroll_rows = []
    pairs_seen: set = set()
    attempts = 0
    while len(enroll_rows) < n_enroll and attempts < n_enroll * 5:
        sid = random.choice(stu_ids)
        sec = random.choice(section_ids)
        attempts += 1
        if (sid, sec) in pairs_seen:
            continue
        pairs_seen.add((sid, sec))
        gl = random.choice(GRADE_LETTERS)
        enroll_rows.append({
            "student_id":    sid,
            "section_id":    sec,
            "enrolled_date": _rand_date(date(2022, 8, 1), date(2024, 9, 1)),
            "status":        random.choices(ENROLL_STATUSES, weights=[0.75, 0.05, 0.18, 0.02])[0],
            "grade_letter":  gl,
            "grade_points":  GRADE_POINTS[GRADE_LETTERS.index(gl)],
        })
    _insert(engine, "enrollments", pd.DataFrame(enroll_rows))

    # assignments — ~15 per section
    assign_rows = []
    for sec in section_ids:
        for _ in range(random.randint(10, 20)):
            due = _rand_date(date(2022, 9, 1), date(2025, 5, 31))
            assign_rows.append({
                "section_id":       sec,
                "title":            f"{random.choice(ASSIGNMENT_TYPES)} – {fake.bs()[:50]}",
                "assignment_type":  random.choice(ASSIGNMENT_TYPES),
                "due_date":         due,
                "max_points":       random.choice([10.0, 20.0, 25.0, 50.0, 100.0]),
                "weight":           round(random.uniform(0.05, 0.30), 4),
            })
    _insert(engine, "assignments", pd.DataFrame(assign_rows))
    with engine.connect() as conn:
        assign_ids = pd.read_sql("SELECT assignment_id FROM assignments", conn)["assignment_id"].tolist()

    # student_grades — batched
    n_grades = cfg["n_grades"]
    stu_arr    = np.array(stu_ids)
    assign_arr = np.array(assign_ids)

    def _grade_batch(offset: int, count: int) -> pd.DataFrame:
        days_back = rng.integers(0, 365 * 3, count)
        base = date(2025, 5, 1)
        submitted = [base - timedelta(days=int(d)) for d in days_back]
        return pd.DataFrame({
            "student_id":    rng.choice(stu_arr, count, replace=True),
            "assignment_id": rng.choice(assign_arr, count, replace=True),
            "score":         np.round(rng.uniform(0, 100, count), 2),
            "submitted_at":  submitted,
            "is_late":       rng.integers(0, 2, count),
        })

    _insert_batched(engine, "student_grades", _grade_batch, n_grades)

    # extracurricular_activities — up to 80 rows
    n_acts = min(80, len(ACTIVITY_NAMES))
    act_rows = [{
        "name":               ACTIVITY_NAMES[i],
        "activity_type":      ACTIVITY_TYPES[i % len(ACTIVITY_TYPES)],
        "sponsor_teacher_id": random.choice(teacher_ids),
        "meeting_schedule":   f"{random.choice(days)} at {random.randint(3,5)}:00 PM",
        "max_members":        random.choice([15, 20, 25, 30, 40, 50, None]),
    } for i in range(n_acts)]
    _insert(engine, "extracurricular_activities", pd.DataFrame(act_rows))
    with engine.connect() as conn:
        act_ids = pd.read_sql(
            "SELECT activity_id FROM extracurricular_activities", conn
        )["activity_id"].tolist()

    # student_activities — up to 4 per student
    sa_rows = []
    for sid in stu_ids:
        n = random.randint(0, min(4, len(act_ids)))
        for act in random.sample(act_ids, n):
            sa_rows.append({
                "student_id":  sid,
                "activity_id": act,
                "role":        random.choices(
                    ["Member", "Leader", "Co-Captain", "President", "Secretary"],
                    weights=[0.70, 0.10, 0.08, 0.06, 0.06],
                )[0],
                "joined_date": _rand_date(date(2020, 9, 1), date(2024, 9, 1)),
            })
    _insert(engine, "student_activities", pd.DataFrame(sa_rows))

    # disciplinary_records
    disc_n = min(8_000, n_stu * 2)
    disc_sids = rng.choice(stu_arr, disc_n, replace=True)
    disc_rows = []
    for sid in disc_sids:
        inc_date = _rand_date(date(2020, 9, 1), date(2025, 5, 1))
        resolved = random.random() > 0.2
        disc_rows.append({
            "student_id":    sid,
            "date":          inc_date,
            "incident_type": random.choice(DISC_TYPES),
            "severity":      random.choice(DISC_SEVERITY),
            "description":   fake.sentence()[:500],
            "action_taken":  fake.sentence()[:200] if resolved else None,
            "resolved":      1 if resolved else 0,
            "resolved_date": _rand_date(inc_date, inc_date + timedelta(days=30)) if resolved else None,
        })
    _insert(engine, "disciplinary_records", pd.DataFrame(disc_rows))

    # attendance — batched (largest education table)
    all_school_days = _school_days(date(2022, 8, 15), date(2025, 5, 30))
    day_arr = np.array([d.toordinal() for d in all_school_days])

    stu_names_arr    = np.array([stu_names[s]    for s in stu_ids])
    stu_grades_arr   = np.array([stu_grades[s]   for s in stu_ids])
    stu_sections_arr = np.array([stu_sections[s] for s in stu_ids])
    n_attend = cfg["n_attendance"]

    def _attend_batch(offset: int, count: int) -> pd.DataFrame:
        idx     = rng.integers(0, len(stu_ids), count)
        day_ord = rng.choice(day_arr, count, replace=True)
        return pd.DataFrame({
            "student_id":    np.array(stu_ids)[idx],
            "student_name":  stu_names_arr[idx],
            "date":          [date.fromordinal(int(o)) for o in day_ord],
            "status":        rng.choice(ATTEND_STATUSES, count, p=ATTEND_WEIGHTS),
            "class_section": stu_sections_arr[idx],
            "grade":         stu_grades_arr[idx],
            "section_id":    None,
            "notes":         None,
        })

    _insert_batched(engine, "attendance", _attend_batch, n_attend)

    engine.dispose()
    print("  education_db seeded ✓")


# ══════════════════════════════════════════════════════════════════════════════
# FINANCE DB
# ══════════════════════════════════════════════════════════════════════════════

_FIN_DROPS = [
    "budgets", "expense_items", "expense_reports",
    "purchase_order_lines", "purchase_orders",
    "payments", "invoice_line_items", "invoices",
    "journal_entry_lines", "journal_entries",
    "transactions", "bank_accounts", "customers", "vendors",
    "chart_of_accounts", "account_types",
    "cost_centers", "fiscal_periods", "fiscal_years",
]

_FIN_CREATES = [
    """
    CREATE TABLE fiscal_years (
        year_id    INT IDENTITY(1,1) PRIMARY KEY,
        name       NVARCHAR(20)  NOT NULL,
        start_date DATE          NOT NULL,
        end_date   DATE          NOT NULL,
        is_closed  BIT           NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE fiscal_periods (
        period_id     INT IDENTITY(1,1) PRIMARY KEY,
        year_id       INT          NOT NULL REFERENCES fiscal_years(year_id),
        period_number TINYINT      NOT NULL,
        name          NVARCHAR(50) NOT NULL,
        start_date    DATE         NOT NULL,
        end_date      DATE         NOT NULL,
        is_closed     BIT          NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE cost_centers (
        cc_id      INT IDENTITY(1,1) PRIMARY KEY,
        code       NVARCHAR(20)  NOT NULL,
        name       NVARCHAR(150) NOT NULL,
        department NVARCHAR(100) NOT NULL,
        manager    NVARCHAR(200) NOT NULL,
        is_active  BIT           NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE account_types (
        type_id        INT IDENTITY(1,1) PRIMARY KEY,
        name           NVARCHAR(50)  NOT NULL,
        normal_balance NVARCHAR(10)  NOT NULL,
        category       NVARCHAR(50)  NOT NULL
    )
    """,
    """
    CREATE TABLE chart_of_accounts (
        account_id     INT IDENTITY(1,1) PRIMARY KEY,
        account_number NVARCHAR(20)  NOT NULL,
        name           NVARCHAR(200) NOT NULL,
        type_id        INT           NOT NULL REFERENCES account_types(type_id),
        parent_id      INT           NULL,
        is_active      BIT           NOT NULL DEFAULT 1,
        description    NVARCHAR(500) NULL
    )
    """,
    """
    CREATE TABLE vendors (
        vendor_id     INT IDENTITY(1,1) PRIMARY KEY,
        name          NVARCHAR(200) NOT NULL,
        category      NVARCHAR(100) NOT NULL,
        payment_terms NVARCHAR(50)  NOT NULL,
        tax_id        NVARCHAR(30)  NULL,
        email         NVARCHAR(200) NULL,
        phone         NVARCHAR(30)  NULL,
        is_active     BIT           NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE customers (
        customer_id  INT IDENTITY(1,1) PRIMARY KEY,
        name         NVARCHAR(200) NOT NULL,
        industry     NVARCHAR(100) NOT NULL,
        credit_limit DECIMAL(14,2) NOT NULL,
        since_date   DATE          NOT NULL,
        email        NVARCHAR(200) NULL,
        phone        NVARCHAR(30)  NULL,
        is_active    BIT           NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE bank_accounts (
        bank_id        INT IDENTITY(1,1) PRIMARY KEY,
        name           NVARCHAR(100) NOT NULL,
        account_number NVARCHAR(50)  NOT NULL,
        institution    NVARCHAR(200) NOT NULL,
        account_type   NVARCHAR(50)  NOT NULL,
        currency       NVARCHAR(5)   NOT NULL DEFAULT 'USD',
        is_active      BIT           NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE transactions (
        txn_id      INT IDENTITY(1,1) PRIMARY KEY,
        date        DATE           NOT NULL,
        amount      DECIMAL(14,2)  NOT NULL,
        txn_type    NVARCHAR(10)   NOT NULL,
        account_id  INT            NOT NULL REFERENCES chart_of_accounts(account_id),
        cc_id       INT            NOT NULL REFERENCES cost_centers(cc_id),
        description NVARCHAR(500)  NOT NULL,
        reference   NVARCHAR(100)  NULL,
        status      NVARCHAR(20)   NOT NULL DEFAULT 'Cleared'
    )
    """,
    """
    CREATE TABLE journal_entries (
        je_id       INT IDENTITY(1,1) PRIMARY KEY,
        date        DATE          NOT NULL,
        description NVARCHAR(500) NOT NULL,
        posted_by   NVARCHAR(200) NOT NULL,
        period_id   INT           NOT NULL REFERENCES fiscal_periods(period_id),
        is_posted   BIT           NOT NULL DEFAULT 1,
        reference   NVARCHAR(100) NULL
    )
    """,
    """
    CREATE TABLE journal_entry_lines (
        line_id    INT IDENTITY(1,1) PRIMARY KEY,
        je_id      INT           NOT NULL REFERENCES journal_entries(je_id),
        account_id INT           NOT NULL REFERENCES chart_of_accounts(account_id),
        debit      DECIMAL(14,2) NOT NULL DEFAULT 0,
        credit     DECIMAL(14,2) NOT NULL DEFAULT 0,
        memo       NVARCHAR(300) NULL
    )
    """,
    """
    CREATE TABLE invoices (
        invoice_id   INT IDENTITY(1,1) PRIMARY KEY,
        customer_id  INT           NOT NULL REFERENCES customers(customer_id),
        invoice_date DATE          NOT NULL,
        due_date     DATE          NOT NULL,
        total        DECIMAL(14,2) NOT NULL,
        tax          DECIMAL(14,2) NOT NULL DEFAULT 0,
        status       NVARCHAR(20)  NOT NULL DEFAULT 'unpaid',
        notes        NVARCHAR(500) NULL
    )
    """,
    """
    CREATE TABLE invoice_line_items (
        line_id     INT IDENTITY(1,1) PRIMARY KEY,
        invoice_id  INT           NOT NULL REFERENCES invoices(invoice_id),
        description NVARCHAR(300) NOT NULL,
        quantity    DECIMAL(10,3) NOT NULL,
        unit_price  DECIMAL(14,2) NOT NULL,
        amount      DECIMAL(14,2) NOT NULL
    )
    """,
    """
    CREATE TABLE payments (
        payment_id   INT IDENTITY(1,1) PRIMARY KEY,
        invoice_id   INT           NOT NULL REFERENCES invoices(invoice_id),
        amount       DECIMAL(14,2) NOT NULL,
        payment_date DATE          NOT NULL,
        method       NVARCHAR(50)  NOT NULL,
        bank_id      INT           NULL REFERENCES bank_accounts(bank_id),
        reference    NVARCHAR(100) NULL,
        status       NVARCHAR(20)  NOT NULL DEFAULT 'cleared'
    )
    """,
    """
    CREATE TABLE purchase_orders (
        po_id         INT IDENTITY(1,1) PRIMARY KEY,
        vendor_id     INT           NOT NULL REFERENCES vendors(vendor_id),
        cc_id         INT           NOT NULL REFERENCES cost_centers(cc_id),
        order_date    DATE          NOT NULL,
        expected_date DATE          NULL,
        total         DECIMAL(14,2) NOT NULL,
        status        NVARCHAR(20)  NOT NULL DEFAULT 'pending',
        notes         NVARCHAR(500) NULL
    )
    """,
    """
    CREATE TABLE purchase_order_lines (
        line_id     INT IDENTITY(1,1) PRIMARY KEY,
        po_id       INT           NOT NULL REFERENCES purchase_orders(po_id),
        description NVARCHAR(300) NOT NULL,
        quantity    DECIMAL(10,3) NOT NULL,
        unit_cost   DECIMAL(14,2) NOT NULL,
        total       DECIMAL(14,2) NOT NULL
    )
    """,
    """
    CREATE TABLE expense_reports (
        report_id      INT IDENTITY(1,1) PRIMARY KEY,
        employee       NVARCHAR(200) NOT NULL,
        cc_id          INT           NOT NULL REFERENCES cost_centers(cc_id),
        submitted_date DATE          NOT NULL,
        approved_date  DATE          NULL,
        total          DECIMAL(14,2) NOT NULL,
        status         NVARCHAR(20)  NOT NULL DEFAULT 'pending',
        notes          NVARCHAR(500) NULL
    )
    """,
    """
    CREATE TABLE expense_items (
        item_id     INT IDENTITY(1,1) PRIMARY KEY,
        report_id   INT           NOT NULL REFERENCES expense_reports(report_id),
        category    NVARCHAR(100) NOT NULL,
        amount      DECIMAL(14,2) NOT NULL,
        date        DATE          NOT NULL,
        description NVARCHAR(300) NOT NULL,
        receipt_ref NVARCHAR(100) NULL
    )
    """,
    """
    CREATE TABLE budgets (
        budget_id       INT IDENTITY(1,1) PRIMARY KEY,
        cc_id           INT           NOT NULL REFERENCES cost_centers(cc_id),
        account_id      INT           NOT NULL REFERENCES chart_of_accounts(account_id),
        period_id       INT           NOT NULL REFERENCES fiscal_periods(period_id),
        budgeted_amount DECIMAL(14,2) NOT NULL,
        actual_amount   DECIMAL(14,2) NOT NULL DEFAULT 0,
        variance        AS (budgeted_amount - actual_amount)
    )
    """,
]


def _create_fin_tables(engine) -> None:
    print("  Dropping old tables (if any)...")
    for tbl in _FIN_DROPS:
        _exec(engine, f"IF OBJECT_ID('{tbl}','U') IS NOT NULL DROP TABLE [{tbl}]")
    print("  Creating tables...")
    for ddl in _FIN_CREATES:
        _exec(engine, ddl)


def seed_finance(cfg: dict) -> None:
    print("\n[Step 3] Seeding finance_db...")
    engine = _engine("finance_db")
    _create_fin_tables(engine)

    # fiscal_years — 5 rows
    fy_rows = [
        {"name": f"FY{yr}", "start_date": date(yr, 1, 1), "end_date": date(yr, 12, 31),
         "is_closed": 1 if yr < 2024 else 0}
        for yr in range(2020, 2025)
    ]
    _insert(engine, "fiscal_years", pd.DataFrame(fy_rows))
    with engine.connect() as conn:
        fy_df = pd.read_sql("SELECT year_id, start_date FROM fiscal_years", conn)

    # fiscal_periods — 60 rows (12 months × 5 years)
    month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    fp_rows = []
    for _, fy in fy_df.iterrows():
        yr = pd.Timestamp(fy["start_date"]).year
        for m in range(1, 13):
            end_d = (date(yr, m + 1, 1) - timedelta(days=1)) if m < 12 else date(yr, 12, 31)
            fp_rows.append({
                "year_id":       int(fy["year_id"]),
                "period_number": m,
                "name":          f"{month_names[m-1]} {yr}",
                "start_date":    date(yr, m, 1),
                "end_date":      end_d,
                "is_closed":     1 if yr < 2024 else 0,
            })
    _insert(engine, "fiscal_periods", pd.DataFrame(fp_rows))
    with engine.connect() as conn:
        fp_df = pd.read_sql("SELECT period_id, start_date, end_date FROM fiscal_periods", conn)
    period_ids = fp_df["period_id"].tolist()
    fp_dates = {
        row["period_id"]: (pd.Timestamp(row["start_date"]).date(), pd.Timestamp(row["end_date"]).date())
        for _, row in fp_df.iterrows()
    }

    # cost_centers — 150 rows
    cc_rows = [
        {
            "code":       f"CC-{i+1:04d}",
            "name":       fake.bs()[:60],
            "department": random.choice(["Finance", "Operations", "HR", "IT", "Marketing",
                                         "Sales", "R&D", "Legal", "Admin", "Facilities"]),
            "manager":    fake.name(),
            "is_active":  1 if random.random() > 0.05 else 0,
        }
        for i in range(150)
    ]
    _insert(engine, "cost_centers", pd.DataFrame(cc_rows))
    with engine.connect() as conn:
        cc_ids = pd.read_sql("SELECT cc_id FROM cost_centers", conn)["cc_id"].tolist()

    # account_types — 6 rows
    at_rows = [{"name": n, "normal_balance": b, "category": c} for n, b, c in ACCOUNT_TYPES_DATA]
    _insert(engine, "account_types", pd.DataFrame(at_rows))
    with engine.connect() as conn:
        at_df = pd.read_sql("SELECT type_id, name FROM account_types", conn)
    at_map = dict(zip(at_df["name"], at_df["type_id"]))

    # chart_of_accounts — 1500 rows
    coa_rows = []
    for atype, (lo, hi) in ACCOUNT_RANGES.items():
        count = (hi - lo) // 2
        for num in range(lo, lo + min(count, 250), 2):
            coa_rows.append({
                "account_number": str(num),
                "name":           f"{atype} – {fake.bs()[:60]}",
                "type_id":        at_map[atype],
                "parent_id":      None,
                "is_active":      1 if random.random() > 0.05 else 0,
                "description":    fake.sentence()[:200] if random.random() > 0.5 else None,
            })
    _insert(engine, "chart_of_accounts", pd.DataFrame(coa_rows[:1500]))
    with engine.connect() as conn:
        account_ids = pd.read_sql(
            "SELECT account_id FROM chart_of_accounts WHERE is_active=1", conn
        )["account_id"].tolist()

    # vendors — 3000 rows
    vendor_rows = [
        {
            "name":          fake.company(),
            "category":      random.choice(VENDOR_CATEGORIES),
            "payment_terms": random.choice(PAYMENT_TERMS),
            "tax_id":        f"{random.randint(10,99)}-{random.randint(1000000,9999999)}",
            "email":         fake.company_email() if random.random() > 0.2 else None,
            "phone":         fake.phone_number()[:30] if random.random() > 0.1 else None,
            "is_active":     1 if random.random() > 0.05 else 0,
        }
        for _ in range(3_000)
    ]
    _insert(engine, "vendors", pd.DataFrame(vendor_rows))
    with engine.connect() as conn:
        vendor_ids = pd.read_sql("SELECT vendor_id FROM vendors", conn)["vendor_id"].tolist()

    # customers — 8000 rows
    cust_rows = [
        {
            "name":         fake.company(),
            "industry":     random.choice(CUSTOMER_INDUSTRIES),
            "credit_limit": round(random.choice([10_000, 25_000, 50_000, 100_000, 250_000, 500_000]), 2),
            "since_date":   _rand_date(date(2010, 1, 1), date(2023, 12, 31)),
            "email":        fake.company_email() if random.random() > 0.1 else None,
            "phone":        fake.phone_number()[:30] if random.random() > 0.1 else None,
            "is_active":    1 if random.random() > 0.03 else 0,
        }
        for _ in range(8_000)
    ]
    _insert(engine, "customers", pd.DataFrame(cust_rows))
    with engine.connect() as conn:
        cust_ids = pd.read_sql("SELECT customer_id FROM customers", conn)["customer_id"].tolist()

    # bank_accounts — 50 rows
    banks = ["Chase", "Bank of America", "Wells Fargo", "Citibank", "TD Bank",
             "PNC", "US Bank", "Truist", "Capital One", "Citizens Bank"]
    bank_rows = [
        {
            "name":           f"{random.choice(banks)} {random.choice(['Checking','Savings','Money Market'])}",
            "account_number": f"****{random.randint(1000,9999)}",
            "institution":    random.choice(banks),
            "account_type":   random.choice(["Checking", "Savings", "Money Market", "CD"]),
            "currency":       "USD",
            "is_active":      1 if random.random() > 0.1 else 0,
        }
        for _ in range(50)
    ]
    _insert(engine, "bank_accounts", pd.DataFrame(bank_rows))
    with engine.connect() as conn:
        bank_ids = pd.read_sql("SELECT bank_id FROM bank_accounts", conn)["bank_id"].tolist()

    # Precompute numpy arrays for fast batch generation
    acc_arr  = np.array(account_ids)
    cc_arr   = np.array(cc_ids)
    fp_arr   = np.array(period_ids)
    cust_arr = np.array(cust_ids)
    vend_arr = np.array(vendor_ids)
    bank_arr = np.array(bank_ids)

    txn_start = date(2020, 1, 1).toordinal()
    txn_end   = date(2024, 12, 31).toordinal()

    # transactions — batched (largest finance table)
    n_txn = cfg["n_transactions"]

    def _txn_batch(offset: int, count: int) -> pd.DataFrame:
        day_ord = rng.integers(txn_start, txn_end + 1, count)
        amounts = np.clip(np.round(rng.exponential(scale=8_000, size=count), 2), 1, 5_000_000)
        return pd.DataFrame({
            "date":        [date.fromordinal(int(d)) for d in day_ord],
            "amount":      amounts,
            "txn_type":    rng.choice(TXN_TYPES, count),
            "account_id":  rng.choice(acc_arr, count, replace=True),
            "cc_id":       rng.choice(cc_arr, count, replace=True),
            "description": [f"TXN-{offset+i+1:08d}" for i in range(count)],
            "reference":   [f"REF-{random.randint(100000,999999)}" for _ in range(count)],
            "status":      rng.choice(TXN_STATUSES, count, p=TXN_STATUS_W),
        })

    _insert_batched(engine, "transactions", _txn_batch, n_txn)

    # journal_entries
    n_je = min(200_000, cfg["n_je_lines"] // 3)
    je_rows = []
    for i in range(n_je):
        pid = random.choice(period_ids)
        ps, pe = fp_dates[pid]
        je_rows.append({
            "date":        _rand_date(ps, pe),
            "description": f"JE-{i+1:07d}: {fake.bs()[:60]}",
            "posted_by":   fake.name(),
            "period_id":   pid,
            "is_posted":   1 if random.random() > 0.05 else 0,
            "reference":   f"REF-{random.randint(100000,999999)}" if random.random() > 0.3 else None,
        })
    _insert(engine, "journal_entries", pd.DataFrame(je_rows))
    with engine.connect() as conn:
        je_ids = pd.read_sql("SELECT je_id FROM journal_entries", conn)["je_id"].tolist()
    je_arr = np.array(je_ids)

    # journal_entry_lines — batched
    n_je_lines = cfg["n_je_lines"]

    def _jel_batch(offset: int, count: int) -> pd.DataFrame:
        amounts  = np.clip(np.round(rng.exponential(scale=5_000, size=count), 2), 1, 1_000_000)
        is_debit = rng.integers(0, 2, count).astype(bool)
        return pd.DataFrame({
            "je_id":      rng.choice(je_arr, count, replace=True),
            "account_id": rng.choice(acc_arr, count, replace=True),
            "debit":      np.where(is_debit, amounts, 0.0),
            "credit":     np.where(~is_debit, amounts, 0.0),
            "memo":       [f"Memo-{offset+i}" for i in range(count)],
        })

    _insert_batched(engine, "journal_entry_lines", _jel_batch, n_je_lines)

    # invoices
    n_inv = min(80_000, cfg["n_inv_lines"] // 4)
    inv_rows = []
    for _ in range(n_inv):
        inv_date = _rand_date(date(2020, 1, 1), date(2024, 12, 31))
        total    = round(random.uniform(500, 250_000), 2)
        inv_rows.append({
            "customer_id":  random.choice(cust_ids),
            "invoice_date": inv_date,
            "due_date":     inv_date + timedelta(days=random.choice([15, 30, 60, 90])),
            "total":        total,
            "tax":          round(total * random.uniform(0, 0.10), 2),
            "status":       random.choices(INV_STATUSES, weights=INV_STATUS_W)[0],
            "notes":        None,
        })
    _insert(engine, "invoices", pd.DataFrame(inv_rows))
    with engine.connect() as conn:
        inv_ids = pd.read_sql("SELECT invoice_id FROM invoices", conn)["invoice_id"].tolist()
    inv_arr = np.array(inv_ids)

    # invoice_line_items — batched
    n_inv_lines = cfg["n_inv_lines"]

    def _invl_batch(offset: int, count: int) -> pd.DataFrame:
        qty   = np.round(rng.uniform(1, 100, count), 3)
        price = np.clip(np.round(rng.exponential(scale=500, size=count), 2), 1, 50_000)
        return pd.DataFrame({
            "invoice_id":  rng.choice(inv_arr, count, replace=True),
            "description": [f"Item-{offset+i}" for i in range(count)],
            "quantity":    qty,
            "unit_price":  price,
            "amount":      np.round(qty * price, 2),
        })

    _insert_batched(engine, "invoice_line_items", _invl_batch, n_inv_lines)

    # payments
    n_pay = cfg["n_payments"]
    pay_rows = []
    for _ in range(n_pay):
        pay_rows.append({
            "invoice_id":   random.choice(inv_ids),
            "amount":       round(random.uniform(100, 50_000), 2),
            "payment_date": _rand_date(date(2020, 1, 1), date(2025, 5, 1)),
            "method":       random.choice(PAYMENT_METHODS),
            "bank_id":      random.choice(bank_ids) if random.random() > 0.2 else None,
            "reference":    f"PAY-{random.randint(100000,999999)}",
            "status":       "cleared",
        })
    _insert(engine, "payments", pd.DataFrame(pay_rows))

    # purchase_orders
    n_po = min(30_000, cfg["n_po_lines"] // 4)
    po_rows = []
    for _ in range(n_po):
        order_date = _rand_date(date(2020, 1, 1), date(2024, 12, 31))
        po_rows.append({
            "vendor_id":     random.choice(vendor_ids),
            "cc_id":         random.choice(cc_ids),
            "order_date":    order_date,
            "expected_date": order_date + timedelta(days=random.randint(7, 90)),
            "total":         round(random.uniform(200, 500_000), 2),
            "status":        random.choices(PO_STATUSES, weights=PO_STATUS_W)[0],
            "notes":         None,
        })
    _insert(engine, "purchase_orders", pd.DataFrame(po_rows))
    with engine.connect() as conn:
        po_ids = pd.read_sql("SELECT po_id FROM purchase_orders", conn)["po_id"].tolist()
    po_arr = np.array(po_ids)

    # purchase_order_lines — batched
    n_po_lines = cfg["n_po_lines"]

    def _pol_batch(offset: int, count: int) -> pd.DataFrame:
        qty  = np.round(rng.uniform(1, 500, count), 3)
        cost = np.clip(np.round(rng.exponential(scale=200, size=count), 2), 1, 100_000)
        return pd.DataFrame({
            "po_id":       rng.choice(po_arr, count, replace=True),
            "description": [f"POLine-{offset+i}" for i in range(count)],
            "quantity":    qty,
            "unit_cost":   cost,
            "total":       np.round(qty * cost, 2),
        })

    _insert_batched(engine, "purchase_order_lines", _pol_batch, n_po_lines)

    # expense_reports
    n_exp = min(15_000, cfg["n_expense_items"] // 5)
    exp_rows = []
    for _ in range(n_exp):
        sub_date = _rand_date(date(2020, 1, 1), date(2025, 3, 31))
        status = random.choices(EXP_STATUSES, weights=EXP_STATUS_W)[0]
        exp_rows.append({
            "employee":       fake.name(),
            "cc_id":          random.choice(cc_ids),
            "submitted_date": sub_date,
            "approved_date":  sub_date + timedelta(days=random.randint(1, 14)) if status in ("approved", "paid") else None,
            "total":          round(random.uniform(50, 10_000), 2),
            "status":         status,
            "notes":          None,
        })
    _insert(engine, "expense_reports", pd.DataFrame(exp_rows))
    with engine.connect() as conn:
        report_ids = pd.read_sql("SELECT report_id FROM expense_reports", conn)["report_id"].tolist()
    rep_arr = np.array(report_ids)

    # expense_items — batched
    n_exp_items = cfg["n_expense_items"]

    def _expi_batch(offset: int, count: int) -> pd.DataFrame:
        day_ord = rng.integers(txn_start, txn_end + 1, count)
        return pd.DataFrame({
            "report_id":   rng.choice(rep_arr, count, replace=True),
            "category":    rng.choice(EXP_CATEGORIES, count),
            "amount":      np.clip(np.round(rng.exponential(scale=200, size=count), 2), 1, 5_000),
            "date":        [date.fromordinal(int(d)) for d in day_ord],
            "description": [f"ExpItem-{offset+i}" for i in range(count)],
            "receipt_ref": [f"RCP-{random.randint(10000,99999)}" if random.random() > 0.3 else None
                            for _ in range(count)],
        })

    _insert_batched(engine, "expense_items", _expi_batch, n_exp_items)

    # budgets — 750 rows
    budget_rows = []
    pairs_seen_b: set = set()
    attempts = 0
    while len(budget_rows) < 750 and attempts < 10_000:
        cc  = random.choice(cc_ids)
        acc = random.choice(account_ids)
        per = random.choice(period_ids)
        key = (cc, acc, per)
        attempts += 1
        if key in pairs_seen_b:
            continue
        pairs_seen_b.add(key)
        budgeted = round(random.uniform(5_000, 500_000), 2)
        budget_rows.append({
            "cc_id":           cc,
            "account_id":      acc,
            "period_id":       per,
            "budgeted_amount": budgeted,
            "actual_amount":   round(budgeted * random.uniform(0.5, 1.5), 2),
        })
    _insert(engine, "budgets", pd.DataFrame(budget_rows))

    engine.dispose()
    print("  finance_db seeded ✓")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    global SERVER, USERNAME, PASSWORD, AUTH_METHOD

    parser = argparse.ArgumentParser(description="Seed test SQL Server databases.")
    parser.add_argument("--scale", choices=["small", "medium", "large"], default="large",
                        help="Data volume (default: large)")
    parser.add_argument("--server", default=SERVER, help="SQL Server host (default: localhost,1433)")
    parser.add_argument("--username", default=USERNAME, help="SQL login (sql auth only)")
    parser.add_argument("--password", default=PASSWORD, help="SQL password (sql auth only)")
    parser.add_argument("--auth-method", choices=["sql", "windows"], default=AUTH_METHOD,
                        help="Authentication method (default: sql)")
    args = parser.parse_args()

    SERVER      = args.server
    USERNAME    = args.username
    PASSWORD    = args.password
    AUTH_METHOD = args.auth_method
    cfg         = SCALES[args.scale]

    print(f"Scale: {args.scale.upper()} | Server: {SERVER} | Auth: {AUTH_METHOD}")
    print(f"  Students:     {cfg['n_students']:,}")
    print(f"  Attendance:   {cfg['n_attendance']:,}")
    print(f"  Transactions: {cfg['n_transactions']:,}")

    create_databases()
    seed_education(cfg)
    seed_finance(cfg)

    print("\nAll done! Both databases are seeded and ready.")


if __name__ == "__main__":
    main()
