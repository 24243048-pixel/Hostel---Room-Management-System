"""SQLite database setup and connection helpers for the hostel application."""

import os
import sqlite3
import calendar
from contextlib import contextmanager
from datetime import date, timedelta

from werkzeug.security import generate_password_hash


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.environ.get("HOSTEL_DATABASE_PATH", os.path.join(BASE_DIR, "hostel.db"))

ATTENDANCE_SAMPLE_STUDENTS = (
    ("ST001", "Priya", "101", "Computer Science", 3, "priya.st001@example.com"),
    ("ST002", "Kavin", "102", "Information Technology", 3, "kavin.st002@example.com"),
    ("ST003", "Swetha", "103", "Computer Science", 2, "swetha.st003@example.com"),
    ("ST004", "Harini", "104", "Information Technology", 3, "harini.st004@example.com"),
    ("ST005", "Divya", "105", "Electronics and Communication", 2, "divya.st005@example.com"),
    ("ST006", "Keerthana", "106", "Computer Science", 3, "keerthana.st006@example.com"),
    ("ST007", "Nithya", "107", "Information Technology", 2, "nithya.st007@example.com"),
    ("ST008", "Arun", "108", "Mechanical Engineering", 3, "arun.st008@example.com"),
    ("ST009", "Santhiya", "109", "Computer Science", 3, "santhiya.st009@example.com"),
    ("ST010", "Dinesh", "110", "Civil Engineering", 2, "dinesh.st010@example.com"),
    ("ST011", "Pavithra", "201", "Information Technology", 3, "pavithra.st011@example.com"),
    ("ST012", "Vignesh", "202", "Computer Science", 2, "vignesh.st012@example.com"),
    ("ST013", "Abinaya", "203", "Electronics and Communication", 3, "abinaya.st013@example.com"),
    ("ST014", "Rohit", "204", "Mechanical Engineering", 2, "rohit.st014@example.com"),
    ("ST015", "Monika", "205", "Computer Science", 3, "monika.st015@example.com"),
    ("ST016", "Suresh", "206", "Civil Engineering", 3, "suresh.st016@example.com"),
    ("ST017", "Aishwarya", "207", "Information Technology", 2, "aishwarya.st017@example.com"),
    ("ST018", "Naveen", "208", "Computer Science", 3, "naveen.st018@example.com"),
    ("ST019", "Anjali", "209", "Electronics and Communication", 2, "anjali.st019@example.com"),
    ("ST020", "Praveen", "210", "Mechanical Engineering", 3, "praveen.st020@example.com"),
    ("ST021", "Janani", "301", "Computer Science", 2, "janani.st021@example.com"),
    ("ST022", "Sanjay", "302", "Information Technology", 3, "sanjay.st022@example.com"),
    ("ST023", "Dharani", "303", "Computer Science", 3, "dharani.st023@example.com"),
    ("ST024", "Ashwin", "304", "Civil Engineering", 2, "ashwin.st024@example.com"),
    ("ST025", "Deepika", "305", "Electronics and Communication", 3, "deepika.st025@example.com"),
    ("ST026", "Hari", "306", "Computer Science", 2, "hari.st026@example.com"),
    ("ST027", "Abishek", "307", "Information Technology", 3, "abishek.st027@example.com"),
    ("ST028", "Vaishnavi", "308", "Computer Science", 3, "vaishnavi.st028@example.com"),
    ("ST029", "Gokul", "309", "Mechanical Engineering", 2, "gokul.st029@example.com"),
    ("ST030", "Meena", "310", "Civil Engineering", 3, "meena.st030@example.com"),
    ("ST031", "Kishore", "401", "Computer Science", 2, "kishore.st031@example.com"),
    ("ST032", "Anusha", "402", "Information Technology", 3, "anusha.st032@example.com"),
    ("ST033", "Bala", "403", "Electronics and Communication", 2, "bala.st033@example.com"),
    ("ST034", "Shalini", "404", "Computer Science", 3, "shalini.st034@example.com"),
    ("ST035", "Surya", "405", "Mechanical Engineering", 3, "surya.st035@example.com"),
    ("ST036", "Gayathri", "406", "Information Technology", 2, "gayathri.st036@example.com"),
    ("ST037", "Lokesh", "407", "Computer Science", 3, "lokesh.st037@example.com"),
    ("ST038", "Ramya", "408", "Electronics and Communication", 2, "ramya.st038@example.com"),
    ("ST039", "Ajay", "409", "Civil Engineering", 3, "ajay.st039@example.com"),
    ("ST040", "Dharun", "410", "Computer Science", 2, "dharun.st040@example.com"),
)


@contextmanager
def get_db():
    """Yield a configured SQLite connection and commit or roll back its work."""
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db():
    """Create tables and insert a small demo data set on first launch."""
    with get_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                department TEXT NOT NULL,
                year INTEGER NOT NULL CHECK (year BETWEEN 1 AND 6),
                phone TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                gender TEXT NOT NULL CHECK (gender IN ('Female', 'Male', 'Other')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS rooms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_number TEXT NOT NULL UNIQUE,
                block TEXT NOT NULL,
                floor INTEGER NOT NULL CHECK (floor >= 0),
                capacity INTEGER NOT NULL CHECK (capacity > 0),
                occupied_count INTEGER NOT NULL DEFAULT 0 CHECK (occupied_count >= 0),
                CHECK (occupied_count <= capacity)
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'student')),
                student_id INTEGER UNIQUE REFERENCES students(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS allocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL UNIQUE REFERENCES students(id) ON DELETE CASCADE,
                room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE RESTRICT,
                allocated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS room_change_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                current_room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE RESTRICT,
                requested_room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE RESTRICT,
                reason TEXT NOT NULL CHECK (reason IN (
                    'Roommate Issue', 'Maintenance Problem', 'Study Environment Problem',
                    'Personal Reason', 'Health/Comfort Reason', 'Other'
                )),
                description TEXT NOT NULL CHECK (length(trim(description)) > 0),
                status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
                    status IN ('PENDING', 'APPROVED', 'REJECTED')
                ),
                admin_comment TEXT,
                admin_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_room_change_student_status
                ON room_change_requests(student_id, status);
            CREATE INDEX IF NOT EXISTS idx_room_change_status_date
                ON room_change_requests(status, requested_at);

            CREATE TABLE IF NOT EXISTS complaints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER REFERENCES students(id) ON DELETE SET NULL,
                room_number TEXT,
                category TEXT NOT NULL CHECK (
                    category IN ('Electrical', 'Plumbing', 'Wi-Fi', 'Furniture', 'Cleaning', 'Other')
                ),
                description TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'Pending' CHECK (
                    status IN ('Pending', 'In Progress', 'Resolved')
                )
            );

            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                attendance_date TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('Present', 'Absent', 'Late', 'Leave')),
                notes TEXT NOT NULL DEFAULT '',
                marked_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                marked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (student_id, attendance_date)
            );
            CREATE INDEX IF NOT EXISTS idx_attendance_date_status
                ON attendance(attendance_date, status);
            CREATE INDEX IF NOT EXISTS idx_attendance_student_date
                ON attendance(student_id, attendance_date);

            CREATE TABLE IF NOT EXISTS fee_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                period TEXT NOT NULL,
                fee_amount REAL NOT NULL CHECK (fee_amount >= 0),
                paid_amount REAL NOT NULL DEFAULT 0 CHECK (paid_amount >= 0),
                pending_amount REAL NOT NULL CHECK (pending_amount >= 0),
                due_date TEXT NOT NULL,
                payment_date TEXT,
                payment_method TEXT,
                transaction_id TEXT,
                status TEXT NOT NULL CHECK (
                    status IN ('PAID', 'PARTIALLY PAID', 'NOT PAID', 'OVERDUE')
                ),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (student_id, period),
                CHECK (paid_amount <= fee_amount),
                CHECK (ABS(pending_amount - (fee_amount - paid_amount)) < 0.005)
            );

            CREATE TABLE IF NOT EXISTS fee_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fee_record_id INTEGER NOT NULL REFERENCES fee_records(id) ON DELETE CASCADE,
                amount REAL NOT NULL CHECK (amount > 0),
                payment_date TEXT NOT NULL,
                transaction_id TEXT NOT NULL UNIQUE,
                payment_method TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PAID' CHECK (status = 'PAID'),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_fee_records_student
                ON fee_records(student_id, due_date);
            CREATE INDEX IF NOT EXISTS idx_fee_payments_date
                ON fee_payments(payment_date);
            """
        )

        attendance_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(attendance)").fetchall()
        }
        if "created_at" not in attendance_columns:
            db.execute("ALTER TABLE attendance ADD COLUMN created_at TEXT")
        if "updated_at" not in attendance_columns:
            db.execute("ALTER TABLE attendance ADD COLUMN updated_at TEXT")
        db.execute(
            """UPDATE attendance
               SET created_at = COALESCE(created_at, marked_at, CURRENT_TIMESTAMP),
                   updated_at = COALESCE(updated_at, marked_at, CURRENT_TIMESTAMP)
               WHERE created_at IS NULL OR updated_at IS NULL"""
        )

        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            demo_students = [
                ("STU001", "Aarav Sharma", "Computer Science", 2, "9876543210", "aarav@example.com", "Male"),
                ("STU002", "Meera Patel", "Electronics", 3, "9876543211", "meera@example.com", "Female"),
                ("STU003", "Kabir Singh", "Mechanical", 1, "9876543212", "kabir@example.com", "Male"),
                ("STU004", "Anaya Rao", "Computer Science", 2, "9876543213", "anaya@example.com", "Female"),
            ]
            db.executemany(
                """INSERT INTO students
                   (student_id, name, department, year, phone, email, gender)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                demo_students,
            )
            demo_rooms = [
                ("A-101", "A", 1, 4, 2),
                ("A-102", "A", 1, 2, 1),
                ("B-201", "B", 2, 3, 0),
            ]
            db.executemany(
                """INSERT INTO rooms (room_number, block, floor, capacity, occupied_count)
                   VALUES (?, ?, ?, ?, ?)""",
                demo_rooms,
            )

            student_rows = db.execute("SELECT id, student_id FROM students").fetchall()
            student_ids = {row["student_id"]: row["id"] for row in student_rows}
            room_ids = {
                row["room_number"]: row["id"]
                for row in db.execute("SELECT id, room_number FROM rooms").fetchall()
            }
            db.executemany(
                """INSERT INTO allocations (student_id, room_id) VALUES (?, ?)""",
                [
                    (student_ids["STU001"], room_ids["A-101"]),
                    (student_ids["STU002"], room_ids["A-101"]),
                    (student_ids["STU003"], room_ids["A-102"]),
                ],
            )
            db.executemany(
                """INSERT INTO users (username, password_hash, role, student_id)
                   VALUES (?, ?, ?, ?)""",
                [
                    ("admin", generate_password_hash("admin123"), "admin", None),
                    (
                        "student",
                        generate_password_hash("student123"),
                        "student",
                        student_ids["STU001"],
                    ),
                ],
            )
            db.executemany(
                """INSERT INTO complaints (student_id, room_number, category, description, status)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (student_ids["STU001"], "A-101", "Wi-Fi", "The Wi-Fi signal is weak in the room.", "Pending"),
                    (student_ids["STU002"], "A-101", "Cleaning", "Please arrange a room cleaning.", "In Progress"),
                ],
            )

        if db.execute("SELECT COUNT(*) FROM fee_records").fetchone()[0] == 0:
            demo_student_codes = {"STU001", "STU002", "STU003", "STU004"}
            students = [
                student
                for student in db.execute(
                    "SELECT id, student_id FROM students ORDER BY student_id"
                ).fetchall()
                if student["student_id"] in demo_student_codes
            ]
            if students:
                today = date.today()
                academic_start = today.year if today.month >= 4 else today.year - 1
                period = f"{academic_start}-{str(academic_start + 1)[-2:]} Semester 1"
                due_soon = (today + timedelta(days=10)).isoformat()
                overdue = (today - timedelta(days=10)).isoformat()
                fee_by_student = {
                    student["student_id"]: (25000.0, 0.0, due_soon)
                    for student in students
                }
                if "STU001" in fee_by_student:
                    fee_by_student["STU001"] = (25000.0, 25000.0, due_soon)
                if "STU002" in fee_by_student:
                    fee_by_student["STU002"] = (25000.0, 10000.0, due_soon)
                if "STU003" in fee_by_student:
                    fee_by_student["STU003"] = (25000.0, 0.0, overdue)
                if "STU004" in fee_by_student:
                    fee_by_student["STU004"] = (25000.0, 0.0, due_soon)

                fee_record_ids = {}
                for student in students:
                    fee_amount, paid_amount, due_date = fee_by_student[student["student_id"]]
                    status = "PAID" if paid_amount >= fee_amount else (
                        "PARTIALLY PAID" if paid_amount > 0 else "NOT PAID"
                    )
                    cursor = db.execute(
                        """INSERT INTO fee_records
                           (student_id, period, fee_amount, paid_amount, pending_amount, due_date, status)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            student["id"], period, fee_amount, paid_amount,
                            round(fee_amount - paid_amount, 2), due_date, status,
                        ),
                    )
                    fee_record_ids[student["student_id"]] = cursor.lastrowid

                for student_code, amount, payment_date, method in (
                    ("STU001", 25000.0, today.isoformat(), "Bank transfer"),
                    ("STU002", 10000.0, (today - timedelta(days=4)).isoformat(), "UPI"),
                ):
                    if student_code in fee_record_ids:
                        transaction_id = f"DEMO-{student_code}-{today.strftime('%Y%m%d')}"
                        db.execute(
                            """INSERT INTO fee_payments
                               (fee_record_id, amount, payment_date, transaction_id, payment_method)
                               VALUES (?, ?, ?, ?, ?)""",
                            (
                                fee_record_ids[student_code], amount, payment_date,
                                transaction_id, method,
                            ),
                        )
                        db.execute(
                            """UPDATE fee_records
                               SET payment_date = ?, payment_method = ?, transaction_id = ?
                               WHERE id = ?""",
                            (payment_date, method, transaction_id, fee_record_ids[student_code]),
                        )

        student_password_hash = None
        for student_code, name, room_number, department, year, email in ATTENDANCE_SAMPLE_STUDENTS:
            existing = db.execute(
                "SELECT id FROM students WHERE student_id = ?", (student_code,)
            ).fetchone()
            if existing:
                student_pk = existing["id"]
            else:
                cursor = db.execute(
                    """INSERT INTO students
                       (student_id, name, department, year, phone, email, gender)
                       VALUES (?, ?, ?, ?, ?, ?, 'Other')""",
                    (
                        student_code, name, department, year,
                        f"910000{int(student_code[2:]):04d}", email,
                    ),
                )
                student_pk = cursor.lastrowid
            username = student_code
            existing_login = db.execute(
                "SELECT id FROM users WHERE student_id = ? AND role = 'student'",
                (student_pk,),
            ).fetchone()
            if not existing_login:
                if student_password_hash is None:
                    student_password_hash = generate_password_hash("student123")
                suffix = 1
                while db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                    username = f"{student_code}-{suffix}"
                    suffix += 1
                db.execute(
                    """INSERT INTO users (username, password_hash, role, student_id)
                       VALUES (?, ?, 'student', ?)""",
                    (username, student_password_hash, student_pk),
                )

            if existing:
                continue

            room = db.execute(
                "SELECT id, capacity, occupied_count FROM rooms WHERE room_number = ?",
                (room_number,),
            ).fetchone()
            if not room:
                cursor = db.execute(
                    """INSERT INTO rooms (room_number, block, floor, capacity, occupied_count)
                       VALUES (?, 'Sample', ?, 4, 0)""",
                    (room_number, int(room_number) // 100),
                )
                room_id = cursor.lastrowid
                available = True
            else:
                room_id = room["id"]
                available = room["occupied_count"] < room["capacity"]
            if available:
                db.execute(
                    "INSERT INTO allocations (student_id, room_id) VALUES (?, ?)",
                    (student_pk, room_id),
                )
                db.execute(
                    "UPDATE rooms SET occupied_count = occupied_count + 1 WHERE id = ?",
                    (room_id,),
                )

        if db.execute("SELECT COUNT(*) FROM room_change_requests").fetchone()[0] == 0:
            admin = db.execute(
                "SELECT id FROM users WHERE username = 'admin' AND role = 'admin'"
            ).fetchone()
            available_rooms = db.execute(
                """SELECT id, room_number FROM rooms
                   WHERE occupied_count < capacity ORDER BY room_number"""
            ).fetchall()
            seed_requests = []

            pending_student = db.execute(
                """SELECT s.id AS student_id, a.room_id AS current_room_id
                   FROM students s JOIN allocations a ON a.student_id = s.id
                   WHERE s.student_id = 'ST001'"""
            ).fetchone()
            if pending_student:
                target = next(
                    (
                        room for room in available_rooms
                        if room["id"] != pending_student["current_room_id"]
                    ),
                    None,
                )
                if target:
                    seed_requests.append(
                        (
                            pending_student["student_id"], pending_student["current_room_id"],
                            target["id"], "Study Environment Problem",
                            "I would like to move to a quieter room to focus on my studies.",
                            "PENDING", None, None,
                        )
                    )

            rejected_student = db.execute(
                """SELECT s.id AS student_id, a.room_id AS current_room_id
                   FROM students s JOIN allocations a ON a.student_id = s.id
                   WHERE s.student_id = 'ST002'"""
            ).fetchone()
            if rejected_student:
                target = next(
                    (
                        room for room in available_rooms
                        if room["id"] != rejected_student["current_room_id"]
                    ),
                    None,
                )
                if target:
                    seed_requests.append(
                        (
                            rejected_student["student_id"], rejected_student["current_room_id"],
                            target["id"], "Roommate Issue",
                            "I would prefer a different room arrangement.",
                            "REJECTED", "The requested room is reserved for another resident.",
                            admin["id"] if admin else None,
                        )
                    )

            approved_student = db.execute(
                """SELECT s.id AS student_id, a.room_id AS requested_room_id
                   FROM students s JOIN allocations a ON a.student_id = s.id
                   WHERE s.student_id = 'STU001'"""
            ).fetchone()
            if approved_student:
                old_room = db.execute(
                    "SELECT id FROM rooms WHERE id != ? ORDER BY room_number LIMIT 1",
                    (approved_student["requested_room_id"],),
                ).fetchone()
                if old_room:
                    seed_requests.append(
                        (
                            approved_student["student_id"], old_room["id"],
                            approved_student["requested_room_id"], "Personal Reason",
                            "A previous room change request was approved.",
                            "APPROVED", "Approved after reviewing room availability.",
                            admin["id"] if admin else None,
                        )
                    )

            for (
                student_pk, current_room_pk, requested_room_pk, reason, description,
                status, admin_comment, admin_user_pk,
            ) in seed_requests:
                db.execute(
                    """INSERT INTO room_change_requests
                       (student_id, current_room_id, requested_room_id, reason, description,
                        status, admin_comment, admin_user_id, reviewed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, CASE WHEN ? = 'PENDING'
                                                           THEN NULL ELSE CURRENT_TIMESTAMP END)""",
                    (
                        student_pk, current_room_pk, requested_room_pk, reason, description,
                        status, admin_comment, admin_user_pk, status,
                    ),
                )

        today = date.today()
        start_date = today
        for _ in range(6):
            month = start_date.month - 1
            year = start_date.year
            if month == 0:
                month = 12
                year -= 1
            start_date = date(year, month, min(start_date.day, calendar.monthrange(year, month)[1]))

        existing_students = db.execute(
            "SELECT id, student_id FROM students WHERE student_id BETWEEN 'ST001' AND 'ST040'"
        ).fetchall()
        for student in existing_students:
            index = int(student["student_id"][2:]) - 1
            fee_period = f"{today.year}-{today.year + 1} Attendance Sample"
            paid = index < 30
            fee_amount = float(4500 + (index % 4) * 250)
            paid_amount = fee_amount if paid else 0.0
            due_date = (today + timedelta(days=30)).isoformat()
            existing_fee = db.execute(
                "SELECT id FROM fee_records WHERE student_id = ? AND period = ?",
                (student["id"], fee_period),
            ).fetchone()
            if not existing_fee:
                payment_date = (
                    (today - timedelta(days=10 + (index * 7) % 45)).isoformat()
                    if paid else None
                )
                cursor = db.execute(
                    """INSERT INTO fee_records
                       (student_id, period, fee_amount, paid_amount, pending_amount, due_date,
                        payment_date, payment_method, transaction_id, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        student["id"], fee_period, fee_amount, paid_amount,
                        round(fee_amount - paid_amount, 2), due_date, payment_date,
                        "UPI" if paid and index % 2 else ("Bank transfer" if paid else None),
                        f"SAMPLE-{student['student_id']}-{today.year}" if paid else None,
                        "PAID" if paid else "NOT PAID",
                    ),
                )
                if paid:
                    db.execute(
                        """INSERT OR IGNORE INTO fee_payments
                           (fee_record_id, amount, payment_date, transaction_id, payment_method)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            cursor.lastrowid, paid_amount, payment_date,
                            f"SAMPLE-{student['student_id']}-{today.year}",
                            "UPI" if index % 2 else "Bank transfer",
                        ),
                    )

            current_day = start_date
            day_number = 0
            while current_day <= today:
                if current_day.weekday() < 5:
                    leave_marker = (index * 7 + day_number * 11) % 37
                    score = (index * 31 + day_number * 17 + day_number * index * 3) % 100
                    absence_threshold = (7, 14, 22, 31)[index % 4]
                    if leave_marker == 0:
                        status = "Leave"
                    elif score < absence_threshold:
                        status = "Absent"
                    else:
                        status = "Present"
                    db.execute(
                        """INSERT OR IGNORE INTO attendance
                           (student_id, attendance_date, status, notes, marked_by,
                            created_at, updated_at)
                           VALUES (?, ?, ?, '', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                        (student["id"], current_day.isoformat(), status),
                    )
                    day_number += 1
                current_day += timedelta(days=1)
