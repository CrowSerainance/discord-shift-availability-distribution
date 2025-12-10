````markdown
# Shift Management Bot (PostgreSQL / discord.py)

A Discord bot for managing moderator shift coverage with:

- Database-backed schedules (no hardcoded shifts)
- DST-aware timezone support
- Schedule management via slash commands
- Fairness system to prevent shift hogging
- Persistent "Claim shift" buttons that survive bot restarts

Built for deployment on services like Render with a managed PostgreSQL DB.

---

## Features

- **Database-stored schedules**
  - Moderators configure their weekly schedule via commands; stored in `mod_schedules`.
- **Shift lifecycle**
  - Drop generic shifts or scheduled mod shifts.
  - Other mods claim via a persistent button.
  - Shifts can be edited (if unclaimed) or cancelled.
- **Fairness / anti-hogging**
  - Tracks hours claimed in the last 7 days.
  - “Heavy” users can only claim when close to shift start.
- **Timezone & DST aware**
  - Each schedule slot stores an IANA timezone (e.g. `America/New_York`).
  - Next-occurence calculation uses `ZoneInfo`, handles DST automatically.
- **Role-based permissions**
  - Everyone: view stats & schedules, claim shifts.
  - Mods: manage their own schedules & shifts.
  - Admins: manage any schedule/shift and sync commands.
- **Persistent UI**
  - Claim button is implemented as a persistent `discord.ui.View`, re-registered on startup.

---

## Tech Stack

- **Language:** Python 3.10+
- **Libraries:**
  - `discord.py` 2.x
  - `psycopg2-binary`
  - `python-dotenv`
  - `zoneinfo` (standard library)
- **Database:** PostgreSQL (Supabase, Neon, Render managed Postgres, etc.)

Example `requirements.txt`:

```txt
discord.py>=2.0.0
psycopg2-binary>=2.9.0
python-dotenv>=1.0.0
````

---

## File Structure

```text
DISCORD BOT/
├── bot.py              # Main bot (PostgreSQL + DST-aware)
├── requirements.txt    # Dependencies
└── DOCUMENTATION.md    # Extended technical docs (schema, commands, workflows)
```

---

## Configuration (in `bot.py`)

At the top of `bot.py` there is a configuration section:

```python
# Discord / roles / channel
ALLOWED_CHANNEL_ID = 1445805874382901388  # Channel where shift commands may be used
MOD_ROLE_ID        = 1445982976809767002  # Moderator role ID
ADMIN_ROLE_ID      = 1445985907093012561  # Admin role ID

# Fairness settings
MAX_HOURS_7D = 3.0                  # Max hours in last 7 days before user is "heavy"
HEAVY_LOCK_WINDOW_MINUTES = 60      # Heavy mods can only claim if start < 60 min away

# Shift duration limits
MIN_DURATION_HOURS = 0.25           # Min 15 minutes
MAX_DURATION_HOURS = 24.0           # Max 24 hours

# Default timezone for new schedules
DEFAULT_TIMEZONE = "UTC"

# Admin schedule management
RESTRICT_ADMIN_SCHEDULE_TO_CHANNEL = False
```

On your own copy, replace those IDs with your server’s channel & role IDs.

---

## Environment Variables

The bot uses environment variables (e.g. `.env` locally, dashboard on Render):

| Variable        | Required | Description                             |
| --------------- | -------- | --------------------------------------- |
| `DISCORD_TOKEN` | Yes      | Bot token from Discord Developer Portal |
| `DATABASE_URL`  | Yes      | PostgreSQL connection string            |

**Postgres connection string format:**

```text
postgresql://user:password@host:port/database
```

**Example `.env` for local development:**

```env
DISCORD_TOKEN=your_discord_bot_token_here
DATABASE_URL=postgresql://user:password@localhost:5432/shifts
```

---

## Database Schema

### `shifts`

Stores all dropped shifts and their state.

| Column             | Type               | Description                         |
| ------------------ | ------------------ | ----------------------------------- |
| `id`               | SERIAL PRIMARY KEY | Auto-increment ID                   |
| `message_id`       | BIGINT UNIQUE      | Discord message ID                  |
| `channel_id`       | BIGINT             | Channel where shift was posted      |
| `description`      | TEXT               | Shift description                   |
| `created_by`       | BIGINT             | User who dropped the shift          |
| `created_at`       | TIMESTAMPTZ        | When created                        |
| `start_time_utc`   | TIMESTAMPTZ        | Shift start time (UTC)              |
| `duration_hours`   | REAL               | Duration in hours                   |
| `assigned_user_id` | BIGINT             | Original moderator (for mod shifts) |
| `claimed_by`       | BIGINT             | User who claimed the shift          |
| `claimed_at`       | TIMESTAMPTZ        | When claimed                        |
| `cancelled`        | BOOLEAN            | Whether shift is cancelled          |

### `mod_schedules`

Stores weekly time slots per moderator.

| Column        | Type               | Description            |
| ------------- | ------------------ | ---------------------- |
| `id`          | SERIAL PRIMARY KEY | Auto-increment ID      |
| `user_id`     | BIGINT NOT NULL    | Discord user ID        |
| `day_of_week` | INTEGER NOT NULL   | 0 = Monday, 6 = Sunday |
| `hour`        | INTEGER NOT NULL   | Hour (0–23)            |
| `minute`      | INTEGER DEFAULT 0  | Minute (0–59)          |
| `timezone`    | TEXT DEFAULT 'UTC' | IANA timezone string   |
| `created_at`  | TIMESTAMPTZ        | When slot was created  |

**Unique constraint:** `(user_id, day_of_week, hour, minute)`

---

## Commands

### Schedule Management

| Command               | Description                               | Who can use                  |
| --------------------- | ----------------------------------------- | ---------------------------- |
| `/schedule_add`       | Add a time slot to **your** schedule      | Moderators                   |
| `/schedule_add_admin` | Add a schedule slot for another moderator | Admins                       |
| `/schedule_remove`    | Remove a time slot from **your** schedule | Moderators                   |
| `/schedule_view`      | View a user’s schedule                    | Anyone                       |
| `/schedule_clear`     | Clear all slots (self or another user)    | Self: Mods, Any user: Admins |

### Shift Commands

| Command           | Description                                         | Who can use                        |
| ----------------- | --------------------------------------------------- | ---------------------------------- |
| `/drop_shift`     | Post a generic shift with a claim button            | Moderators                         |
| `/drop_mod_shift` | Drop a scheduled shift from a moderator’s schedule  | Mods (own), Admins (any moderator) |
| `/shift_edit`     | Edit an unclaimed shift (description/time/duration) | Shift owner or Admin               |
| `/shift_cancel`   | Cancel a shift (claimed or unclaimed)               | Shift owner or Admin               |
| `/shift_stats`    | View shift count & total claimed hours for a user   | Anyone                             |
| `/sync_commands`  | Sync slash commands with Discord                    | Admins                             |

### Fallback Prefix Commands

| Command | Description                    |
| ------- | ------------------------------ |
| `!ping` | Check if the bot is responsive |
| `!sync` | Manually sync slash commands   |

---

## Fairness System

To stop one mod from hoarding all the coverage:

1. Bot sums `duration_hours` for **claimed, non-cancelled** shifts in the last 7 days for each user.
2. If `total_hours >= MAX_HOURS_7D` (default: `3.0`), user is considered **heavy**.
3. Heavy users can only claim shifts where the start time is within `HEAVY_LOCK_WINDOW_MINUTES` (default: 60) of now.
4. If they try to claim earlier, the bot rejects the claim and shows their current total hours.

---

## Timezone & DST Handling

* Each schedule slot stores:

  * `day_of_week` (0–6)
  * `hour` / `minute`
  * `timezone` (IANA name, e.g. `America/New_York`)
* The bot:

  * Converts "day/hour/minute in timezone" to the **next upcoming** datetime using `ZoneInfo`.
  * Converts that to UTC for storage & comparison.
* DST transitions (e.g. EST ↔ EDT) are handled automatically; the moderator’s local schedule stays consistent.

Common timezones supported via autocomplete:

* `UTC`
* `America/New_York`
* `America/Chicago`
* `America/Denver`
* `America/Los_Angeles`
* `Europe/London`
* `Europe/Paris`
* `Asia/Tokyo`
* `Australia/Sydney`

Any valid IANA timezone works.

---

## Local Setup

1. **Clone / copy the files**

   Put `bot.py`, `requirements.txt`, and `DOCUMENTATION.md` into a folder, e.g.:

   ```text
   DISCORD BOT/
   ├── bot.py
   ├── requirements.txt
   └── DOCUMENTATION.md
   ```

2. **Create virtual environment (optional but recommended)**

   ```bash
   python -m venv .venv
   # Windows
   .venv\Scripts\activate
   # macOS / Linux
   source .venv/bin/activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Set environment variables**

   Either export them in your shell or create a `.env` file:

   ```env
   DISCORD_TOKEN=your_discord_bot_token_here
   DATABASE_URL=postgresql://user:password@localhost:5432/shifts
   ```

5. **Configure IDs in `bot.py`**

   * `ALLOWED_CHANNEL_ID`
   * `MOD_ROLE_ID`
   * `ADMIN_ROLE_ID`
   * Any fairness / duration / timezone settings you want to change.

6. **Run the bot**

   ```bash
   python bot.py
   ```

   On startup, the bot will:

   * Initialize `shifts` and `mod_schedules` tables if they don’t exist.
   * Register the persistent `ShiftClaimView` for the claim button.
   * Log in as your bot user.

7. **Sync slash commands**

   In Discord, once the bot is online:

   * Use `/sync_commands` (admin), or
   * Use `!sync` if you prefer the prefix command.

Slash commands may take 1–2 minutes to fully propagate in the client.

---

## Deployment (e.g. Render + PostgreSQL)

1. **Database**

   * Create a PostgreSQL instance (Render, Supabase, Neon, etc.).
   * Copy the **connection string** and use it as `DATABASE_URL`.

2. **Web Service**

   * Push your bot code to a Git repository (GitHub, etc.).

   * Create a new Web Service and connect it to the repo.

   * Build command:

     ```bash
     pip install -r requirements.txt
     ```

   * Start command:

     ```bash
     python bot.py
     ```

   * Environment variables:

     * `DISCORD_TOKEN=...`
     * `DATABASE_URL=...`

3. Deploy. The bot should come online and start responding in your server.

---

## License

This bot is provided as-is for managing Discord moderator shifts.
You’re free to fork, modify, and adapt it for your own servers.

```

Cram that into your repo, commit, and go back to bullying your schedule spreadsheet into submission. 
::contentReference[oaicite:1]{index=1}
```
