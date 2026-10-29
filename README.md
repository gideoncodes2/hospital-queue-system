# Hospital Patient Appointment and Queue Management System

Patients book an appointment, get a queue number, see a live wait estimate, and receive SMS alerts.
Staff run the queue from a dashboard and time each consultation. The recorded consultation times
drive the wait estimates.

## Quick start

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo        # demo doctors, patients and a live queue for today
python manage.py runserver
```

Open http://127.0.0.1:8000

| Role    | Login                       | Where to look                        |
|---------|-----------------------------|--------------------------------------|
| Staff   | `dr.achieng` / `demo1234`   | /staff/ (queue dashboard)            |
| Patient | `0700000005` / `demo1234`   | /appointments/ (ticket + live wait)  |
| Admin   | `admin` / `admin1234`       | /admin/ (doctors, hours, departments)|

Change these passwords before showing the system to anyone outside the team.

## How the wait estimate works

1. Staff press **Start consultation** and **Finish consultation**. The system stores both timestamps.
2. For each doctor it averages the last 20 timed consultations. Until 3 have been timed, it uses the
   doctor's `default_consult_minutes` (set in the admin).
3. Wait = (waiting patients ahead x average) + time left in the consultation happening now.
   A booked patient is never told a time earlier than their slot.
4. The estimate is recalculated every time a consultation finishes.

## SMS alerts

| Message        | When it is sent                                                                 |
|----------------|---------------------------------------------------------------------------------|
| Confirmation   | On booking, or when a walk-in is issued a number                                |
| Reminder       | Day before, by `python manage.py send_reminders` (schedule once a day)          |
| Turn is near   | When the estimated wait drops to `ALERT_MINUTES_BEFORE` (default 30 min)        |
| Delay update   | When the expected time moves later by `DELAY_UPDATE_MINUTES` (default 15 min)   |

"Turn is near" and delay messages are checked whenever a consultation finishes, and also by
`python manage.py process_alerts`. Schedule that to run every minute (cron, or Task Scheduler on Windows)
so alerts also fire as time passes:

```
* * * * * cd /path/to/project && venv/bin/python manage.py process_alerts
```

By default messages are printed to the terminal. To send real SMS through Africa's Talking:

```bash
export SMS_BACKEND=africastalking
export AT_USERNAME=sandbox          # your Africa's Talking username
export AT_API_KEY=your-api-key
export AT_SANDBOX=1                 # set to 0 for live sending
```

## PostgreSQL (optional)

SQLite is used by default. For PostgreSQL: `pip install "psycopg[binary]"`, then set
`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` before running `migrate`.

## Tests

```bash
python manage.py test
```

They cover double-booking, slot release on cancellation, queue numbering, wait estimates, the
learned average, alerts (sent once, no spam, delay updates) and the main page flows.

## Project layout

```
config/                 settings, root URLs
clinic/models.py        Department, Doctor, Patient, Appointment, Notification
clinic/services.py      booking, queue status, wait estimate, SMS alerts (the core logic)
clinic/sms.py           console and Africa's Talking senders
clinic/views.py         patient pages and staff dashboard
clinic/templates/       HTML pages
clinic/management/      seed_demo, process_alerts, send_reminders
clinic/tests.py         automated tests
```

## Known limits (good material for "future work")

- Estimates use one average per doctor. New patients and follow-ups take different lengths of time,
  so separate averages per visit type would be more accurate.
- Booked patients who have not arrived still count as "ahead" until staff mark them no-show.
- Pages refresh every 30 seconds instead of updating instantly (WebSockets would fix this).
- No patient medical records, billing or NHIF integration (out of scope by design).
- Patient data is covered by the Data Protection Act, 2019. Do not load real patient data into the demo.

## Suggested split for the team

- Backend and database: `models.py`, `services.py`, migrations, PostgreSQL setup
- Frontend: templates and `style.css`, usability fixes after testing with real users
- SMS and deployment: `sms.py`, Africa's Talking sandbox, scheduled jobs, hosting
- Testing and docs: extend `tests.py`, collect timing data at a facility, write the report
