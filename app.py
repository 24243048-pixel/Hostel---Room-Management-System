"""Flask application entry point for Hostel Room Management System."""

import calendar
import csv
import io
import os
import re
import sqlite3
from functools import wraps
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from flask import Flask, flash, jsonify, make_response, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_db, init_db


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "hostel-room-management-dev-key")
app.jinja_env.filters["inr"] = lambda amount: f"₹{amount:,.2f}"
app.jinja_env.filters["format_date"] = lambda value: (
    date.fromisoformat(value).strftime("%d %b %Y") if value else "—"
)
app.jinja_env.filters["weekday"] = lambda value: date.fromisoformat(value).strftime("%A")

COMPLAINT_CATEGORIES = ("Electrical", "Plumbing", "Wi-Fi", "Furniture", "Cleaning", "Other")
COMPLAINT_STATUSES = ("Pending", "In Progress", "Resolved")
GENDERS = ("Female", "Male", "Other")
PAYMENT_METHODS = ("Cash", "UPI", "Bank transfer", "Card", "Other")
ATTENDANCE_STATUSES = ("Present", "Absent", "Late", "Leave")
ROOM_CHANGE_REASONS = (
    "Roommate Issue",
    "Maintenance Problem",
    "Study Environment Problem",
    "Personal Reason",
    "Health/Comfort Reason",
    "Other",
)


def fee_status(fee, today=None):
    """Return the current status, calculating overdue from the current date."""
    today = today or date.today()
    pending = round(float(fee["fee_amount"]) - float(fee["paid_amount"]), 2)
    if pending <= 0:
        return "PAID"
    if date.fromisoformat(fee["due_date"]) < today:
        return "OVERDUE"
    if float(fee["paid_amount"]) > 0:
        return "PARTIALLY PAID"
    return "NOT PAID"


def fee_record_dict(row):
    record = dict(row)
    record["pending_amount"] = round(record["fee_amount"] - record["paid_amount"], 2)
    record["computed_status"] = fee_status(record)
    return record


def sync_fee_statuses(db):
    """Persist time-dependent overdue states whenever fee data is read."""
    today = date.today().isoformat()
    expression = """CASE
        WHEN pending_amount <= 0 THEN 'PAID'
        WHEN due_date < ? THEN 'OVERDUE'
        WHEN paid_amount > 0 THEN 'PARTIALLY PAID'
        ELSE 'NOT PAID'
    END"""
    db.execute(
        f"""UPDATE fee_records
            SET status = {expression}, updated_at = CURRENT_TIMESTAMP
            WHERE status != {expression}""",
        (today, today),
    )


def parse_iso_date(value, field_label):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"Enter a valid {field_label}.") from None


def parse_money(value, field_label, *, allow_zero=False):
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"Enter a valid {field_label}.") from None
    if not amount.is_finite() or amount < 0 or (amount == 0 and not allow_zero):
        requirement = "zero or more" if allow_zero else "greater than zero"
        raise ValueError(f"{field_label.capitalize()} must be {requirement}.")
    if amount.as_tuple().exponent < -2:
        raise ValueError(f"{field_label.capitalize()} can have at most two decimal places.")
    if amount > Decimal("999999999999.99"):
        raise ValueError(f"{field_label.capitalize()} is too large.")
    return float(amount.quantize(Decimal("0.01")))


def current_fee_summary(db):
    """Summarize the most recent fee period for each student."""
    sync_fee_statuses(db)
    student_count = db.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    rows = db.execute(
        """SELECT f.*, s.student_id AS student_code, s.name
           FROM fee_records f JOIN students s ON s.id = f.student_id
           ORDER BY f.student_id, f.period DESC, f.id DESC"""
    ).fetchall()
    latest_by_student = {}
    for row in rows:
        latest_by_student.setdefault(row["student_id"], fee_record_dict(row))

    latest_records = list(latest_by_student.values())
    paid_count = sum(record["computed_status"] == "PAID" for record in latest_records)
    totals = db.execute(
        "SELECT COALESCE(SUM(paid_amount), 0), COALESCE(SUM(pending_amount), 0) FROM fee_records"
    ).fetchone()
    due_dates = [
        record["due_date"] for record in latest_records if record["pending_amount"] > 0
    ]
    due_date = min(due_dates, default=None)
    return {
        "students": student_count,
        "paid": paid_count,
        "pending_students": max(student_count - paid_count, 0),
        "collected": round(totals[0], 2),
        "pending_amount": round(totals[1], 2),
        "due_date": due_date,
        "due_is_overdue": bool(due_date and date.fromisoformat(due_date) < date.today()),
    }


def monthly_fee_collections(db):
    """Return actual payment totals for the current and previous five months."""
    today = date.today()
    months = []
    year, month = today.year, today.month
    for _ in range(6):
        months.append((year, month))
        month -= 1
        if month == 0:
            year -= 1
            month = 12
    months.reverse()
    totals = {
        row["month"]: row["total"]
        for row in db.execute(
            """SELECT strftime('%Y-%m', payment_date) AS month, SUM(amount) AS total
               FROM fee_payments
               WHERE payment_date >= ? GROUP BY strftime('%Y-%m', payment_date)""",
            (date(months[0][0], months[0][1], 1).isoformat(),),
        ).fetchall()
    }
    return [
        {
            "key": f"{year:04d}-{month:02d}",
            "label": calendar.month_abbr[month],
            "amount": round(totals.get(f"{year:04d}-{month:02d}", 0) or 0, 2),
        }
        for year, month in months
    ]


def load_fee_records(db):
    sync_fee_statuses(db)
    rows = db.execute(
        """SELECT f.*, s.student_id AS student_code, s.name,
                  r.room_number
           FROM fee_records f
           JOIN students s ON s.id = f.student_id
           LEFT JOIN allocations a ON a.student_id = s.id
           LEFT JOIN rooms r ON r.id = a.room_id
           ORDER BY f.due_date, s.name, f.period DESC"""
    ).fetchall()
    return [fee_record_dict(row) for row in rows]


def payment_dict(row):
    return dict(row)


def parse_attendance_date(value, *, allow_future=False):
    attendance_day = parse_iso_date(value, "attendance date")
    if not allow_future and attendance_day > date.today():
        raise ValueError("Attendance cannot be marked for a future date.")
    return attendance_day


def attendance_roster(db, attendance_day, search="", room_filter=""):
    query = """SELECT s.id, s.student_id, s.name, s.department, s.year,
                      r.room_number, a.status, a.notes
               FROM students s
               LEFT JOIN allocations al ON al.student_id = s.id
               LEFT JOIN rooms r ON r.id = al.room_id
               LEFT JOIN attendance a
                 ON a.student_id = s.id AND a.attendance_date = ?
               WHERE (s.student_id LIKE ? OR s.name LIKE ?)"""
    params = [attendance_day, f"%{search}%", f"%{search}%"]
    if room_filter == "__unassigned__":
        query += " AND r.room_number IS NULL"
    elif room_filter:
        query += " AND r.room_number = ?"
        params.append(room_filter)
    query += " ORDER BY s.name COLLATE NOCASE"
    return db.execute(query, params).fetchall()


def save_attendance(db, attendance_day, records, marked_by):
    """Validate a whole marking batch before atomically inserting or updating it."""
    if not isinstance(records, list) or not records:
        raise ValueError("Select at least one student to mark attendance.")
    seen_students = set()
    validated = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Attendance records must be valid student entries.")
        try:
            student_pk = int(record.get("student_id"))
        except (TypeError, ValueError):
            raise ValueError("Each attendance record must reference a valid student.") from None
        if student_pk <= 0 or student_pk in seen_students:
            raise ValueError("Attendance contains an invalid or duplicate student.")
        seen_students.add(student_pk)
        status = record.get("status")
        if status not in ATTENDANCE_STATUSES:
            raise ValueError("Choose Present, Absent, Late, or Leave for every selected student.")
        notes = record.get("notes") or ""
        if not isinstance(notes, str) or len(notes) > 300:
            raise ValueError("Attendance notes must be 300 characters or fewer.")
        notes = notes.strip()
        validated.append((student_pk, status, notes))

    with db:
        db.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" for _ in seen_students)
        found = db.execute(
            f"SELECT id FROM students WHERE id IN ({placeholders})", tuple(seen_students)
        ).fetchall()
        if len(found) != len(seen_students):
            raise ValueError("One or more selected students no longer exist.")
        db.executemany(
            """INSERT INTO attendance
               (student_id, attendance_date, status, notes, marked_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(student_id, attendance_date) DO UPDATE SET
                   status = excluded.status,
                   notes = excluded.notes,
                   marked_by = excluded.marked_by,
                   marked_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP""",
            [
                (student_pk, attendance_day, status, notes, marked_by)
                for student_pk, status, notes in validated
            ],
        )
    return len(validated)


def attendance_report_rows(db, date_from, date_to, search="", room_filter="", status_filter=""):
    query = """SELECT s.id, s.student_id, s.name, s.department, r.room_number,
                      COUNT(a.id) AS total_days,
                      SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END) AS present_days,
                      SUM(CASE WHEN a.status = 'Absent' THEN 1 ELSE 0 END) AS absent_days,
                      SUM(CASE WHEN a.status = 'Late' THEN 1 ELSE 0 END) AS late_days,
                      SUM(CASE WHEN a.status = 'Leave' THEN 1 ELSE 0 END) AS leave_days
               FROM students s
               LEFT JOIN allocations al ON al.student_id = s.id
               LEFT JOIN rooms r ON r.id = al.room_id
               LEFT JOIN attendance a ON a.student_id = s.id
                    AND a.attendance_date BETWEEN ? AND ?
               WHERE (s.student_id LIKE ? OR s.name LIKE ?)"""
    params = [date_from, date_to, f"%{search}%", f"%{search}%"]
    if room_filter == "__unassigned__":
        query += " AND r.room_number IS NULL"
    elif room_filter:
        query += " AND r.room_number = ?"
        params.append(room_filter)
    query += " GROUP BY s.id"
    if status_filter:
        query += (
            " HAVING SUM(CASE WHEN a.status = ? THEN 1 ELSE 0 END) > 0"
        )
        params.append(status_filter)
    query += " ORDER BY s.name COLLATE NOCASE"

    rows = []
    for raw_row in db.execute(query, params).fetchall():
        row = dict(raw_row)
        row["total_days"] = row["total_days"] or 0
        for status in ("present", "absent", "late", "leave"):
            row[f"{status}_days"] = row[f"{status}_days"] or 0
        row["working_days"] = row["present_days"] + row["absent_days"] + row["late_days"]
        denominator = row["working_days"]
        row["attendance_rate"] = round(
            row["present_days"] * 100 / denominator
        ) if denominator else 0
        rows.append(row)
    return rows


def login_required(role=None):
    """Restrict a view to signed-in users, optionally of one role."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))
            if role and session.get("role") != role:
                flash("You do not have permission to view that page.", "error")
                return redirect(url_for("index"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


def validate_student_form(form):
    fields = ("student_id", "name", "department", "year", "phone", "email", "gender")
    values = {field: form.get(field, "").strip() for field in fields}
    if any(not values[field] for field in fields):
        return values, "Please complete all student fields."
    try:
        values["year"] = int(values["year"])
    except ValueError:
        return values, "Year must be a whole number between 1 and 6."
    if not 1 <= values["year"] <= 6:
        return values, "Year must be between 1 and 6."
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", values["email"]):
        return values, "Enter a valid email address."
    phone_digits = re.sub(r"\D", "", values["phone"])
    if not re.fullmatch(r"[0-9+\-\s()]{7,20}", values["phone"]) or not 7 <= len(phone_digits) <= 15:
        return values, "Enter a valid phone number containing 7 to 15 digits."
    if values["gender"] not in GENDERS:
        return values, "Choose a valid gender."
    return values, None


def validate_room_form(form):
    values = {
        "room_number": form.get("room_number", "").strip(),
        "block": form.get("block", "").strip(),
        "floor": form.get("floor", "").strip(),
        "capacity": form.get("capacity", "").strip(),
    }
    if any(not values[field] for field in values):
        return values, "Please complete all room fields."
    try:
        values["floor"] = int(values["floor"])
        values["capacity"] = int(values["capacity"])
    except ValueError:
        return values, "Floor and capacity must be whole numbers."
    if values["floor"] < 0 or values["capacity"] < 1:
        return values, "Floor cannot be negative and capacity must be at least 1."
    return values, None


def available_rooms_for_student(db, student_pk):
    rows = db.execute(
        """SELECT r.id, r.room_number, r.block, r.floor, r.capacity, r.occupied_count,
                  r.capacity - r.occupied_count AS available_beds
           FROM rooms r
           WHERE r.occupied_count < r.capacity
             AND r.id != COALESCE(
                 (SELECT room_id FROM allocations WHERE student_id = ?), -1
             )
           ORDER BY r.block, r.floor, r.room_number""",
        (student_pk,),
    ).fetchall()
    return [
        {
            **dict(row),
            "room_type": (
                "Single occupancy" if row["capacity"] == 1
                else f"{row['capacity']} sharing"
            ),
        }
        for row in rows
    ]


def create_room_change_request(db, student_pk, form_data):
    reason_value = form_data.get("reason")
    description_value = form_data.get("description")
    if not isinstance(reason_value, str) or not isinstance(description_value, str):
        raise ValueError("Choose a reason and enter a detailed description.")
    reason = reason_value.strip()
    description = description_value.strip()
    try:
        requested_room_value = form_data.get("requested_room_id")
        if isinstance(requested_room_value, bool):
            raise ValueError
        requested_room_pk = int(requested_room_value)
    except (TypeError, ValueError):
        raise ValueError("Select an available room.") from None
    if reason not in ROOM_CHANGE_REASONS:
        raise ValueError("Choose a valid reason for your room change.")
    if not description:
        raise ValueError("Enter a detailed description for your request.")
    if len(description) > 1000:
        raise ValueError("The detailed description must be 1,000 characters or fewer.")

    db.execute("BEGIN IMMEDIATE")
    student = db.execute(
        "SELECT id FROM students WHERE id = ?", (student_pk,)
    ).fetchone()
    if not student:
        raise ValueError("Your student account could not be found.")
    if db.execute(
        "SELECT 1 FROM room_change_requests WHERE student_id = ? AND status = 'PENDING'",
        (student_pk,),
    ).fetchone():
        raise ValueError("You already have a pending room change request.")
    current = db.execute(
        "SELECT room_id FROM allocations WHERE student_id = ?", (student_pk,)
    ).fetchone()
    if not current:
        raise ValueError("You need an assigned room before requesting a room change.")
    if current["room_id"] == requested_room_pk:
        raise ValueError("Choose a room other than your current room.")
    target = db.execute(
        "SELECT id, occupied_count, capacity FROM rooms WHERE id = ?",
        (requested_room_pk,),
    ).fetchone()
    if not target:
        raise ValueError("The selected room no longer exists.")
    if target["occupied_count"] >= target["capacity"]:
        raise ValueError("The selected room is no longer available. Choose another room.")
    cursor = db.execute(
        """INSERT INTO room_change_requests
           (student_id, current_room_id, requested_room_id, reason, description)
           VALUES (?, ?, ?, ?, ?)""",
        (student_pk, current["room_id"], requested_room_pk, reason, description),
    )
    return cursor.lastrowid


def room_change_request_query(db, search="", status="", current_room="", requested_room="", requested_date=""):
    clauses = []
    params = []
    if search:
        clauses.append("(s.name LIKE ? OR s.student_id LIKE ?)")
        params.extend((f"%{search}%", f"%{search}%"))
    if status in ("PENDING", "APPROVED", "REJECTED"):
        clauses.append("rc.status = ?")
        params.append(status)
    if current_room:
        clauses.append("current_room.room_number = ?")
        params.append(current_room)
    if requested_room:
        clauses.append("requested_room.room_number = ?")
        params.append(requested_room)
    if requested_date:
        clauses.append("date(rc.requested_at) = ?")
        params.append(requested_date)
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return db.execute(
        f"""SELECT rc.*, s.name, s.student_id AS student_code, s.department, s.year,
                   current_room.room_number AS current_room_number,
                   current_room.block AS current_block,
                   requested_room.room_number AS requested_room_number,
                   requested_room.block AS requested_block,
                   requested_room.floor AS requested_floor,
                   requested_room.capacity AS requested_capacity,
                   requested_room.occupied_count AS requested_occupied,
                   requested_room.capacity - requested_room.occupied_count AS requested_available
            FROM room_change_requests rc
            JOIN students s ON s.id = rc.student_id
            LEFT JOIN rooms current_room ON current_room.id = rc.current_room_id
            LEFT JOIN rooms requested_room ON requested_room.id = rc.requested_room_id
            {where_clause}
            ORDER BY CASE rc.status WHEN 'PENDING' THEN 0 ELSE 1 END,
                     rc.requested_at DESC, rc.id DESC""",
        params,
    ).fetchall()


def approve_room_change_request(db, request_pk, admin_user_pk, admin_comment=""):
    if not isinstance(admin_comment, str):
        raise ValueError("Approval comment must be text.")
    if len(admin_comment) > 1000:
        raise ValueError("The approval comment must be 1,000 characters or fewer.")
    db.execute("BEGIN IMMEDIATE")
    change_request = db.execute(
        "SELECT * FROM room_change_requests WHERE id = ?", (request_pk,)
    ).fetchone()
    if not change_request:
        raise LookupError("Room change request not found.")
    if change_request["status"] != "PENDING":
        raise ValueError("Only pending room change requests can be approved.")
    student = db.execute(
        "SELECT id FROM students WHERE id = ?", (change_request["student_id"],)
    ).fetchone()
    current = db.execute(
        "SELECT id, room_id FROM allocations WHERE student_id = ?",
        (change_request["student_id"],),
    ).fetchone()
    if not student or not current or current["room_id"] != change_request["current_room_id"]:
        raise ValueError("The student's current room has changed. Review the request again.")
    if current["room_id"] == change_request["requested_room_id"]:
        raise ValueError("The student is already assigned to the requested room.")
    target = db.execute(
        "SELECT id, occupied_count, capacity FROM rooms WHERE id = ?",
        (change_request["requested_room_id"],),
    ).fetchone()
    if not target:
        raise ValueError("The requested room no longer exists.")
    if target["occupied_count"] >= target["capacity"]:
        raise ValueError("Cannot approve this request because the selected room is no longer available.")

    updated_target = db.execute(
        """UPDATE rooms SET occupied_count = occupied_count + 1
           WHERE id = ? AND occupied_count < capacity""",
        (target["id"],),
    )
    if updated_target.rowcount != 1:
        raise ValueError("Cannot approve this request because the selected room is no longer available.")
    updated_current = db.execute(
        """UPDATE rooms SET occupied_count = occupied_count - 1
           WHERE id = ? AND occupied_count > 0""",
        (current["room_id"],),
    )
    if updated_current.rowcount != 1:
        raise ValueError("The current room occupancy is inconsistent. Ask an administrator to review it.")
    db.execute(
        "UPDATE allocations SET room_id = ?, allocated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (target["id"], current["id"]),
    )
    db.execute(
        """UPDATE room_change_requests
           SET status = 'APPROVED', admin_comment = ?, admin_user_id = ?,
               reviewed_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'PENDING'""",
        (admin_comment.strip() or None, admin_user_pk, request_pk),
    )


def reject_room_change_request(db, request_pk, admin_user_pk, admin_comment):
    if not isinstance(admin_comment, str):
        raise ValueError("Enter a valid rejection reason.")
    admin_comment = (admin_comment or "").strip()
    if not admin_comment:
        raise ValueError("Enter a rejection reason before rejecting the request.")
    if len(admin_comment) > 1000:
        raise ValueError("The rejection reason must be 1,000 characters or fewer.")
    db.execute("BEGIN IMMEDIATE")
    change_request = db.execute(
        "SELECT status FROM room_change_requests WHERE id = ?", (request_pk,)
    ).fetchone()
    if not change_request:
        raise LookupError("Room change request not found.")
    if change_request["status"] != "PENDING":
        raise ValueError("Only pending room change requests can be rejected.")
    db.execute(
        """UPDATE room_change_requests
           SET status = 'REJECTED', admin_comment = ?, admin_user_id = ?,
               reviewed_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'PENDING'""",
        (admin_comment, admin_user_pk, request_pk),
    )


@app.route("/")
def index():
    if session.get("role") == "admin":
        return redirect(url_for("admin_dashboard"))
    if session.get("role") == "student":
        return redirect(url_for("student_dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    selected_role = request.form.get("role", "student")
    if selected_role not in ("admin", "student"):
        selected_role = "student"
    username_value = request.form.get("username", "").strip()
    if request.method == "POST":
        password = request.form.get("password", "")
        with get_db() as db:
            user = db.execute("SELECT * FROM users WHERE username = ?", (username_value,)).fetchone()
            role_matches = user and user["role"] == selected_role
            if role_matches and check_password_hash(user["password_hash"], password):
                session.clear()
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                session["role"] = user["role"]
                session["student_id"] = user["student_id"]
                flash("Welcome back!", "success")
                return redirect(url_for("index"))
        flash("Incorrect credentials for the selected account type.", "error")
    return render_template(
        "login.html", selected_role=selected_role, username=username_value
    )


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/admin")
@login_required("admin")
def admin_dashboard():
    with get_db() as db:
        stats = {
            "students": db.execute("SELECT COUNT(*) FROM students").fetchone()[0],
            "rooms": db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0],
            "occupied": db.execute("SELECT COUNT(*) FROM rooms WHERE occupied_count > 0").fetchone()[0],
            "available": db.execute("SELECT COUNT(*) FROM rooms WHERE occupied_count < capacity").fetchone()[0],
            "complaints": db.execute("SELECT COUNT(*) FROM complaints WHERE status = 'Pending'").fetchone()[0],
        }
        fee_stats = current_fee_summary(db)
        attendance_count = db.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        today_attendance = db.execute(
            "SELECT COUNT(*) FROM attendance WHERE attendance_date = ?",
            (date.today().isoformat(),),
        ).fetchone()[0]
        today_status = {
            row["status"]: row["count"]
            for row in db.execute(
                """SELECT status, COUNT(*) AS count FROM attendance
                   WHERE attendance_date = ? GROUP BY status""",
                (date.today().isoformat(),),
            ).fetchall()
        }
        attendance_totals = db.execute(
            """SELECT
                   SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END) AS present,
                   SUM(CASE WHEN status = 'Absent' THEN 1 ELSE 0 END) AS absent,
                   SUM(CASE WHEN status = 'Late' THEN 1 ELSE 0 END) AS late
               FROM attendance WHERE attendance_date >= ? AND attendance_date <= ?""",
            (six_months_before(date.today()).isoformat(), date.today().isoformat()),
        ).fetchone()
        attendance_workdays = sum(
            attendance_totals[key] or 0 for key in ("present", "absent", "late")
        )
        overall_attendance = round(
            (attendance_totals["present"] or 0) * 100 / attendance_workdays
        ) if attendance_workdays else 0
        room_change_status_counts = {
            row["status"]: row["total"]
            for row in db.execute(
                """SELECT status, COUNT(*) AS total
                   FROM room_change_requests GROUP BY status"""
            ).fetchall()
        }
        room_change_stats = {
            "total": sum(room_change_status_counts.values()),
            "pending": room_change_status_counts.get("PENDING", 0),
            "approved": room_change_status_counts.get("APPROVED", 0),
            "rejected": room_change_status_counts.get("REJECTED", 0),
        }
        recent_complaints = db.execute(
            """SELECT c.*, COALESCE(s.name, 'Former student') AS student_name
               FROM complaints c LEFT JOIN students s ON c.student_id = s.id
               ORDER BY c.created_at DESC, c.id DESC LIMIT 5"""
        ).fetchall()
    return render_template(
        "admin_dashboard.html",
        stats=stats,
        complaints=recent_complaints,
        fee_stats=fee_stats,
        attendance_count=attendance_count,
        today_attendance=today_attendance,
        today_status=today_status,
        overall_attendance=overall_attendance,
        room_change_stats=room_change_stats,
    )


@app.route("/students")
@login_required("admin")
def students():
    search = request.args.get("q", "").strip()
    with get_db() as db:
        rows = db.execute(
            """SELECT s.*, r.room_number FROM students s
               LEFT JOIN allocations a ON a.student_id = s.id
               LEFT JOIN rooms r ON r.id = a.room_id
               WHERE s.student_id LIKE ? OR s.name LIKE ? OR s.department LIKE ? OR s.email LIKE ?
               ORDER BY s.name""",
            tuple(f"%{search}%" for _ in range(4)),
        ).fetchall()
    return render_template("students.html", students=rows, search=search)


@app.route("/students/add", methods=["GET", "POST"])
@login_required("admin")
def add_student():
    if request.method == "POST":
        values, error = validate_student_form(request.form)
        if error:
            flash(error, "error")
            return render_template("add_student.html", values=values, genders=GENDERS)
        try:
            with get_db() as db:
                cursor = db.execute(
                    """INSERT INTO students (student_id, name, department, year, phone, email, gender)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        values["student_id"], values["name"], values["department"], values["year"],
                        values["phone"], values["email"], values["gender"],
                    ),
                )
                db.execute(
                    "INSERT INTO users (username, password_hash, role, student_id) VALUES (?, ?, 'student', ?)",
                    (values["student_id"], generate_password_hash("student123"), cursor.lastrowid),
                )
            flash("Student added. Their username is the Student ID and the default password is student123.", "success")
            return redirect(url_for("students"))
        except sqlite3.IntegrityError:
            flash("That Student ID or email is already in use.", "error")
    else:
        values = {}
    return render_template("add_student.html", values=values, genders=GENDERS)


@app.route("/students/<int:student_id>/edit", methods=["GET", "POST"])
@login_required("admin")
def edit_student(student_id):
    with get_db() as db:
        student = db.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    if not student:
        flash("Student not found.", "error")
        return redirect(url_for("students"))
    student = dict(student)
    if request.method == "POST":
        values, error = validate_student_form(request.form)
        if error:
            flash(error, "error")
            return render_template("edit_student.html", student=values, genders=GENDERS)
        try:
            with get_db() as db:
                db.execute(
                    """UPDATE students SET student_id = ?, name = ?, department = ?, year = ?,
                       phone = ?, email = ?, gender = ? WHERE id = ?""",
                    (
                        values["student_id"], values["name"], values["department"], values["year"],
                        values["phone"], values["email"], values["gender"], student_id,
                    ),
                )
                db.execute(
                    "UPDATE users SET username = ? WHERE student_id = ?",
                    (values["student_id"], student_id),
                )
            flash("Student details updated.", "success")
            return redirect(url_for("students"))
        except sqlite3.IntegrityError:
            flash("That Student ID or email is already in use.", "error")
            return render_template("edit_student.html", student=values, genders=GENDERS)
    return render_template("edit_student.html", student=student, genders=GENDERS)


@app.route("/students/<int:student_id>/delete", methods=["POST"])
@login_required("admin")
def delete_student(student_id):
    with get_db() as db:
        student = db.execute("SELECT id FROM students WHERE id = ?", (student_id,)).fetchone()
        if not student:
            flash("Student not found.", "error")
        else:
            allocation = db.execute("SELECT room_id FROM allocations WHERE student_id = ?", (student_id,)).fetchone()
            if allocation:
                db.execute(
                    "UPDATE rooms SET occupied_count = occupied_count - 1 WHERE id = ?",
                    (allocation["room_id"],),
                )
            db.execute("DELETE FROM students WHERE id = ?", (student_id,))
            flash("Student deleted.", "success")
    return redirect(url_for("students"))


@app.route("/rooms")
@login_required("admin")
def rooms():
    with get_db() as db:
        rows = db.execute(
            """SELECT r.*, r.capacity - r.occupied_count AS available_beds
               FROM rooms r ORDER BY r.block, r.floor, r.room_number"""
        ).fetchall()
    return render_template("rooms.html", rooms=rows)


@app.route("/rooms/add", methods=["GET", "POST"])
@login_required("admin")
def add_room():
    if request.method == "POST":
        values, error = validate_room_form(request.form)
        if error:
            flash(error, "error")
            return render_template("add_room.html", values=values)
        try:
            with get_db() as db:
                db.execute(
                    "INSERT INTO rooms (room_number, block, floor, capacity) VALUES (?, ?, ?, ?)",
                    (values["room_number"], values["block"], values["floor"], values["capacity"]),
                )
            flash("Room added.", "success")
            return redirect(url_for("rooms"))
        except sqlite3.IntegrityError:
            flash("That room number already exists.", "error")
    else:
        values = {}
    return render_template("add_room.html", values=values)


@app.route("/rooms/<int:room_id>/edit", methods=["GET", "POST"])
@login_required("admin")
def edit_room(room_id):
    with get_db() as db:
        room = db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
    if not room:
        flash("Room not found.", "error")
        return redirect(url_for("rooms"))
    room = dict(room)
    if request.method == "POST":
        values, error = validate_room_form(request.form)
        if not error and values["capacity"] < room["occupied_count"]:
            error = f"Capacity cannot be less than the {room['occupied_count']} current occupant(s)."
        if error:
            flash(error, "error")
            return render_template("edit_room.html", room=values, occupied_count=room["occupied_count"])
        try:
            with get_db() as db:
                db.execute(
                    "UPDATE rooms SET room_number = ?, block = ?, floor = ?, capacity = ? WHERE id = ?",
                    (values["room_number"], values["block"], values["floor"], values["capacity"], room_id),
                )
            flash("Room details updated.", "success")
            return redirect(url_for("rooms"))
        except sqlite3.IntegrityError:
            flash("That room number already exists or the capacity is invalid.", "error")
            return render_template("edit_room.html", room=values, occupied_count=room["occupied_count"])
    return render_template("edit_room.html", room=room, occupied_count=room["occupied_count"])


@app.route("/rooms/<int:room_id>/delete", methods=["POST"])
@login_required("admin")
def delete_room(room_id):
    with get_db() as db:
        room = db.execute("SELECT room_number FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if not room:
            flash("Room not found.", "error")
        elif db.execute("SELECT 1 FROM allocations WHERE room_id = ?", (room_id,)).fetchone():
            flash("Remove all student allocations before deleting this room.", "error")
        elif db.execute(
            """SELECT 1 FROM room_change_requests
               WHERE current_room_id = ? OR requested_room_id = ? LIMIT 1""",
            (room_id, room_id),
        ).fetchone():
            flash("This room is referenced by room change request history and cannot be deleted.", "error")
        else:
            db.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
            flash("Room deleted.", "success")
    return redirect(url_for("rooms"))


@app.route("/allocation", methods=["GET", "POST"])
@login_required("admin")
def allocation():
    if request.method == "POST":
        student_id = request.form.get("student_id", type=int)
        room_id = request.form.get("room_id", type=int)
        if not student_id or not room_id:
            flash("Select both a student and a room.", "error")
            return redirect(url_for("allocation"))
        try:
            with get_db() as db:
                db.execute("BEGIN IMMEDIATE")
                student = db.execute("SELECT id FROM students WHERE id = ?", (student_id,)).fetchone()
                target_room = db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
                if not student or not target_room:
                    raise ValueError("The selected student or room no longer exists.")
                current = db.execute(
                    "SELECT id, room_id FROM allocations WHERE student_id = ?", (student_id,)
                ).fetchone()
                if current and current["room_id"] == room_id:
                    raise ValueError("This student is already allocated to that room.")
                if target_room["occupied_count"] >= target_room["capacity"]:
                    raise ValueError("That room is full. Choose a room with an available bed.")
                if current:
                    db.execute(
                        "UPDATE rooms SET occupied_count = occupied_count - 1 WHERE id = ?",
                        (current["room_id"],),
                    )
                    db.execute(
                        "UPDATE allocations SET room_id = ?, allocated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (room_id, current["id"]),
                    )
                else:
                    db.execute("INSERT INTO allocations (student_id, room_id) VALUES (?, ?)", (student_id, room_id))
                db.execute("UPDATE rooms SET occupied_count = occupied_count + 1 WHERE id = ?", (room_id,))
            flash("Room allocation saved.", "success")
        except ValueError as error:
            flash(str(error), "error")
        except sqlite3.IntegrityError:
            flash("The student already has a room allocation.", "error")
        return redirect(url_for("allocation"))

    with get_db() as db:
        students_rows = db.execute(
            """SELECT s.id, s.student_id, s.name, r.room_number
               FROM students s LEFT JOIN allocations a ON a.student_id = s.id
               LEFT JOIN rooms r ON r.id = a.room_id ORDER BY s.name"""
        ).fetchall()
        room_rows = db.execute(
            """SELECT id, room_number, block, floor, capacity, occupied_count,
                      capacity - occupied_count AS available_beds
               FROM rooms ORDER BY room_number"""
        ).fetchall()
        allocations = db.execute(
            """SELECT a.id, a.allocated_at, s.id AS student_pk, s.student_id, s.name,
                      r.room_number, r.block, r.floor
               FROM allocations a JOIN students s ON s.id = a.student_id
               JOIN rooms r ON r.id = a.room_id ORDER BY r.room_number, s.name"""
        ).fetchall()
    return render_template(
        "allocation.html", students=students_rows, rooms=room_rows, allocations=allocations
    )


@app.route("/allocation/<int:allocation_id>/remove", methods=["POST"])
@login_required("admin")
def remove_allocation(allocation_id):
    with get_db() as db:
        allocation_row = db.execute(
            "SELECT student_id, room_id FROM allocations WHERE id = ?", (allocation_id,)
        ).fetchone()
        if not allocation_row:
            flash("Allocation not found.", "error")
        else:
            db.execute("DELETE FROM allocations WHERE id = ?", (allocation_id,))
            db.execute(
                "UPDATE rooms SET occupied_count = occupied_count - 1 WHERE id = ?",
                (allocation_row["room_id"],),
            )
            flash("Room allocation removed.", "success")
    return redirect(url_for("allocation"))


@app.route("/student/room-change-requests", methods=["GET", "POST"])
@login_required("student")
def student_room_change_requests():
    student_pk = session["student_id"]
    if request.method == "POST":
        try:
            with get_db() as db:
                create_room_change_request(db, student_pk, request.form)
            flash("Room change request submitted successfully. Waiting for admin approval.", "success")
            return redirect(url_for("student_room_change_requests"))
        except ValueError as error:
            flash(str(error), "error")
        except sqlite3.IntegrityError:
            flash("The request could not be saved because its student or room is no longer valid.", "error")

    with get_db() as db:
        student = db.execute(
            "SELECT id, student_id, name, department, year FROM students WHERE id = ?",
            (student_pk,),
        ).fetchone()
        current_room = db.execute(
            """SELECT r.* FROM allocations a JOIN rooms r ON r.id = a.room_id
               WHERE a.student_id = ?""",
            (student_pk,),
        ).fetchone()
        available_rooms = available_rooms_for_student(db, student_pk)
        requests_for_student = db.execute(
            """SELECT rc.*, current_room.room_number AS current_room_number,
                      requested_room.room_number AS requested_room_number
               FROM room_change_requests rc
               LEFT JOIN rooms current_room ON current_room.id = rc.current_room_id
               LEFT JOIN rooms requested_room ON requested_room.id = rc.requested_room_id
               WHERE rc.student_id = ? ORDER BY rc.requested_at DESC, rc.id DESC""",
            (student_pk,),
        ).fetchall()
        has_pending_request = any(row["status"] == "PENDING" for row in requests_for_student)
    return render_template(
        "student_room_change.html",
        student=student,
        current_room=current_room,
        available_rooms=available_rooms,
        requests=requests_for_student,
        has_pending_request=has_pending_request,
        reasons=ROOM_CHANGE_REASONS,
    )


@app.route("/room-change-requests")
@login_required("admin")
def admin_room_change_requests():
    search = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").upper()
    if status_filter not in ("", "PENDING", "APPROVED", "REJECTED"):
        status_filter = ""
    current_room_filter = request.args.get("current_room", "").strip()
    requested_room_filter = request.args.get("requested_room", "").strip()
    requested_date = request.args.get("date", "").strip()
    if requested_date:
        try:
            requested_date = parse_iso_date(requested_date, "request date").isoformat()
        except ValueError as error:
            flash(str(error), "error")
            requested_date = ""

    with get_db() as db:
        rows = room_change_request_query(
            db, search, status_filter, current_room_filter,
            requested_room_filter, requested_date,
        )
        counts = {
            row["status"]: row["total"]
            for row in db.execute(
                """SELECT status, COUNT(*) AS total
                   FROM room_change_requests GROUP BY status"""
            ).fetchall()
        }
        summary = {
            "total": sum(counts.values()),
            "pending": counts.get("PENDING", 0),
            "approved": counts.get("APPROVED", 0),
            "rejected": counts.get("REJECTED", 0),
        }
        rooms = db.execute(
            "SELECT room_number FROM rooms ORDER BY room_number"
        ).fetchall()
        requested_rooms = db.execute(
            """SELECT r.room_number, COUNT(*) AS request_count
               FROM room_change_requests rc JOIN rooms r ON r.id = rc.requested_room_id
               GROUP BY rc.requested_room_id ORDER BY request_count DESC, r.room_number LIMIT 5"""
        ).fetchall()
        reasons = db.execute(
            """SELECT reason, COUNT(*) AS request_count FROM room_change_requests
               GROUP BY reason ORDER BY request_count DESC, reason"""
        ).fetchall()
    return render_template(
        "admin_room_change_requests.html",
        requests=rows,
        summary=summary,
        rooms=rooms,
        requested_rooms=requested_rooms,
        reasons=reasons,
        search=search,
        status_filter=status_filter,
        current_room_filter=current_room_filter,
        requested_room_filter=requested_room_filter,
        requested_date=requested_date,
    )


@app.route("/room-change-requests/<int:request_id>")
@login_required("admin")
def admin_room_change_request_detail(request_id):
    with get_db() as db:
        change_request = db.execute(
            """SELECT rc.*, s.name, s.student_id AS student_code, s.department, s.year,
                      current_room.room_number AS current_room_number,
                      current_room.block AS current_block, current_room.floor AS current_floor,
                      current_room.capacity AS current_capacity,
                      current_room.occupied_count AS current_occupied,
                      requested_room.room_number AS requested_room_number,
                      requested_room.block AS requested_block,
                      requested_room.floor AS requested_floor,
                      requested_room.capacity AS requested_capacity,
                      requested_room.occupied_count AS requested_occupied,
                      requested_room.capacity - requested_room.occupied_count AS requested_available
               FROM room_change_requests rc
               JOIN students s ON s.id = rc.student_id
               LEFT JOIN rooms current_room ON current_room.id = rc.current_room_id
               LEFT JOIN rooms requested_room ON requested_room.id = rc.requested_room_id
               WHERE rc.id = ?""",
            (request_id,),
        ).fetchone()
    if not change_request:
        flash("Room change request not found.", "error")
        return redirect(url_for("admin_room_change_requests"))
    return render_template(
        "admin_room_change_request_detail.html", change_request=change_request
    )


@app.route("/api/rooms/available")
@login_required("student")
def available_rooms_api():
    with get_db() as db:
        available_rooms = available_rooms_for_student(db, session["student_id"])
    return jsonify({"rooms": available_rooms})


@app.route("/api/room-change-requests", methods=["POST"])
@login_required("student")
def create_room_change_request_api():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Send a JSON object with requested_room_id, reason, and description."}), 400
    try:
        with get_db() as db:
            request_id = create_room_change_request(db, session["student_id"], payload)
    except ValueError as error:
        status_code = 409 if "pending room change request" in str(error).lower() else 400
        return jsonify({"error": str(error)}), status_code
    except sqlite3.IntegrityError:
        return jsonify({"error": "The request could not be saved because its student or room is no longer valid."}), 409
    return jsonify(
        {
            "id": request_id,
            "status": "PENDING",
            "message": "Room change request submitted successfully. Waiting for admin approval.",
        }
    ), 201


@app.route("/api/room-change-requests/my")
@login_required("student")
def my_room_change_requests_api():
    with get_db() as db:
        rows = db.execute(
            """SELECT rc.*, current_room.room_number AS current_room_number,
                      requested_room.room_number AS requested_room_number
               FROM room_change_requests rc
               LEFT JOIN rooms current_room ON current_room.id = rc.current_room_id
               LEFT JOIN rooms requested_room ON requested_room.id = rc.requested_room_id
               WHERE rc.student_id = ? ORDER BY rc.requested_at DESC, rc.id DESC""",
            (session["student_id"],),
        ).fetchall()
    return jsonify({"requests": [dict(row) for row in rows]})


@app.route("/api/admin/room-change-requests")
@login_required("admin")
def admin_room_change_requests_api():
    search = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").upper()
    if status_filter not in ("", "PENDING", "APPROVED", "REJECTED"):
        status_filter = ""
    requested_date = request.args.get("date", "").strip()
    if requested_date:
        try:
            requested_date = parse_iso_date(requested_date, "request date").isoformat()
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
    with get_db() as db:
        rows = room_change_request_query(
            db,
            search,
            status_filter,
            request.args.get("current_room", "").strip(),
            request.args.get("requested_room", "").strip(),
            requested_date,
        )
    return jsonify({"requests": [dict(row) for row in rows]})


@app.route("/api/admin/room-change-requests/<int:request_id>")
@login_required("admin")
def admin_room_change_request_api(request_id):
    with get_db() as db:
        row = db.execute(
            """SELECT rc.*, s.name, s.student_id AS student_code, s.department, s.year,
                      current_room.room_number AS current_room_number,
                      current_room.block AS current_block,
                      current_room.floor AS current_floor,
                      current_room.capacity AS current_capacity,
                      current_room.occupied_count AS current_occupied,
                      requested_room.room_number AS requested_room_number,
                      requested_room.block AS requested_block,
                      requested_room.floor AS requested_floor,
                      requested_room.capacity AS requested_capacity,
                      requested_room.occupied_count AS requested_occupied,
                      requested_room.capacity - requested_room.occupied_count AS requested_available
               FROM room_change_requests rc JOIN students s ON s.id = rc.student_id
               LEFT JOIN rooms current_room ON current_room.id = rc.current_room_id
               LEFT JOIN rooms requested_room ON requested_room.id = rc.requested_room_id
               WHERE rc.id = ?""",
            (request_id,),
        ).fetchone()
    if not row:
        return jsonify({"error": "Room change request not found."}), 404
    return jsonify({"request": dict(row)})


@app.route(
    "/api/admin/room-change-requests/<int:request_id>/approve",
    methods=["POST", "PATCH"],
)
@login_required("admin")
def approve_room_change_request_api(request_id):
    payload = request.get_json(silent=True) if request.is_json else request.form
    if request.is_json and not isinstance(payload, dict):
        return jsonify({"error": "Send a JSON object containing an optional admin_comment."}), 400
    if payload is None:
        payload = {}
    admin_comment_value = payload.get("admin_comment") or ""
    if not isinstance(admin_comment_value, str):
        return jsonify({"error": "Approval comment must be text."}), 400
    admin_comment = admin_comment_value.strip()
    try:
        with get_db() as db:
            approve_room_change_request(
                db, request_id, session["user_id"], admin_comment
            )
    except LookupError as error:
        if request.is_json:
            return jsonify({"error": str(error)}), 404
        flash(str(error), "error")
        return redirect(url_for("admin_room_change_requests"))
    except ValueError as error:
        if request.is_json:
            return jsonify({"error": str(error)}), 409
        flash(str(error), "error")
        return redirect(url_for("admin_room_change_request_detail", request_id=request_id))
    except sqlite3.IntegrityError:
        message = "Room assignment could not be updated because a database constraint was violated."
        if request.is_json:
            return jsonify({"error": message}), 409
        flash(message, "error")
        return redirect(url_for("admin_room_change_request_detail", request_id=request_id))
    if request.is_json:
        return jsonify({"status": "APPROVED", "message": "Room change request approved."})
    flash("Room change request approved and the student's room allocation was updated.", "success")
    return redirect(url_for("admin_room_change_request_detail", request_id=request_id))


@app.route(
    "/api/admin/room-change-requests/<int:request_id>/reject",
    methods=["POST", "PATCH"],
)
@login_required("admin")
def reject_room_change_request_api(request_id):
    payload = request.get_json(silent=True) if request.is_json else request.form
    if request.is_json and not isinstance(payload, dict):
        return jsonify({"error": "Send a JSON object containing the required rejection reason."}), 400
    if payload is None:
        payload = {}
    admin_comment = payload.get("admin_comment") or payload.get("rejection_reason") or ""
    try:
        with get_db() as db:
            reject_room_change_request(
                db, request_id, session["user_id"], admin_comment
            )
    except LookupError as error:
        if request.is_json:
            return jsonify({"error": str(error)}), 404
        flash(str(error), "error")
        return redirect(url_for("admin_room_change_requests"))
    except ValueError as error:
        if request.is_json:
            status_code = 409 if "only pending" in str(error).lower() else 400
            return jsonify({"error": str(error)}), status_code
        flash(str(error), "error")
        return redirect(url_for("admin_room_change_request_detail", request_id=request_id))
    except sqlite3.IntegrityError:
        message = "The request response could not be saved because a database constraint was violated."
        if request.is_json:
            return jsonify({"error": message}), 409
        flash(message, "error")
        return redirect(url_for("admin_room_change_request_detail", request_id=request_id))
    if request.is_json:
        return jsonify({"status": "REJECTED", "message": "Room change request rejected."})
    flash("Room change request rejected. The student's room was not changed.", "success")
    return redirect(url_for("admin_room_change_request_detail", request_id=request_id))


@app.route("/complaints", methods=["GET", "POST"])
@login_required()
def complaints():
    is_admin = session["role"] == "admin"
    if request.method == "POST":
        if is_admin:
            complaint_id = request.form.get("complaint_id", type=int)
            status = request.form.get("status", "")
            if status not in COMPLAINT_STATUSES:
                flash("Choose a valid complaint status.", "error")
            else:
                with get_db() as db:
                    cursor = db.execute(
                        "UPDATE complaints SET status = ? WHERE id = ?", (status, complaint_id)
                    )
                flash("Complaint status updated." if cursor.rowcount else "Complaint not found.", "success" if cursor.rowcount else "error")
        else:
            category = request.form.get("category", "")
            description = request.form.get("description", "").strip()
            if category not in COMPLAINT_CATEGORIES or not description:
                flash("Choose a category and enter a complaint description.", "error")
            elif len(description) > 2000:
                flash("Complaint descriptions must be 2,000 characters or fewer.", "error")
            else:
                with get_db() as db:
                    allocation_row = db.execute(
                        """SELECT r.room_number FROM allocations a
                           JOIN rooms r ON r.id = a.room_id WHERE a.student_id = ?""",
                        (session["student_id"],),
                    ).fetchone()
                    db.execute(
                        """INSERT INTO complaints (student_id, room_number, category, description)
                           VALUES (?, ?, ?, ?)""",
                        (
                            session["student_id"],
                            allocation_row["room_number"] if allocation_row else None,
                            category,
                            description,
                        ),
                    )
                flash("Your complaint has been submitted.", "success")
        return redirect(url_for("complaints"))

    with get_db() as db:
        if is_admin:
            complaint_rows = db.execute(
                """SELECT c.*, COALESCE(s.name, 'Former student') AS student_name,
                          COALESCE(s.student_id, '—') AS student_code
                   FROM complaints c LEFT JOIN students s ON s.id = c.student_id
                   ORDER BY c.created_at DESC, c.id DESC"""
            ).fetchall()
        else:
            complaint_rows = db.execute(
                """SELECT c.*, s.name AS student_name, s.student_id AS student_code
                   FROM complaints c JOIN students s ON s.id = c.student_id
                   WHERE c.student_id = ? ORDER BY c.created_at DESC, c.id DESC""",
                (session["student_id"],),
            ).fetchall()
    return render_template(
        "complaints.html",
        complaints=complaint_rows,
        is_admin=is_admin,
        categories=COMPLAINT_CATEGORIES,
        statuses=COMPLAINT_STATUSES,
    )


@app.route("/student")
@login_required("student")
def student_dashboard():
    with get_db() as db:
        sync_fee_statuses(db)
        student = db.execute("SELECT * FROM students WHERE id = ?", (session["student_id"],)).fetchone()
        room = db.execute(
            """SELECT r.* FROM allocations a JOIN rooms r ON r.id = a.room_id
               WHERE a.student_id = ?""",
            (session["student_id"],),
        ).fetchone()
        roommates = []
        if room:
            roommates = db.execute(
                """SELECT s.student_id, s.name, s.department FROM allocations a
                   JOIN students s ON s.id = a.student_id
                   WHERE a.room_id = ? AND s.id != ? ORDER BY s.name""",
                (room["id"], session["student_id"]),
            ).fetchall()
        complaint_rows = db.execute(
            """SELECT * FROM complaints WHERE student_id = ?
               ORDER BY created_at DESC, id DESC LIMIT 5""",
            (session["student_id"],),
        ).fetchall()
        fee_row = db.execute(
            """SELECT * FROM fee_records WHERE student_id = ?
               ORDER BY period DESC, id DESC LIMIT 1""",
            (session["student_id"],),
        ).fetchone()
        fee = fee_record_dict(fee_row) if fee_row else None
        room_change_requests = db.execute(
            """SELECT rc.id, rc.reason, rc.status, rc.admin_comment, rc.requested_at,
                      old_room.room_number AS current_room_number,
                      new_room.room_number AS requested_room_number
               FROM room_change_requests rc
               LEFT JOIN rooms old_room ON old_room.id = rc.current_room_id
               LEFT JOIN rooms new_room ON new_room.id = rc.requested_room_id
               WHERE rc.student_id = ? ORDER BY rc.requested_at DESC, rc.id DESC LIMIT 5""",
            (session["student_id"],),
        ).fetchall()
        payment_history = db.execute(
            """SELECT p.*, f.period FROM fee_payments p
               JOIN fee_records f ON f.id = p.fee_record_id
               WHERE f.student_id = ? ORDER BY p.payment_date DESC, p.id DESC""",
            (session["student_id"],),
        ).fetchall()
        attendance_summary = db.execute(
            """SELECT COUNT(*) AS total_days,
                      SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END) AS present_days,
                      SUM(CASE WHEN status = 'Absent' THEN 1 ELSE 0 END) AS absent_days,
                      SUM(CASE WHEN status = 'Late' THEN 1 ELSE 0 END) AS late_days,
                      SUM(CASE WHEN status = 'Leave' THEN 1 ELSE 0 END) AS leave_days
               FROM attendance WHERE student_id = ?""",
            (session["student_id"],),
        ).fetchone()
        today_attendance = db.execute(
            "SELECT status FROM attendance WHERE student_id = ? AND attendance_date = ?",
            (session["student_id"], date.today().isoformat()),
        ).fetchone()
        attendance_recent = db.execute(
            """SELECT attendance_date, status, notes FROM attendance
               WHERE student_id = ? ORDER BY attendance_date DESC LIMIT 5""",
            (session["student_id"],),
        ).fetchall()
        attendance_days = sum(
            attendance_summary[f"{status}_days"] or 0
            for status in ("present", "absent", "late")
        )
        present_days = attendance_summary["present_days"] or 0
        attendance_rate = round(present_days * 100 / attendance_days) if attendance_days else 0
    reminder = "none"
    if fee:
        if fee["computed_status"] == "PAID":
            reminder = "paid"
        elif fee["computed_status"] == "OVERDUE":
            reminder = "overdue"
        elif (date.fromisoformat(fee["due_date"]) - date.today()).days <= 14:
            reminder = "due-soon"
        else:
            reminder = "pending"
    return render_template(
        "student_dashboard.html",
        student=student,
        room=room,
        roommates=roommates,
        complaints=complaint_rows,
        fee=fee,
        payment_history=payment_history,
        fee_reminder=reminder,
        attendance_summary=attendance_summary,
        today_attendance=today_attendance,
        attendance_recent=attendance_recent,
        attendance_rate=attendance_rate,
        room_change_requests=room_change_requests,
        now=date.today(),
    )


def month_start(month_date):
    return month_date.replace(day=1)


def previous_month(month_date):
    if month_date.month == 1:
        return date(month_date.year - 1, 12, 1)
    return date(month_date.year, month_date.month - 1, 1)


def six_months_before(day):
    month = day.month - 6
    year = day.year
    while month <= 0:
        year -= 1
        month += 12
    return day.replace(day=min(day.day, calendar.monthrange(year, month)[1]), year=year, month=month)


@app.route("/attendance/dashboard")
@login_required("admin")
def attendance_dashboard():
    today = date.today()
    range_start = six_months_before(today)
    month_chart_start = month_start(today)
    for _ in range(5):
        month_chart_start = previous_month(month_chart_start)
    search = request.args.get("q", "").strip()
    room_filter = request.args.get("room", "").strip()
    try:
        threshold = int(request.args.get("threshold", "75"))
    except ValueError:
        threshold = 75
    if not 1 <= threshold <= 100:
        threshold = 75

    with get_db() as db:
        student_count = db.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        today_rows = db.execute(
            "SELECT status, COUNT(*) AS count FROM attendance WHERE attendance_date = ? GROUP BY status",
            (today.isoformat(),),
        ).fetchall()
        today_counts = {status: 0 for status in ATTENDANCE_STATUSES}
        today_counts.update({row["status"]: row["count"] for row in today_rows})
        all_rows = attendance_report_rows(db, range_start.isoformat(), today.isoformat())
        overall_rows = attendance_report_rows(
            db, range_start.isoformat(), today.isoformat(), search, room_filter
        )
        rooms = db.execute(
            """SELECT DISTINCT r.room_number FROM rooms r
               JOIN allocations a ON a.room_id = r.id ORDER BY r.room_number"""
        ).fetchall()

        month_points = []
        month = month_chart_start
        while month <= month_start(today):
            next_month = (
                date(month.year + 1, 1, 1)
                if month.month == 12
                else date(month.year, month.month + 1, 1)
            )
            counts = db.execute(
                """SELECT
                       SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END) AS present,
                       SUM(CASE WHEN status = 'Absent' THEN 1 ELSE 0 END) AS absent,
                       SUM(CASE WHEN status = 'Late' THEN 1 ELSE 0 END) AS late,
                       SUM(CASE WHEN status = 'Leave' THEN 1 ELSE 0 END) AS leave_count
                   FROM attendance WHERE attendance_date >= ? AND attendance_date < ?""",
                (month.isoformat(), next_month.isoformat()),
            ).fetchone()
            present = counts["present"] or 0
            absent = counts["absent"] or 0
            late = counts["late"] or 0
            denominator = present + absent + late
            month_points.append(
                {
                    "label": month.strftime("%b"),
                    "period": month.strftime("%Y-%m"),
                    "rate": round(present * 100 / denominator) if denominator else 0,
                    "present": present,
                    "absent": absent,
                    "leave": counts["leave_count"] or 0,
                }
            )
            month = next_month

    present_total = sum(row["present_days"] for row in all_rows)
    absent_total = sum(row["absent_days"] for row in all_rows)
    late_total = sum(row["late_days"] for row in all_rows)
    overall_denominator = present_total + absent_total + late_total
    overall_rate = round(present_total * 100 / overall_denominator) if overall_denominator else 0
    unmarked_today = max(student_count - sum(today_counts.values()), 0)
    low_students = [
        row for row in overall_rows
        if row["total_days"] and row["attendance_rate"] < threshold
    ]
    return render_template(
        "attendance_dashboard.html",
        student_count=student_count,
        today_counts=today_counts,
        unmarked_today=unmarked_today,
        overall_rate=overall_rate,
        month_points=month_points,
        rows=overall_rows,
        low_students=low_students,
        range_start=range_start.isoformat(),
        today=today.isoformat(),
        search=search,
        room_filter=room_filter,
        rooms=rooms,
        threshold=threshold,
        statuses=ATTENDANCE_STATUSES,
    )


@app.route("/attendance", methods=["GET", "POST"])
@login_required("admin")
def attendance_management():
    selected_date = request.values.get("date", date.today().isoformat())
    try:
        attendance_day = parse_attendance_date(selected_date, allow_future=request.method == "GET")
    except ValueError as error:
        flash(str(error), "error")
        attendance_day = date.today()
        selected_date = attendance_day.isoformat()

    search = request.values.get("q", "").strip()
    room_filter = request.values.get("room", "").strip()
    if request.method == "POST":
        selected_ids = request.form.getlist("student_ids")
        records = [
            {
                "student_id": student_id,
                "status": request.form.get(f"status_{student_id}", ""),
                "notes": request.form.get(f"notes_{student_id}", ""),
            }
            for student_id in selected_ids
        ]
        try:
            with get_db() as db:
                saved_count = save_attendance(
                    db, attendance_day.isoformat(), records, session["user_id"]
                )
            flash(f"Attendance saved for {saved_count} student(s).", "success")
        except ValueError as error:
            flash(str(error), "error")
        except sqlite3.IntegrityError:
            flash("Attendance could not be saved due to a database constraint.", "error")
        query_args = {"date": selected_date}
        if search:
            query_args["q"] = search
        if room_filter:
            query_args["room"] = room_filter
        return redirect(url_for("attendance_management", **query_args))

    with get_db() as db:
        roster = attendance_roster(db, selected_date, search, room_filter)
        rooms = db.execute(
            """SELECT DISTINCT r.room_number FROM rooms r
               JOIN allocations a ON a.room_id = r.id ORDER BY r.room_number"""
        ).fetchall()
    counts = {status: sum(row["status"] == status for row in roster) for status in ATTENDANCE_STATUSES}
    marked_count = sum(counts.values())
    return render_template(
        "attendance.html",
        students=roster,
        rooms=rooms,
        selected_date=selected_date,
        search=search,
        room_filter=room_filter,
        statuses=ATTENDANCE_STATUSES,
        counts=counts,
        marked_count=marked_count,
        unmarked_count=len(roster) - marked_count,
        today=date.today().isoformat(),
    )


@app.route("/attendance/reports")
@login_required("admin")
def attendance_reports():
    today = date.today()
    date_from = request.args.get("date_from", today.replace(day=1).isoformat())
    date_to = request.args.get("date_to", today.isoformat())
    try:
        start_day = parse_attendance_date(date_from, allow_future=True)
        end_day = parse_attendance_date(date_to, allow_future=True)
        if start_day > end_day:
            raise ValueError("Start date must be on or before the end date.")
    except ValueError as error:
        flash(str(error), "error")
        start_day = today.replace(day=1)
        end_day = today
        date_from = start_day.isoformat()
        date_to = end_day.isoformat()

    search = request.args.get("q", "").strip()
    room_filter = request.args.get("room", "").strip()
    status_filter = request.args.get("status", "").strip()
    if status_filter not in ATTENDANCE_STATUSES:
        status_filter = ""

    with get_db() as db:
        rows = attendance_report_rows(
            db, date_from, date_to, search, room_filter, status_filter
        )
        rooms = db.execute(
            """SELECT DISTINCT r.room_number FROM rooms r
               JOIN allocations a ON a.room_id = r.id ORDER BY r.room_number"""
        ).fetchall()

    totals = {
        "students": len(rows),
        "records": sum(row["total_days"] for row in rows),
        "present": sum(row["present_days"] for row in rows),
        "absent": sum(row["absent_days"] for row in rows),
        "late": sum(row["late_days"] for row in rows),
        "leave": sum(row["leave_days"] for row in rows),
    }
    totals["rate"] = round(
        totals["present"] * 100
        / max(totals["present"] + totals["absent"] + totals["late"], 1)
    )
    if request.args.get("export") == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Student ID", "Student Name", "Room Number", "Recorded Days",
                "Present", "Absent", "Late", "Leave", "Attendance Rate (%)",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["student_id"], row["name"], row["room_number"] or "",
                    row["total_days"], row["present_days"], row["absent_days"],
                    row["late_days"], row["leave_days"], row["attendance_rate"],
                ]
            )
        response = make_response("\ufeff" + output.getvalue())
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = (
            f"attachment; filename=attendance-report-{date_from}-to-{date_to}.csv"
        )
        return response

    return render_template(
        "attendance_reports.html",
        rows=rows,
        rooms=rooms,
        totals=totals,
        date_from=date_from,
        date_to=date_to,
        search=search,
        room_filter=room_filter,
        status_filter=status_filter,
        statuses=ATTENDANCE_STATUSES,
    )


def student_attendance_filter_values(args):
    month_value = args.get("month", "").strip()
    date_from = args.get("date_from", "").strip()
    date_to = args.get("date_to", "").strip()
    status = args.get("status", "").strip()
    if month_value:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month_value):
            raise ValueError("Choose a valid attendance month.")
        month_date = date.fromisoformat(f"{month_value}-01")
        next_month = (
            date(month_date.year + 1, 1, 1)
            if month_date.month == 12
            else date(month_date.year, month_date.month + 1, 1)
        )
        date_from = month_date.isoformat()
        date_to = (next_month - timedelta(days=1)).isoformat()
    if date_from:
        date_from = parse_attendance_date(date_from, allow_future=True).isoformat()
    if date_to:
        date_to = parse_attendance_date(date_to, allow_future=True).isoformat()
    if date_from and date_to and date_from > date_to:
        raise ValueError("Start date must be on or before the end date.")
    if status and status not in ATTENDANCE_STATUSES:
        raise ValueError("Choose a valid attendance status.")
    return {
        "month": month_value,
        "date_from": date_from,
        "date_to": date_to,
        "status": status,
    }


@app.route("/student/attendance")
@login_required("student")
def student_attendance():
    try:
        filters = student_attendance_filter_values(request.args)
    except ValueError as error:
        flash(str(error), "error")
        filters = {"month": "", "date_from": "", "date_to": "", "status": ""}

    clauses = ["student_id = ?"]
    params = [session["student_id"]]
    if filters["date_from"]:
        clauses.append("attendance_date >= ?")
        params.append(filters["date_from"])
    if filters["date_to"]:
        clauses.append("attendance_date <= ?")
        params.append(filters["date_to"])
    if filters["status"]:
        clauses.append("status = ?")
        params.append(filters["status"])
    with get_db() as db:
        records = db.execute(
            f"""SELECT attendance_date, status, notes FROM attendance
                WHERE {' AND '.join(clauses)}
                ORDER BY attendance_date DESC""",
            params,
        ).fetchall()
    month_value = filters["month"] or date.today().strftime("%Y-%m")
    month_date = date.fromisoformat(f"{month_value}-01")
    next_month = (
        date(month_date.year + 1, 1, 1)
        if month_date.month == 12
        else date(month_date.year, month_date.month + 1, 1)
    )
    with get_db() as db:
        month_records = db.execute(
            """SELECT attendance_date, status FROM attendance
               WHERE student_id = ? AND attendance_date >= ? AND attendance_date < ?""",
            (session["student_id"], month_date.isoformat(), next_month.isoformat()),
        ).fetchall()
    calendar_records = {row["attendance_date"]: row["status"] for row in month_records}
    calendar_weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(
        month_date.year, month_date.month
    )
    return render_template(
        "student_attendance.html",
        records=records,
        filters=filters,
        month_value=month_value,
        month_label=month_date.strftime("%B %Y"),
        calendar_weeks=calendar_weeks,
        calendar_records=calendar_records,
        today=date.today().isoformat(),
    )


@app.route("/api/admin/attendance", methods=["GET", "POST"])
@login_required("admin")
def admin_attendance_api():
    if request.method == "GET":
        selected_date = request.args.get("date", date.today().isoformat())
        try:
            attendance_day = parse_attendance_date(selected_date, allow_future=True)
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        search = request.args.get("q", "").strip()
        room_filter = request.args.get("room", "").strip()
        with get_db() as db:
            rows = attendance_roster(db, attendance_day.isoformat(), search, room_filter)
        return jsonify(
            {
                "date": attendance_day.isoformat(),
                "students": [
                    {
                        **dict(row),
                        "status": row["status"] or "Unmarked",
                    }
                    for row in rows
                ],
            }
        )

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Send a JSON object containing date and records."}), 400
    try:
        attendance_day = parse_attendance_date(payload.get("date", ""))
        records = payload.get("records")
        with get_db() as db:
            saved_count = save_attendance(
                db, attendance_day.isoformat(), records, session["user_id"]
            )
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except sqlite3.IntegrityError:
        return jsonify({"error": "Attendance could not be saved due to a database constraint."}), 400
    return jsonify({"saved": saved_count, "date": attendance_day.isoformat()})


@app.route("/api/student/attendance")
@login_required("student")
def student_attendance_api():
    try:
        filters = student_attendance_filter_values(request.args)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    clauses = ["student_id = ?"]
    params = [session["student_id"]]
    if filters["date_from"]:
        clauses.append("attendance_date >= ?")
        params.append(filters["date_from"])
    if filters["date_to"]:
        clauses.append("attendance_date <= ?")
        params.append(filters["date_to"])
    if filters["status"]:
        clauses.append("status = ?")
        params.append(filters["status"])
    with get_db() as db:
        records = db.execute(
            f"""SELECT attendance_date, status, notes, marked_at
                FROM attendance WHERE {' AND '.join(clauses)}
                ORDER BY attendance_date DESC""",
            params,
        ).fetchall()
    return jsonify({"attendance": [dict(row) for row in records]})


@app.route("/fees")
@login_required("admin")
def fee_management():
    search = request.args.get("q", "").strip().casefold()
    status_filter = request.args.get("status", "")
    room_filter = request.args.get("room", "")
    due_filter = request.args.get("due_date", "")
    period_filter = request.args.get("period", "")
    if status_filter not in ("", "PAID", "NOT PAID"):
        status_filter = ""
    if due_filter:
        try:
            parse_iso_date(due_filter, "due date")
        except ValueError:
            flash("The due date filter is invalid.", "error")
            due_filter = ""

    with get_db() as db:
        fee_rows = load_fee_records(db)
        summary = current_fee_summary(db)
        collections = monthly_fee_collections(db)
        rooms_rows = db.execute("SELECT DISTINCT room_number FROM rooms ORDER BY room_number").fetchall()
        periods = db.execute("SELECT DISTINCT period FROM fee_records ORDER BY period DESC").fetchall()
    filtered_fees = []
    for fee in fee_rows:
        is_paid = fee["computed_status"] == "PAID"
        if status_filter == "PAID" and not is_paid:
            continue
        if status_filter == "NOT PAID" and is_paid:
            continue
        if room_filter:
            if room_filter == "__unassigned__" and fee["room_number"] is not None:
                continue
            if room_filter != "__unassigned__" and fee["room_number"] != room_filter:
                continue
        if due_filter and fee["due_date"] != due_filter:
            continue
        if period_filter and fee["period"] != period_filter:
            continue
        if search and search not in fee["name"].casefold() and search not in fee["student_code"].casefold():
            continue
        filtered_fees.append(fee)

    peak_collection = max((month["amount"] for month in collections), default=0)
    total_students = max(summary["students"], 1)
    paid_percent = round(summary["paid"] * 100 / total_students)
    return render_template(
        "fees.html",
        fees=filtered_fees,
        summary=summary,
        collections=collections,
        peak_collection=peak_collection,
        paid_percent=paid_percent,
        rooms=rooms_rows,
        periods=periods,
        filters={
            "q": request.args.get("q", ""),
            "status": status_filter,
            "room": room_filter,
            "due_date": due_filter,
            "period": period_filter,
        },
    )


@app.route("/fees/add", methods=["GET", "POST"])
@login_required("admin")
def add_fee_record():
    with get_db() as db:
        students_rows = db.execute("SELECT id, student_id, name FROM students ORDER BY name").fetchall()
    values = {
        "student_id": request.form.get("student_id", ""),
        "period": request.form.get("period", ""),
        "fee_amount": request.form.get("fee_amount", ""),
        "due_date": request.form.get("due_date", ""),
    }
    if request.method == "POST":
        try:
            try:
                student_id = int(values["student_id"])
            except ValueError:
                raise ValueError("Select an existing student.") from None
            fee_amount = parse_money(values["fee_amount"], "fee amount", allow_zero=True)
            due_date = parse_iso_date(values["due_date"], "due date")
            period = values["period"].strip()
            if not period or len(period) > 60:
                raise ValueError("Enter a fee period of 1 to 60 characters.")
            with get_db() as db:
                student = db.execute("SELECT id FROM students WHERE id = ?", (student_id,)).fetchone()
                if not student:
                    raise ValueError("Select an existing student.")
                status = "NOT PAID"
                if fee_amount == 0:
                    status = "PAID"
                elif due_date < date.today():
                    status = "OVERDUE"
                db.execute(
                    """INSERT INTO fee_records
                       (student_id, period, fee_amount, paid_amount, pending_amount, due_date, status)
                       VALUES (?, ?, ?, 0, ?, ?, ?)""",
                    (student_id, period, fee_amount, fee_amount, due_date.isoformat(), status),
                )
            flash("Fee record created.", "success")
            return redirect(url_for("fee_management"))
        except ValueError as error:
            flash(str(error), "error")
        except sqlite3.IntegrityError:
            flash("This student already has a fee record for that period.", "error")
    return render_template("add_fee.html", students=students_rows, values=values)


@app.route("/fees/<int:fee_id>")
@login_required("admin")
def fee_details(fee_id):
    with get_db() as db:
        sync_fee_statuses(db)
        fee_row = db.execute(
            """SELECT f.*, s.student_id AS student_code, s.name, s.department, s.email,
                      r.room_number, r.block, r.floor
               FROM fee_records f JOIN students s ON s.id = f.student_id
               LEFT JOIN allocations a ON a.student_id = s.id
               LEFT JOIN rooms r ON r.id = a.room_id
               WHERE f.id = ?""",
            (fee_id,),
        ).fetchone()
        if fee_row:
            payments = db.execute(
                """SELECT * FROM fee_payments WHERE fee_record_id = ?
                   ORDER BY payment_date DESC, id DESC""",
                (fee_id,),
            ).fetchall()
        else:
            payments = []
    if not fee_row:
        flash("Fee record not found.", "error")
        return redirect(url_for("fee_management"))
    fee = fee_record_dict(fee_row)
    return render_template(
        "fee_details.html",
        fee=fee,
        payments=payments,
        payment_methods=PAYMENT_METHODS,
        today=date.today().isoformat(),
    )


@app.route("/fees/<int:fee_id>/payments", methods=["POST"])
@login_required("admin")
def record_fee_payment(fee_id):
    try:
        payment_date = parse_iso_date(request.form.get("payment_date", ""), "payment date")
        if payment_date > date.today():
            raise ValueError("Payment date cannot be in the future.")
        method = request.form.get("payment_method", "")
        transaction_id = request.form.get("transaction_id", "").strip() or f"HRM-{uuid4().hex.upper()}"
        if method not in PAYMENT_METHODS:
            raise ValueError("Choose a valid payment method.")
        if len(transaction_id) > 64:
            raise ValueError("Transaction IDs must be 64 characters or fewer.")
        with get_db() as db:
            db.execute("BEGIN IMMEDIATE")
            fee = db.execute("SELECT * FROM fee_records WHERE id = ?", (fee_id,)).fetchone()
            if not fee:
                raise ValueError("Fee record not found.")
            pending = round(fee["fee_amount"] - fee["paid_amount"], 2)
            amount = pending if request.form.get("mark_remaining") else parse_money(
                request.form.get("amount", ""), "payment amount"
            )
            if amount <= 0:
                raise ValueError("There is no remaining balance to mark as paid.")
            if amount > pending:
                raise ValueError("Payment cannot exceed the remaining fee balance.")
            paid_amount = round(fee["paid_amount"] + amount, 2)
            pending_amount = round(fee["fee_amount"] - paid_amount, 2)
            status = "PAID" if pending_amount == 0 else (
                "PARTIALLY PAID" if payment_date <= date.fromisoformat(fee["due_date"]) else "OVERDUE"
            )
            db.execute(
                """INSERT INTO fee_payments
                   (fee_record_id, amount, payment_date, transaction_id, payment_method)
                   VALUES (?, ?, ?, ?, ?)""",
                (fee_id, amount, payment_date.isoformat(), transaction_id, method),
            )
            db.execute(
                """UPDATE fee_records
                   SET paid_amount = ?, pending_amount = ?, payment_date = ?,
                       payment_method = ?, transaction_id = ?, status = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (
                    paid_amount, pending_amount, payment_date.isoformat(), method,
                    transaction_id, status, fee_id,
                ),
            )
        flash(f"Payment of ₹{amount:,.2f} recorded.", "success")
    except ValueError as error:
        flash(str(error), "error")
    except sqlite3.IntegrityError:
        flash("That transaction ID has already been used.", "error")
    return redirect(url_for("fee_details", fee_id=fee_id))


@app.route("/api/admin/fees/summary")
@login_required("admin")
def admin_fee_summary_api():
    with get_db() as db:
        summary = current_fee_summary(db)
        collections = monthly_fee_collections(db)
    paid_percent = round(summary["paid"] * 100 / max(summary["students"], 1))
    return jsonify(
        {
            **summary,
            "paid_percent": paid_percent,
            "monthly_collections": collections,
        }
    )


@app.route("/api/admin/fees/<int:fee_id>")
@login_required("admin")
def admin_fee_details_api(fee_id):
    with get_db() as db:
        sync_fee_statuses(db)
        row = db.execute(
            """SELECT f.*, s.student_id AS student_code, s.name, r.room_number
               FROM fee_records f JOIN students s ON s.id = f.student_id
               LEFT JOIN allocations a ON a.student_id = s.id
               LEFT JOIN rooms r ON r.id = a.room_id
               WHERE f.id = ?""",
            (fee_id,),
        ).fetchone()
        if row:
            payments = db.execute(
                "SELECT * FROM fee_payments WHERE fee_record_id = ? ORDER BY payment_date DESC, id DESC",
                (fee_id,),
            ).fetchall()
    if not row:
        return jsonify({"error": "Fee record not found."}), 404
    return jsonify({"fee": fee_record_dict(row), "payments": [payment_dict(p) for p in payments]})


@app.route("/api/student/fees")
@login_required("student")
def student_fees_api():
    with get_db() as db:
        sync_fee_statuses(db)
        rows = db.execute(
            """SELECT * FROM fee_records WHERE student_id = ?
               ORDER BY period DESC, id DESC""",
            (session["student_id"],),
        ).fetchall()
        payments = db.execute(
            """SELECT p.*, f.period FROM fee_payments p
               JOIN fee_records f ON f.id = p.fee_record_id
               WHERE f.student_id = ? ORDER BY p.payment_date DESC, p.id DESC""",
            (session["student_id"],),
        ).fetchall()
    return jsonify(
        {
            "fees": [fee_record_dict(row) for row in rows],
            "payments": [payment_dict(row) for row in payments],
        }
    )


init_db()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "true").lower() == "true")
