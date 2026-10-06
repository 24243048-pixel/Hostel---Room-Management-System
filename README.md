# Hostel Room Management System

A small, beginner-friendly web application for managing hostel students, rooms, allocations, maintenance complaints, and hostel fees. It uses Flask for the server, server-rendered HTML/CSS/JavaScript for the interface, and SQLite for persistent storage. The application creates its database and demo records automatically the first time it starts.

## Features

- Admin/warden and student sign-in with role-based dashboards.
- Student directory with create, view, edit, delete, and search.
- Room directory with duplicate checks and automatically calculated available beds.
- Room allocation, room changes, and allocation removal, with checks that prevent full rooms and duplicate allocations.
- Student room-change requests with database-backed available-room choices, request history, and one active request per student.
- Admin room-change review with searchable/filterable requests and transactional approval/rejection.
- Student view of personal details, room, roommates, and complaint history.
- Student complaint submission and admin status updates.
- Fee records per student and semester/month period, with due-date, balance, and overdue tracking.
- Admin fee dashboard with dynamic paid/pending summaries, paid-vs-pending and monthly collection charts, search and filters, fee details, and payment recording.
- Student fee summary, due/overdue reminders, and private payment history.
- Daily attendance register with Present, Absent, Late, and Leave statuses.
- Admin attendance dashboard with six-month trends, status summaries, student search, and configurable low-attendance list.
- Attendance search, room filtering, monthly/student attendance summaries, report filters, and CSV export.
- Student-only attendance history and a role-protected attendance JSON API.
- Role selection at login and individual student portal accounts for the 40 sample attendance students.
- Input validation for required fields, email, phone, room capacity, and unique identifiers.
- Responsive layout with a purple/blue theme.

## Technology stack

- Python 3.9 or later
- Flask
- SQLite (included with Python)
- HTML, CSS, and vanilla JavaScript

## Project structure

```text
hostel-room-management/
├── app.py
├── database.py
├── requirements.txt
├── README.md
├── .gitignore
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── admin_dashboard.html
│   ├── student_dashboard.html
│   ├── students.html
│   ├── add_student.html
│   ├── edit_student.html
│   ├── rooms.html
│   ├── add_room.html
│   ├── edit_room.html
│   ├── allocation.html
│   ├── complaints.html
│   ├── fees.html
│   ├── add_fee.html
│   ├── fee_details.html
│   ├── attendance.html
│   ├── attendance_reports.html
│   ├── student_attendance.html
│   ├── student_room_change.html
│   ├── admin_room_change_requests.html
│   ├── admin_room_change_request_detail.html
│   ├── _student_form.html
│   └── _room_form.html
├── static/
│   ├── style.css
│   └── script.js
└── tests/
    └── test_workflows.py
```

The `tests/` folder contains end-to-end workflow smoke tests and is optional for running the app.

## Installation and run (Windows 10/11)

Open the project folder in VS Code. In the integrated terminal, choose **Command Prompt** and run these commands from the project folder:

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

If your VS Code terminal is PowerShell instead, activate the environment with:

```powershell
.\venv\Scripts\Activate.ps1
```

If Windows blocks script activation in PowerShell, switch the VS Code terminal profile to Command Prompt and use the four commands above. No paid service or external API is needed.

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser. Stop the local server with `Ctrl+C`.

The SQLite file `hostel.db` is created in the project folder when the application starts. Existing databases are upgraded automatically with fee and room-change request tables; existing hostel data is preserved. The first launch inserts sample rooms, students, allocations, complaints, demo fee records, and room-change request examples where the matching demo students and rooms are available.

## Fee management

Sign in as the admin and select **Fee Management** in the sidebar. Create one fee record per student and billing period, then open **View details** to record a payment or mark the remaining balance as paid. Transaction IDs are generated automatically when none is supplied. Payment records are append-only and a transaction ID can only be used once.

The initial demo data uses a fee amount of ₹25,000 per student. It includes one paid account (STU001), one partial payment (STU002), and unpaid accounts (STU003/STU004). In addition, a separate attendance sample billing period is created for ST001–ST040: 30 students are marked PAID and 10 are NOT PAID. Paid rows receive payment dates and transaction history; unpaid rows do not receive fake payment dates. The sample period is added only when a student's record for that period is missing, so existing fee data is preserved.

The migration adds `fee_records` (per student and fee period, including total/paid/pending amounts, due date, latest payment details, status, and timestamps) and `fee_payments` (one row per transaction, including amount, date, unique transaction ID, method, status, and its fee-record relationship). Pending balances are recalculated for every recorded payment. A fee becomes overdue when its due date has passed and a balance remains; dashboard and API reads persist this time-based status. Student reminders change automatically, with a due-soon reminder shown within 14 days of the due date.

### Attendance management

On startup, the app adds the supplied attendance test students only when their Student ID is not already present. It preserves existing student rows and allocations. Newly inserted attendance students receive a `student123` starter password using their Student ID as the username, plus a room/allocation from their sample row so room filtering works; existing student IDs are skipped without changes. Sample rooms use the exact provided room numbers. Student accounts are created only when a matching student login is missing. The 40 demo rows are held as database initialization data, not as frontend records.

Attendance records are stored in the `attendance` table with a unique student/date pair, status, optional note, marking user, and timestamps. Six months of weekday sample records with varied attendance rates are inserted for ST001–ST040 using insert-if-missing semantics. Admins can mark or update each date's register, filter it by name/ID and room, and view dashboard trends and reports by date range, status, student, and room. Reports include students with no recorded attendance, show their zero totals, and can be exported as CSV. Students can only view their own attendance summary/history and calendar. No attendance reminder or warning notifications are included.

The attendance-related JSON endpoints use the current login session:

| Method and path | Access | Purpose |
| --- | --- | --- |
| `GET /api/admin/attendance?date=YYYY-MM-DD` | Admin | Retrieve a day's student roster; accepts `q` and `room` filters. |
| `POST /api/admin/attendance` | Admin | Save validated attendance records as JSON `{ "date": "YYYY-MM-DD", "records": [{ "student_id": 1, "status": "Present", "notes": "" }] }`. |
| `GET /api/student/attendance` | Signed-in student | Retrieve only the current student's attendance history. |

### Room change requests

Students open **Room Change Request** from their dashboard or sidebar. The form displays the logged-in student's actual allocation and rooms with open beds from the existing `rooms` table; the current room is excluded and available capacity is fetched again when the student submits. The existing room schema has no separate room-type column, so the interface labels room type from its capacity (for example, “2 sharing”) and displays block/floor as stored. Only the signed-in student's ID is used by the backend. Students can see only their own requests and cannot directly change their allocation.

The admin **Room Change Requests** page includes live totals by status, filters for student/name/ID, current room, requested room, status, and date, plus summaries of popular requested rooms and reasons. A request detail page shows the student's current allocation and current requested-room capacity. Admin approval revalidates the request, current allocation, and target capacity while holding a SQLite write transaction; it changes the existing `allocations` row and adjusts both existing room occupancy counters atomically. A full target remains pending and is not approved. Rejection requires a reason and does not alter room allocation or occupancy.

The `room_change_requests` table references the existing `students`, `rooms`, and `users` tables. It records the current and requested room IDs, reason, description, `PENDING`/`APPROVED`/`REJECTED` state, optional admin response, and request/review timestamps. Room deletion is blocked when the room is referenced by request history so past requests remain readable.

| Method and path | Access | Purpose |
| --- | --- | --- |
| `GET /student/room-change-requests` | Signed-in student | Request form and the student's own request history. |
| `GET /api/rooms/available` | Signed-in student | Rooms with available beds, excluding their current room. |
| `POST /api/room-change-requests` | Signed-in student | Submit JSON `{ "requested_room_id": 1, "reason": "Roommate Issue", "description": "..." }`. |
| `GET /api/room-change-requests/my` | Signed-in student | Return only the signed-in student's requests. |
| `GET /room-change-requests` | Admin | Filtered request list and live summary reports. |
| `GET /room-change-requests/<id>` | Admin | Detailed request review page. |
| `GET /api/admin/room-change-requests` | Admin | List requests with optional query filters. |
| `GET /api/admin/room-change-requests/<id>` | Admin | Retrieve request and current room availability details. |
| `POST` or `PATCH /api/admin/room-change-requests/<id>/approve` | Admin | Recheck and approve, atomically changing room assignment and occupancy. |
| `POST` or `PATCH /api/admin/room-change-requests/<id>/reject` | Admin | Reject with a required `rejection_reason` or `admin_comment`. |

On an empty request table, startup adds representative pending, approved, and rejected demo requests when suitable sample student allocations and rooms exist. The approved example is historical and does not change current allocations during initialization.

### Session-authenticated JSON endpoints

| Method and path | Access | Purpose |
| --- | --- | --- |
| `GET /api/admin/fees/summary` | Admin | Fee totals and six-month payment collection series for the charts. |
| `GET /api/admin/fees/<fee_id>` | Admin | One fee record and its payment history. |
| `GET /api/student/fees` | Signed-in student | Only that student's fee records and payment history. |

There is no online payment processor. Admins record payments received through cash, UPI, transfer, card, or another offline method.

Run the workflow tests from the project folder with:

```bat
python -m unittest discover -s tests -v
```

## Demo login credentials

| Role | Username | Password |
| --- | --- | --- |
| Admin / Warden | `admin` | `admin123` |
| Student demo account | `student` | `student123` |
| Attendance sample student | `ST001`–`ST040` | `student123` |

Choose the matching **Admin** or **Student** role on the login page. Each sample student has a separate login; for example, ST001 signs in as `ST001` with password `student123`. New students added through student management receive a portal username matching their Student ID and the initial password `student123`.

## Future enhancements

- Add password changes, password reset, and stronger production secret management.
- Add pagination and CSV import/export for larger student and room lists.
- Add email or in-app notifications when a complaint status changes.
- Add audit history and role-based warden accounts.
- Add automated database backups and deployment configuration.
