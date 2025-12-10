````markdown
# Shift Management Bot (PostgreSQL / discord.py)

A Discord bot for managing moderator shift coverage with:

- Database-stored schedules (no hardcoded mod shifts)
- DST-aware timezone support
- Schedule management via slash commands
- Fairness system to prevent shift hogging
- Persistent "Claim shift" buttons that survive bot restarts :contentReference[oaicite:0]{index=0}

---

## Features

- **Database-backed schedules**
  - All moderator schedules are stored in the `mod_schedules` table.
  - No hardcoded schedules; everything is editable via commands. :contentReference[oaicite:1]{index=1}

- **DST-aware timezones**
  - Schedules store an IANA timezone (e.g. `America/New_York`).
  - Next-occurrence calculations use `ZoneInfo` and automatically handle DST transitions. 

- **Shift lifecycle**
  - Mods can drop shifts (generic or based on their schedule).
  - Other mods can claim shifts via a persistent button.
  - Shifts can be edited (if unclaimed) or cancelled. :contentReference[oaicite:3]{index=3}

- **Fairness system**
  - Tracks total hours claimed in the last 7 days.
  - Users over the configured cap can only claim close to shift start. 

- **Role-based permissions**
  - Everyone can view schedules and stats.
  - Moderators manage their own schedule and shifts.
  - Admins can manage any schedule/shift and sync commands. :contentReference[oaicite:5]{index=5}

---

## Tech Stack

- **Language:** Python 3.10+
- **Libraries:**
  - `discord.py`
  - `psycopg2-binary`
  - `python-dotenv`
  - `zoneinfo` (standard library, Python 3.9+) :contentReference[oaicite:6]{index=6}
- **Database:** PostgreSQL (Neon, Supabase, Render managed, etc.)

Example `requirements.txt`:

```txt
discord.py>=2.0.0
psycopg2-binary>=2.9.0
python-dotenv>=1.0.0
````

---

## File Structure

````text
DISCORD BOT/
├── bot.py              # Main bot (PostgreSQL, DST-aware)
├── requirements.txt    # Dependencies
└── DOCUMENTATION.md    # Technical details & command reference
``` :contentReference[oaicite:7]{index=7}

---

## Configuration (in `bot.py`)

Edit these values near the top of `bot.py`:

```python
# The channel ID where shift commands can be used
ALLOWED_CHANNEL_ID = 123456789012345678  # Replace with your channel ID

# The role ID that can use moderator commands (drop shifts, cancel shifts)
MOD_ROLE_ID = 123456789012345679         # Replace with your moderator role ID

# The role ID that can use admin commands (sync commands, admin schedule mgmt)
ADMIN_ROLE_ID = 123456789012345680       # Replace with your admin role ID

# Fairness settings
MAX_HOURS_7D = 3.0                  # Max hours in last 7 days before being "heavy"
HEAVY_LOCK_WINDOW_MINUTES = 60      # Heavy mods can only claim if <60 min before start

# Duration limits
MIN_DURATION_HOURS = 0.25           # Minimum 15 minutes
MAX_DURATION_HOURS = 24.0           # Maximum 24 hours

# Default timezone for new schedules
DEFAULT_TIMEZONE = "UTC"
````

**Summary:**

* `ALLOWED_CHANNEL_ID`
  Channel where shift commands (`/drop_shift`, `/drop_mod_shift`, etc.) may be used.

* `MOD_ROLE_ID`
  Role allowed to manage their own schedules and shifts.

* `ADMIN_ROLE_ID`
  Role allowed to manage any schedule/shift and sync commands.

---

## Environment Variables

The bot expects:

| Variable        | Required | Description                  |
| --------------- | -------- | ---------------------------- |
| `DISCORD_TOKEN` | Yes      | Discord bot token            |
| `DATABASE_URL`  | Yes      | PostgreSQL connection string |

**Example connection string:**

```text
postgresql://user:password@host:port/database
```

Local `.env` example:

```env
DISCORD_TOKEN=your_discord_bot_token_here
DATABASE_URL=postgresql://user:password@localhost:5432/shifts
```

---

## Database Schema

### Table: `shifts`

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

### Table: `mod_schedules`

Stores weekly schedule slots per moderator.

| Column        | Type               | Description            |
| ------------- | ------------------ | ---------------------- |
| `id`          | SERIAL PRIMARY KEY | Auto-increment ID      |
| `user_id`     | BIGINT NOT NULL    | Discord user ID        |
| `day_of_week` | INTEGER NOT NULL   | 0 = Monday, 6 = Sunday |
| `hour`        | INTEGER NOT NULL   | Hour (0–23)            |
| `minute`      | INTEGER DEFAULT 0  | Minute (0–59)          |
| `timezone`    | TEXT DEFAULT 'UTC' | IANA timezone string   |
| `created_at`  | TIMESTAMPTZ        | When slot was created  |

Unique constraint: `(user_id, day_of_week, hour, minute)`.

---

## Commands

### Schedule Management

| Command               | Description                               | Who can use                  |   |
| --------------------- | ----------------------------------------- | ---------------------------- | - |
| `/schedule_add`       | Add a time slot to your own schedule      | Moderators                   |   |
| `/schedule_add_admin` | Add a schedule slot for another moderator | Admins                       |   |
| `/schedule_remove`    | Remove a slot from your own schedule      | Moderators                   |   |
| `/schedule_view`      | View a user’s schedule                    | Anyone                       |   |
| `/schedule_clear`     | Clear all slots for a user                | Self: Mods, Any user: Admins |   |

### Shift Commands

| Command           | Description                                           | Who can use                         |   |
| ----------------- | ----------------------------------------------------- | ----------------------------------- | - |
| `/drop_shift`     | Post a generic shift with a claim button              | Moderators                          |   |
| `/drop_mod_shift` | Drop a scheduled shift from a moderator’s schedule    | Mods (self), Admins (any moderator) |   |
| `/shift_edit`     | Edit an unclaimed shift (time, duration, description) | Shift owner or Admin                |   |
| `/shift_cancel`   | Cancel a shift (claimed or unclaimed)                 | Shift owner or Admin                |   |
| `/shift_stats`    | View claimed shift count and total hours              | Anyone                              |   |
| `/sync_commands`  | Manually sync slash commands with Discord             | Admins                              |   |

### Fallback Prefix Commands

| Command | Description                 |   |
| ------- | --------------------------- | - |
| `!ping` | Check if bot is responsive  |   |
| `!sync` | Sync slash commands (admin) |   |

---

## Fairness System

To prevent one moderator from claiming all the shifts:

1. The bot sums up `duration_hours` for claimed, non-cancelled shifts in the last 7 days for each user.
2. If `total_hours >= MAX_HOURS_7D`, the user is considered **heavy**.
3. Heavy users can only claim shifts with start times within `HEAVY_LOCK_WINDOW_MINUTES` of now.
4. Otherwise, the claim is blocked and the user gets an explanatory error.

---

## Timezone & DST Handling

* Each schedule slot stores:

  * `day_of_week` (0–6),
  * `hour` / `minute`,
  * `timezone` (IANA name).
* The bot uses `ZoneInfo` to:

  * Get the current local time in that timezone.
  * Calculate the **next** occurrence of that weekday/time.
  * Convert it to UTC for storage and comparison.
* DST transitions (EST↔EDT, etc.) are handled automatically; the moderator’s local time stays consistent.

---

## Local Setup

1. **Create virtual environment (optional but recommended)**

   ```bash
   python -m venv .venv
   # Windows
   .venv\Scripts\activate
   # macOS/Linux
   source .venv/bin/activate
   ```

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**

   * Set `DISCORD_TOKEN` and `DATABASE_URL` in your environment, or
   * Create a `.env` file next to `bot.py`:

     ```env
     DISCORD_TOKEN=your_discord_bot_token_here
     DATABASE_URL=postgresql://user:password@host:5432/shifts
     ```

4. **Configure IDs in `bot.py`**

   * Set `ALLOWED_CHANNEL_ID`, `MOD_ROLE_ID`, `ADMIN_ROLE_ID`, and any fairness/timing settings.

5. **Run the bot**

   ```bash
   python bot.py
   ```

   On first startup, the bot will:

   * Initialize the `shifts` and `mod_schedules` tables if they do not exist.
   * Register the persistent `ShiftClaimView` for buttons.

6. **Sync slash commands**

   In Discord, once the bot is online:

   * Use `/sync_commands`, or
   * Use `!sync` if you prefer prefix commands.

Slash commands may take a minute or two to fully appear.

---

## Deployment (Example: Render)

1. **Database**

   * Provision a PostgreSQL database (e.g. Supabase, Neon, or Render’s managed Postgres).
   * Copy the connection URL and use it as `DATABASE_URL`. 

2. **Web Service**

   * Connect your GitHub repo to a new Web Service.

   * Build command:

     ```bash
     pip install -r requirements.txt
     ```

   * Start command:

     ```bash
     python bot.py
     ```

   * Add environment variables:

     * `DISCORD_TOKEN=...`
     * `DATABASE_URL=...`

3. Deploy. The bot should come online and start listening for slash/prefix commands.

---

## License

This bot is provided as-is for managing Discord moderator shifts.
You are free to fork, modify, and adapt it for your own servers and workflows.

```

::contentReference[oaicite:19]{index=19}
```
