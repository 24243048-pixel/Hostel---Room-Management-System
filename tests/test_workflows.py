"""End-to-end smoke tests for the main hostel management workflows."""

import os
import tempfile
import unittest
from datetime import date, timedelta


_test_directory = tempfile.TemporaryDirectory()
os.environ["HOSTEL_DATABASE_PATH"] = os.path.join(_test_directory.name, "test-hostel.db")

from app import app  # noqa: E402
from database import get_db, init_db  # noqa: E402


class HostelWorkflowTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_attendance_sample_data_marking_reports_and_student_scope(self):
        with get_db() as db:
            sample_count = db.execute(
                "SELECT COUNT(*) FROM students WHERE student_id BETWEEN 'ST001' AND 'ST040'"
            ).fetchone()[0]
            existing_demo = db.execute(
                "SELECT name, email FROM students WHERE student_id = 'STU001'"
            ).fetchone()
            student_19 = db.execute(
                """SELECT s.id, s.name, r.room_number
                   FROM students s LEFT JOIN allocations a ON a.student_id = s.id
                   LEFT JOIN rooms r ON r.id = a.room_id WHERE s.student_id = 'ST019'"""
            ).fetchone()
            student_40 = db.execute(
                """SELECT s.id, s.name, r.room_number
                   FROM students s LEFT JOIN allocations a ON a.student_id = s.id
                   LEFT JOIN rooms r ON r.id = a.room_id WHERE s.student_id = 'ST040'"""
            ).fetchone()
        self.assertEqual(sample_count, 40)
        self.assertEqual(existing_demo["name"], "Aarav Sharma")
        self.assertEqual(existing_demo["email"], "aarav@example.com")
        self.assertEqual((student_19["name"], student_19["room_number"]), ("Anjali", "209"))
        self.assertEqual((student_40["name"], student_40["room_number"]), ("Dharun", "410"))
        init_db()
        with get_db() as db:
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM students WHERE student_id BETWEEN 'ST001' AND 'ST040'"
                ).fetchone()[0],
                40,
            )
            fee_period = f"{date.today().year}-{date.today().year + 1} Attendance Sample"
            fee_rows = db.execute(
                """SELECT s.student_id, f.status, f.payment_date, f.fee_amount
                   FROM fee_records f JOIN students s ON s.id = f.student_id
                   WHERE f.period = ? ORDER BY s.student_id""",
                (fee_period,),
            ).fetchall()
            attendance_sample_total = db.execute(
                """SELECT COUNT(*) FROM attendance a JOIN students s ON s.id = a.student_id
                   WHERE s.student_id BETWEEN 'ST001' AND 'ST040'
                     AND a.attendance_date >= ? AND a.attendance_date <= ?""",
                ((date.today() - timedelta(days=184)).isoformat(), date.today().isoformat()),
            ).fetchone()[0]
            attendance_students = db.execute(
                """SELECT COUNT(DISTINCT a.student_id) FROM attendance a
                   JOIN students s ON s.id = a.student_id
                   WHERE s.student_id BETWEEN 'ST001' AND 'ST040'"""
            ).fetchone()[0]
            percentages = db.execute(
                """SELECT s.student_id,
                          100.0 * SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END) /
                          SUM(CASE WHEN a.status IN ('Present', 'Absent', 'Late') THEN 1 ELSE 0 END)
                   FROM students s JOIN attendance a ON a.student_id = s.id
                   WHERE s.student_id BETWEEN 'ST001' AND 'ST040'
                   GROUP BY s.student_id"""
            ).fetchall()
        self.assertEqual(len(fee_rows), 40)
        self.assertEqual(sum(row["status"] == "PAID" for row in fee_rows), 30)
        self.assertEqual(sum(row["status"] == "NOT PAID" for row in fee_rows), 10)
        self.assertTrue(all(row["payment_date"] for row in fee_rows if row["status"] == "PAID"))
        self.assertTrue(all(row["payment_date"] is None for row in fee_rows if row["status"] == "NOT PAID"))
        self.assertEqual(attendance_students, 40)
        self.assertGreater(attendance_sample_total, 5000)
        pct_values = [row[1] for row in percentages]
        self.assertGreater(max(pct_values), 85)
        self.assertLess(min(pct_values), 75)
        self.assertGreater(len(set(round(value) for value in pct_values)), 10)

        admin = self.client.post(
            "/login", data={"role": "admin", "username": "admin", "password": "admin123"}, follow_redirects=True
        )
        self.assertIn("Attendance ·".encode(), admin.data)
        self.assertEqual(self.client.get("/attendance/dashboard").status_code, 200)
        self.assertIn(b"Attendance", self.client.get("/students?q=ST019").data)

        roster = self.client.get("/attendance?q=ST019&room=209")
        self.assertEqual(roster.status_code, 200)
        self.assertIn(b"Anjali", roster.data)
        self.assertIn(b"ST019", roster.data)
        self.assertIn(b"209", roster.data)
        self.assertNotIn(b"ST020", roster.data)
        search_last_sample = self.client.get("/attendance?q=ST040&room=410")
        self.assertIn(b"Dharun", search_last_sample.data)
        self.assertIn(b"ST040", self.client.get("/students?q=ST040").data)
        admin_dashboard = self.client.get("/admin")
        self.assertIn(b"Total students", admin_dashboard.data)
        with get_db() as db:
            total_students = db.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        self.assertIn(f">{total_students}</strong>".encode(), admin_dashboard.data)
        empty_range_report = self.client.get(
            "/attendance/reports?date_from=2020-01-01&date_to=2020-01-31&q=ST040&room=410"
        )
        self.assertIn(b"Dharun", empty_range_report.data)
        self.assertIn(b"ST040", empty_range_report.data)

        with get_db() as db:
            pk19 = db.execute("SELECT id FROM students WHERE student_id = 'ST019'").fetchone()["id"]
            pk20 = db.execute("SELECT id FROM students WHERE student_id = 'ST020'").fetchone()["id"]
        today = date.today().isoformat()
        save_response = self.client.post(
            "/api/admin/attendance",
            json={
                "date": today,
                "records": [
                    {"student_id": pk19, "status": "Present", "notes": "On time"},
                    {"student_id": pk20, "status": "Absent", "notes": ""},
                ],
            },
        )
        self.assertEqual(save_response.status_code, 200)
        self.assertEqual(save_response.get_json()["saved"], 2)
        update_response = self.client.post(
            "/api/admin/attendance",
            json={
                "date": today,
                "records": [{"student_id": pk19, "status": "Late", "notes": "Updated record"}],
            },
        )
        self.assertEqual(update_response.status_code, 200)
        with get_db() as db:
            attendance_rows = db.execute(
                """SELECT student_id, status FROM attendance
                   WHERE attendance_date = ? AND student_id IN (?, ?)
                   ORDER BY student_id""",
                (today, pk19, pk20),
            ).fetchall()
        self.assertEqual([(row["student_id"], row["status"]) for row in attendance_rows], [
            (pk19, "Late"), (pk20, "Absent")
        ])

        invalid_status = self.client.post(
            "/api/admin/attendance",
            json={
                "date": today,
                "records": [{"student_id": pk19, "status": "Excused", "notes": ""}],
            },
        )
        self.assertEqual(invalid_status.status_code, 400)
        future_date = (date.today() + timedelta(days=1)).isoformat()
        invalid_future = self.client.post(
            "/api/admin/attendance",
            json={
                "date": future_date,
                "records": [{"student_id": pk19, "status": "Present", "notes": ""}],
            },
        )
        self.assertEqual(invalid_future.status_code, 400)

        reports = self.client.get(
            f"/attendance/reports?date_from={today}&date_to={today}&q=ST019&room=209"
        )
        self.assertEqual(reports.status_code, 200)
        self.assertIn(b"Anjali", reports.data)
        self.assertIn(b"0%", reports.data)
        export = self.client.get(
            f"/attendance/reports?date_from={today}&date_to={today}&export=csv"
        )
        self.assertEqual(export.status_code, 200)
        self.assertIn(b"ST019,Anjali,209", export.data)
        api_roster = self.client.get(f"/api/admin/attendance?date={today}&q=ST040&room=410")
        self.assertEqual(api_roster.status_code, 200)
        self.assertEqual(api_roster.get_json()["students"][0]["student_id"], "ST040")

        with self.client.session_transaction() as session:
            session.clear()
        student_login = self.client.post(
            "/login", data={"role": "student", "username": "ST019", "password": "student123"}, follow_redirects=True
        )
        self.assertIn(b"Attendance", student_login.data)
        student_history = self.client.get("/student/attendance")
        self.assertEqual(student_history.status_code, 200)
        self.assertIn(b"Updated record", student_history.data)
        self.assertIn(b"Attendance calendar", student_history.data)
        own_attendance = self.client.get("/api/student/attendance")
        self.assertEqual(own_attendance.status_code, 200)
        with get_db() as db:
            expected_student_attendance = db.execute(
                "SELECT COUNT(*) FROM attendance WHERE student_id = ?", (pk19,)
            ).fetchone()[0]
        self.assertEqual(
            len(own_attendance.get_json()["attendance"]), expected_student_attendance
        )
        self.assertEqual(self.client.get("/api/admin/attendance").status_code, 302)
        self.assertEqual(self.client.get("/attendance").status_code, 302)

        with self.client.session_transaction() as session:
            session.clear()
        priya_login = self.client.post(
            "/login",
            data={"role": "student", "username": "ST001", "password": "student123"},
            follow_redirects=True,
        )
        self.assertIn(b"Hello, Priya", priya_login.data)
        self.assertNotIn(b"Hello, Kavin", priya_login.data)
        priya_fee = self.client.get("/api/student/fees").get_json()
        self.assertTrue(all(fee["student_id"] != student_19["id"] for fee in priya_fee["fees"]))
        with self.client.session_transaction() as session:
            session.clear()
        kavin_login = self.client.post(
            "/login",
            data={"role": "student", "username": "ST002", "password": "student123"},
            follow_redirects=True,
        )
        self.assertIn(b"Hello, Kavin", kavin_login.data)
        self.assertNotIn(b"Hello, Priya", kavin_login.data)
        kavin_attendance = self.client.get("/api/student/attendance?student_id=1").get_json()
        with get_db() as db:
            kavin_pk = db.execute("SELECT id FROM students WHERE student_id = 'ST002'").fetchone()["id"]
            expected_kavin_attendance = db.execute(
                "SELECT COUNT(*) FROM attendance WHERE student_id = ?", (kavin_pk,)
            ).fetchone()[0]
        self.assertEqual(len(kavin_attendance["attendance"]), expected_kavin_attendance)
        self.assertGreater(expected_kavin_attendance, 0)
        mismatch = self.client.post(
            "/login",
            data={"role": "admin", "username": "ST001", "password": "student123"},
            follow_redirects=True,
        )
        self.assertIn(b"Incorrect credentials", mismatch.data)

    def test_admin_student_room_allocation_and_complaint_workflows(self):
        database_path = os.environ["HOSTEL_DATABASE_PATH"]
        self.assertTrue(os.path.exists(database_path))

        response = self.client.post(
            "/login", data={"role": "admin", "username": "admin", "password": "admin123"}, follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Total students", response.data)

        response = self.client.post(
            "/students/add",
            data={
                "student_id": "TEST100",
                "name": "Test Resident",
                "department": "Computer Science",
                "year": "2",
                "phone": "9876500000",
                "email": "test.resident@example.com",
                "gender": "Other",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Test Resident", response.data)
        self.assertIn(b"student123", response.data)
        with get_db() as db:
            student = db.execute("SELECT id FROM students WHERE student_id = 'TEST100'").fetchone()
            student_pk = student["id"]

        duplicate = self.client.post(
            "/students/add",
            data={
                "student_id": "TEST100",
                "name": "Duplicate",
                "department": "Computer Science",
                "year": "2",
                "phone": "9876500001",
                "email": "duplicate@example.com",
                "gender": "Other",
            },
            follow_redirects=True,
        )
        self.assertIn(b"already in use", duplicate.data)
        self.assertEqual(self.client.get("/students?q=Test%20Resident").status_code, 200)

        edited = self.client.post(
            f"/students/{student_pk}/edit",
            data={
                "student_id": "TEST100",
                "name": "Updated Resident",
                "department": "Computer Science",
                "year": "3",
                "phone": "9876500000",
                "email": "test.resident@example.com",
                "gender": "Other",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Updated Resident", edited.data)

        room_response = self.client.post(
            "/rooms/add",
            data={"room_number": "T-001", "block": "T", "floor": "0", "capacity": "1"},
            follow_redirects=True,
        )
        self.assertIn(b"T-001", room_response.data)
        duplicate_room = self.client.post(
            "/rooms/add",
            data={"room_number": "T-001", "block": "T", "floor": "0", "capacity": "2"},
            follow_redirects=True,
        )
        self.assertIn(b"already exists", duplicate_room.data)
        with get_db() as db:
            room_pk = db.execute("SELECT id FROM rooms WHERE room_number = 'T-001'").fetchone()["id"]
        room_edit = self.client.post(
            f"/rooms/{room_pk}/edit",
            data={"room_number": "T-001", "block": "T", "floor": "1", "capacity": "1"},
            follow_redirects=True,
        )
        self.assertIn(b"Room details updated", room_edit.data)
        with get_db() as db:
            replacement_room_pk = db.execute(
                "SELECT id FROM rooms WHERE room_number = 'B-201'"
            ).fetchone()["id"]
            unallocated_student = db.execute(
                """SELECT s.id FROM students s LEFT JOIN allocations a ON a.student_id = s.id
                   WHERE a.id IS NULL LIMIT 1"""
            ).fetchone()["id"]

        allocated = self.client.post(
            "/allocation", data={"student_id": str(student_pk), "room_id": str(room_pk)}, follow_redirects=True
        )
        self.assertIn(b"Room allocation saved", allocated.data)
        full_room_attempt = self.client.post(
            "/allocation",
            data={"student_id": str(unallocated_student), "room_id": str(room_pk)},
            follow_redirects=True,
        )
        self.assertIn(b"room is full", full_room_attempt.data)
        with get_db() as db:
            self.assertEqual(
                db.execute("SELECT occupied_count FROM rooms WHERE id = ?", (room_pk,)).fetchone()[0], 1
            )
        changed = self.client.post(
            "/allocation",
            data={"student_id": str(student_pk), "room_id": str(replacement_room_pk)},
            follow_redirects=True,
        )
        self.assertIn(b"Room allocation saved", changed.data)
        with get_db() as db:
            self.assertEqual(
                db.execute("SELECT occupied_count FROM rooms WHERE id = ?", (room_pk,)).fetchone()[0], 0
            )
            self.assertEqual(
                db.execute(
                    "SELECT occupied_count FROM rooms WHERE id = ?", (replacement_room_pk,)
                ).fetchone()[0],
                1,
            )
            allocation_id = db.execute(
                "SELECT id FROM allocations WHERE student_id = ?", (student_pk,)
            ).fetchone()["id"]

        self.client.post(f"/allocation/{allocation_id}/remove", follow_redirects=True)
        with get_db() as db:
            self.assertEqual(
                db.execute("SELECT occupied_count FROM rooms WHERE id = ?", (room_pk,)).fetchone()[0], 0
            )
            self.assertEqual(
                db.execute(
                    "SELECT occupied_count FROM rooms WHERE id = ?", (replacement_room_pk,)
                ).fetchone()[0],
                0,
            )

        with self.client.session_transaction() as session:
            session.clear()
        student_login = self.client.post(
            "/login", data={"role": "student", "username": "student", "password": "student123"}, follow_redirects=True
        )
        self.assertIn(b"Hello, Aarav", student_login.data)
        self.assertIn(b"ROOMMATES", student_login.data)
        complaint_response = self.client.post(
            "/complaints",
            data={"category": "Wi-Fi", "description": "Please check this test connection."},
            follow_redirects=True,
        )
        self.assertIn(b"Please check this test connection.", complaint_response.data)
        with get_db() as db:
            complaint_id = db.execute(
                "SELECT id FROM complaints WHERE description = 'Please check this test connection.'"
            ).fetchone()["id"]

        with self.client.session_transaction() as session:
            session.clear()
        admin_login = self.client.post(
            "/login", data={"role": "admin", "username": "admin", "password": "admin123"}, follow_redirects=True
        )
        self.assertIn(b"Dashboard", admin_login.data)
        status_response = self.client.post(
            "/complaints",
            data={"complaint_id": str(complaint_id), "status": "Resolved"},
            follow_redirects=True,
        )
        self.assertIn(b"Resolved", status_response.data)

        self.client.post(f"/students/{student_pk}/delete", follow_redirects=True)
        with get_db() as db:
            self.assertIsNone(db.execute("SELECT id FROM students WHERE id = ?", (student_pk,)).fetchone())
        deleted_room = self.client.post(f"/rooms/{room_pk}/delete", follow_redirects=True)
        self.assertIn(b"Room deleted", deleted_room.data)
        with get_db() as db:
            self.assertIsNone(db.execute("SELECT id FROM rooms WHERE id = ?", (room_pk,)).fetchone())

    def test_fee_management_payments_and_student_data_isolation(self):
        admin = self.client.post(
            "/login", data={"role": "admin", "username": "admin", "password": "admin123"}, follow_redirects=True
        )
        self.assertIn(b"Fee collection overview", admin.data)

        fee_list = self.client.get("/fees")
        self.assertEqual(fee_list.status_code, 200)
        self.assertIn(b"Paid vs pending students", fee_list.data)
        self.assertIn(b"Monthly fee collection", fee_list.data)
        self.assertIn(b"STU001", fee_list.data)

        summary_response = self.client.get("/api/admin/fees/summary")
        self.assertEqual(summary_response.status_code, 200)
        summary = summary_response.get_json()
        self.assertGreaterEqual(summary["students"], 4)
        self.assertEqual(len(summary["monthly_collections"]), 6)
        self.assertGreaterEqual(summary["collected"], 25000)

        with get_db() as db:
            student_id = db.execute(
                "SELECT id FROM students WHERE student_id = 'STU004'"
            ).fetchone()["id"]
        due_date = (date.today() + timedelta(days=30)).isoformat()
        created = self.client.post(
            "/fees/add",
            data={
                "student_id": str(student_id),
                "period": "Automated test cycle",
                "fee_amount": "15000.00",
                "due_date": due_date,
            },
            follow_redirects=True,
        )
        self.assertIn(b"Fee record created", created.data)
        duplicate_period = self.client.post(
            "/fees/add",
            data={
                "student_id": str(student_id),
                "period": "Automated test cycle",
                "fee_amount": "15000",
                "due_date": due_date,
            },
            follow_redirects=True,
        )
        self.assertIn(b"already has a fee record", duplicate_period.data)
        invalid_fee = self.client.post(
            "/fees/add",
            data={
                "student_id": str(student_id),
                "period": "Invalid test cycle",
                "fee_amount": "-5",
                "due_date": due_date,
            },
            follow_redirects=True,
        )
        self.assertIn(b"Fee amount must be zero or more", invalid_fee.data)
        with get_db() as db:
            fee_id = db.execute(
                "SELECT id FROM fee_records WHERE student_id = ? AND period = 'Automated test cycle'",
                (student_id,),
            ).fetchone()["id"]

        detail = self.client.get(f"/fees/{fee_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Record a payment", detail.data)
        api_detail = self.client.get(f"/api/admin/fees/{fee_id}")
        self.assertEqual(api_detail.status_code, 200)
        self.assertEqual(api_detail.get_json()["fee"]["pending_amount"], 15000)

        invalid_payment_date = self.client.post(
            f"/fees/{fee_id}/payments",
            data={
                "amount": "100",
                "payment_date": "not-a-date",
                "payment_method": "Cash",
            },
            follow_redirects=True,
        )
        self.assertIn(b"valid payment date", invalid_payment_date.data)
        partial = self.client.post(
            f"/fees/{fee_id}/payments",
            data={
                "amount": "5000",
                "payment_date": date.today().isoformat(),
                "payment_method": "UPI",
                "transaction_id": "TXN-TEST-PART-001",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Payment of", partial.data)
        duplicate_transaction = self.client.post(
            f"/fees/{fee_id}/payments",
            data={
                "amount": "100",
                "payment_date": date.today().isoformat(),
                "payment_method": "Cash",
                "transaction_id": "TXN-TEST-PART-001",
            },
            follow_redirects=True,
        )
        self.assertIn(b"already been used", duplicate_transaction.data)
        overpayment = self.client.post(
            f"/fees/{fee_id}/payments",
            data={
                "amount": "10001",
                "payment_date": date.today().isoformat(),
                "payment_method": "Cash",
            },
            follow_redirects=True,
        )
        self.assertIn(b"cannot exceed", overpayment.data)
        with get_db() as db:
            self.assertEqual(
                db.execute("SELECT paid_amount FROM fee_records WHERE id = ?", (fee_id,)).fetchone()[0],
                5000,
            )

        mark_paid = self.client.post(
            f"/fees/{fee_id}/payments",
            data={
                "amount": "1",
                "mark_remaining": "yes",
                "payment_date": date.today().isoformat(),
                "payment_method": "Bank transfer",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Balance paid", mark_paid.data)
        with get_db() as db:
            fee = db.execute(
                "SELECT paid_amount, pending_amount, status FROM fee_records WHERE id = ?", (fee_id,)
            ).fetchone()
            payment_count = db.execute(
                "SELECT COUNT(*) FROM fee_payments WHERE fee_record_id = ?", (fee_id,)
            ).fetchone()[0]
        self.assertEqual(tuple(fee), (15000, 0, "PAID"))
        self.assertEqual(payment_count, 2)

        with self.client.session_transaction() as session:
            session.clear()
        with get_db() as db:
            own_fee_id = db.execute(
                "SELECT id FROM fee_records WHERE student_id = 1 ORDER BY period DESC, id DESC LIMIT 1"
            ).fetchone()["id"]
            db.execute(
                """UPDATE fee_records SET paid_amount = 0, pending_amount = fee_amount,
                   due_date = ?, status = 'NOT PAID' WHERE id = ?""",
                ((date.today() - timedelta(days=1)).isoformat(), own_fee_id),
            )
        student_login = self.client.post(
            "/login", data={"role": "student", "username": "student", "password": "student123"}, follow_redirects=True
        )
        self.assertIn(b"Your fee summary", student_login.data)
        self.assertIn(b"Fee Overdue", student_login.data)
        student_api = self.client.get("/api/student/fees")
        self.assertEqual(student_api.status_code, 200)
        student_fees = student_api.get_json()
        self.assertTrue(student_fees["fees"])
        self.assertTrue(all(record["student_id"] == 1 for record in student_fees["fees"]))
        self.assertNotIn("Automated test cycle", str(student_fees))
        self.assertEqual(student_fees["fees"][0]["computed_status"], "OVERDUE")
        self.assertEqual(self.client.get(f"/fees/{fee_id}").status_code, 302)
        self.assertEqual(self.client.get("/api/admin/fees/summary").status_code, 302)

    def test_room_change_request_lifecycle_and_access_control(self):
        with get_db() as db:
            sample_statuses = {
                row["status"]: row["total"]
                for row in db.execute(
                    """SELECT status, COUNT(*) AS total FROM room_change_requests
                       GROUP BY status"""
                ).fetchall()
            }
            student = db.execute(
                """SELECT s.id, s.student_id, a.room_id, r.room_number
                   FROM students s JOIN allocations a ON a.student_id = s.id
                   JOIN rooms r ON r.id = a.room_id WHERE s.student_id = 'ST019'"""
            ).fetchone()
        self.assertGreaterEqual(sample_statuses.get("PENDING", 0), 1)
        self.assertGreaterEqual(sample_statuses.get("APPROVED", 0), 1)
        self.assertGreaterEqual(sample_statuses.get("REJECTED", 0), 1)
        self.assertIsNotNone(student)

        student_login = self.client.post(
            "/login",
            data={"role": "student", "username": "ST019", "password": "student123"},
            follow_redirects=True,
        )
        self.assertIn(b"Room Change Request", student_login.data)
        request_page = self.client.get("/student/room-change-requests")
        self.assertEqual(request_page.status_code, 200)
        self.assertIn(b"ST019", request_page.data)
        self.assertIn(student["room_number"].encode(), request_page.data)
        rooms_response = self.client.get("/api/rooms/available?student_id=1")
        self.assertEqual(rooms_response.status_code, 200)
        available_rooms = rooms_response.get_json()["rooms"]
        self.assertTrue(available_rooms)
        self.assertNotIn(student["room_id"], [room["id"] for room in available_rooms])
        target = available_rooms[0]
        invalid_reason = self.client.post(
            "/api/room-change-requests",
            json={
                "requested_room_id": target["id"],
                "reason": "A reason not in the form",
                "description": "This should be rejected.",
            },
        )
        self.assertEqual(invalid_reason.status_code, 400)
        current_room_request = self.client.post(
            "/api/room-change-requests",
            json={
                "requested_room_id": student["room_id"],
                "reason": "Other",
                "description": "The current room cannot be selected.",
            },
        )
        self.assertEqual(current_room_request.status_code, 400)
        payload = {
            "student_id": 1,
            "requested_room_id": target["id"],
            "reason": "Roommate Issue",
            "description": "The current room arrangement makes it difficult to rest.",
        }
        submitted = self.client.post("/api/room-change-requests", json=payload)
        self.assertEqual(submitted.status_code, 201)
        request_id = submitted.get_json()["id"]
        self.assertEqual(submitted.get_json()["status"], "PENDING")
        duplicate = self.client.post("/api/room-change-requests", json=payload)
        self.assertEqual(duplicate.status_code, 409)
        own_requests = self.client.get(
            "/api/room-change-requests/my?student_id=1"
        ).get_json()["requests"]
        self.assertTrue(own_requests)
        self.assertTrue(all(row["student_id"] == student["id"] for row in own_requests))
        self.assertEqual(self.client.get("/api/admin/room-change-requests").status_code, 302)
        self.assertEqual(
            self.client.patch(
                f"/api/admin/room-change-requests/{request_id}/approve", json={}
            ).status_code,
            302,
        )

        with self.client.session_transaction() as session:
            session.clear()
        self.client.post(
            "/login",
            data={"role": "admin", "username": "admin", "password": "admin123"},
            follow_redirects=True,
        )
        admin_list = self.client.get("/room-change-requests?q=ST019&status=PENDING")
        self.assertEqual(admin_list.status_code, 200)
        self.assertIn(b"ST019", admin_list.data)
        self.assertIn(b"Most requested rooms", admin_list.data)
        detail = self.client.get(f"/room-change-requests/{request_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"difficult to rest", detail.data)
        self.assertEqual(
            self.client.get(f"/api/admin/room-change-requests/{request_id}").status_code,
            200,
        )

        with get_db() as db:
            old_room_before = db.execute(
                "SELECT occupied_count FROM rooms WHERE id = ?", (student["room_id"],)
            ).fetchone()[0]
            target_count_before = db.execute(
                "SELECT occupied_count FROM rooms WHERE id = ?", (target["id"],)
            ).fetchone()[0]
            db.execute(
                "UPDATE rooms SET occupied_count = capacity WHERE id = ?", (target["id"],)
            )
        full_room_approval = self.client.patch(
            f"/api/admin/room-change-requests/{request_id}/approve", json={}
        )
        self.assertEqual(full_room_approval.status_code, 409)
        self.assertIn("no longer available", full_room_approval.get_json()["error"])
        with get_db() as db:
            self.assertEqual(
                db.execute(
                    "SELECT status FROM room_change_requests WHERE id = ?", (request_id,)
                ).fetchone()["status"],
                "PENDING",
            )
            db.execute(
                "UPDATE rooms SET occupied_count = ? WHERE id = ?",
                (target_count_before, target["id"]),
            )

        approval = self.client.post(
            f"/api/admin/room-change-requests/{request_id}/approve",
            data={"admin_comment": "Approved after checking available beds."},
        )
        self.assertEqual(approval.status_code, 302)
        with get_db() as db:
            moved_allocation = db.execute(
                "SELECT room_id FROM allocations WHERE student_id = ?", (student["id"],)
            ).fetchone()["room_id"]
            old_room_after = db.execute(
                "SELECT occupied_count FROM rooms WHERE id = ?", (student["room_id"],)
            ).fetchone()[0]
            target_count_after = db.execute(
                "SELECT occupied_count FROM rooms WHERE id = ?", (target["id"],)
            ).fetchone()[0]
            approved_status = db.execute(
                "SELECT status FROM room_change_requests WHERE id = ?", (request_id,)
            ).fetchone()["status"]
        self.assertEqual(moved_allocation, target["id"])
        self.assertEqual(old_room_after, old_room_before - 1)
        self.assertEqual(target_count_after, target_count_before + 1)
        self.assertEqual(approved_status, "APPROVED")
        approved_twice = self.client.patch(
            f"/api/admin/room-change-requests/{request_id}/approve", json={}
        )
        self.assertEqual(approved_twice.status_code, 409)

        with self.client.session_transaction() as session:
            session.clear()
        self.client.post(
            "/login",
            data={"role": "student", "username": "ST019", "password": "student123"},
        )
        next_available = self.client.get("/api/rooms/available").get_json()["rooms"]
        self.assertNotIn(target["id"], [room["id"] for room in next_available])
        rejected_target = next_available[0]
        rejected_submit = self.client.post(
            "/student/room-change-requests",
            data={
                "requested_room_id": rejected_target["id"],
                "reason": "Maintenance Problem",
                "description": "The current room needs maintenance work.",
            },
            follow_redirects=True,
        )
        self.assertEqual(rejected_submit.status_code, 200)
        self.assertIn(b"Waiting for admin approval", rejected_submit.data)
        with get_db() as db:
            rejected_id = db.execute(
                """SELECT id FROM room_change_requests
                   WHERE student_id = ? AND status = 'PENDING'
                   ORDER BY id DESC LIMIT 1""",
                (student["id"],),
            ).fetchone()["id"]
        room_before_rejection = moved_allocation

        with self.client.session_transaction() as session:
            session.clear()
        self.client.post(
            "/login",
            data={"role": "admin", "username": "admin", "password": "admin123"},
        )
        with get_db() as db:
            current_count_before_rejection = db.execute(
                "SELECT occupied_count FROM rooms WHERE id = ?", (room_before_rejection,)
            ).fetchone()[0]
            target_count_before_rejection = db.execute(
                "SELECT occupied_count FROM rooms WHERE id = ?", (rejected_target["id"],)
            ).fetchone()[0]
        missing_reason = self.client.patch(
            f"/api/admin/room-change-requests/{rejected_id}/reject", json={}
        )
        self.assertEqual(missing_reason.status_code, 400)
        rejection = self.client.post(
            f"/api/admin/room-change-requests/{rejected_id}/reject",
            data={"rejection_reason": "The maintenance work is already scheduled."},
        )
        self.assertEqual(rejection.status_code, 302)
        with get_db() as db:
            rejected_status = db.execute(
                "SELECT status, admin_comment FROM room_change_requests WHERE id = ?",
                (rejected_id,),
            ).fetchone()
            current_room_after_rejection = db.execute(
                "SELECT room_id FROM allocations WHERE student_id = ?", (student["id"],)
            ).fetchone()["room_id"]
            current_count_after_rejection = db.execute(
                "SELECT occupied_count FROM rooms WHERE id = ?", (room_before_rejection,)
            ).fetchone()[0]
            target_count_after_rejection = db.execute(
                "SELECT occupied_count FROM rooms WHERE id = ?", (rejected_target["id"],)
            ).fetchone()[0]
        self.assertEqual(rejected_status["status"], "REJECTED")
        self.assertEqual(
            rejected_status["admin_comment"], "The maintenance work is already scheduled."
        )
        self.assertEqual(current_room_after_rejection, room_before_rejection)
        self.assertEqual(
            current_count_after_rejection, current_count_before_rejection
        )
        self.assertEqual(target_count_after_rejection, target_count_before_rejection)
        rejected_twice = self.client.patch(
            f"/api/admin/room-change-requests/{rejected_id}/reject",
            json={"rejection_reason": "A second attempt."},
        )
        self.assertEqual(rejected_twice.status_code, 409)


if __name__ == "__main__":
    unittest.main()
