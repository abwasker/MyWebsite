# Local Setup Tasks

## System setup

- [x] Confirm Git is installed.
  - Verified: `git version 2.53.0.windows.1`
- [x] Clone the repository locally.
  - Cloned from `https://github.com/abwasker/MyWebsite.git`
- [x] Create a working branch for future pull requests.
  - Branch: `codex/local-setup-and-scope`
- [x] Confirm the Python 3 executable path for this workspace.
  - User shell confirms Python 3.12.10 can run.
  - Path: `C:\Users\Anosh\AppData\Local\Programs\Python\Python312\python.exe`
- [x] Create and activate a virtual environment.
  - Created at `.venv`
- [x] Install project dependencies.
  - Installed from `requirements.txt`
- [x] Verify the Django app runs locally.
  - Started with `python manage.py runserver 127.0.0.1:8000 --noreload`
  - Verified homepage returns `200 OK` at `http://127.0.0.1:8000/`

## Admin user setup

- Create the first admin user from the repository root:
  ```powershell
  .\.venv\Scripts\python.exe .\Website\manage.py createsuperuser
  ```
- Start the local server, visit `http://127.0.0.1:8000/admin/`, and log in with that account.
- Use Django admin to create and manage any additional user accounts. Public signup is disabled for v1.

## Database setup

- [x] Identify current database configuration.
  - The app currently uses SQLite at `Website/db.sqlite3`.
- [x] Add and apply the initial `blog` migration locally.
  - Added `blog/migrations/0001_initial.py`.
  - Applied successfully to the local SQLite database.
- [ ] Decide whether to keep SQLite for local development or move to MySQL.
- [ ] Install MySQL if the project will use MySQL.
  - MySQL was not found in PATH and no MySQL Windows service was detected.
- [ ] Choose and install a SQL GUI.
  - DBeaver is a good cross-database choice for SQLite and MySQL.
- [ ] If moving to MySQL, add environment-based database settings.
  - Keep credentials out of Git.

## Project scope draft

The current site appears to be a personal portfolio and writing site. It should:

- Present a landing page with the newest posts.
- Show portfolio information, skills, experience, and projects.
- Provide a password-protected resume page.
- Publish static blog posts.
- Publish database-backed poetry entries.
- Support user signup, login, and logout.
- Let authenticated users submit comments on posts.
- Hold comments for admin approval before public display.

## Near-term product questions

- Should blog posts remain hard-coded in Python, or move into the database/admin?
- Should poems and posts share one content model?
- Should resume access stay password-based, or use authenticated users/groups?
- Should comments support moderation notifications?
- Should the site be prepared for deployment with environment variables and production settings?
