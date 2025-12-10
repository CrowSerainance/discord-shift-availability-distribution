# Shift Bot - Technical Documentation

## Overview

A Discord bot for managing moderator shift coverage. Features:
- Database-stored schedules (no hardcoded values)
- DST-aware timezone support
- Schedule management via Discord commands
- Shift creation, editing, and cancellation
- Fairness system to prevent shift hogging
- Persistent claim buttons

---

## File Structure

```
DISCORD BOT/
├── bot.py              # Main bot (PostgreSQL, DST-aware)
├── requirements.txt    # Dependencies
└── DOCUMENTATION.md    # This file
```

---

## Database Schema

### Table: `shifts`

| Column | Type | Description |
|--------|------|-------------|
| `id` | `SERIAL PRIMARY KEY` | Auto-increment ID |
| `message_id` | `BIGINT UNIQUE` | Discord message ID |
| `channel_id` | `BIGINT` | Discord channel ID |
| `description` | `TEXT` | Shift description |
| `created_by` | `BIGINT` | User who dropped the shift |
| `created_at` | `TIMESTAMPTZ` | When created |
| `start_time_utc` | `TIMESTAMPTZ` | When shift starts (UTC) |
| `duration_hours` | `REAL` | Duration in hours |
| `assigned_user_id` | `BIGINT` | Original moderator |
| `claimed_by` | `BIGINT` | User who claimed |
| `claimed_at` | `TIMESTAMPTZ` | When claimed |
| `cancelled` | `BOOLEAN` | Whether cancelled |

### Table: `mod_schedules`

| Column | Type | Description |
|--------|------|-------------|
| `id` | `SERIAL PRIMARY KEY` | Auto-increment ID |
| `user_id` | `BIGINT NOT NULL` | Discord user ID |
| `day_of_week` | `INTEGER NOT NULL` | 0=Monday, 6=Sunday |
| `hour` | `INTEGER NOT NULL` | Hour (0-23) |
| `minute` | `INTEGER DEFAULT 0` | Minute (0-59) |
| `timezone` | `TEXT DEFAULT 'UTC'` | IANA timezone |
| `created_at` | `TIMESTAMPTZ` | When created |

**Unique Constraint:** `(user_id, day_of_week, hour, minute)`

---

## Configuration

Edit these values in `bot.py` (lines 68-86):

```python
ALLOWED_CHANNEL_ID = YOUR_CHANNEL_ID_HERE      # Channel for shift commands
MOD_ROLE_ID = YOUR_MOD_ROLE_ID_HERE            # Moderator role
ADMIN_ROLE_ID = YOUR_ADMIN_ROLE_ID_HERE        # Admin role

MAX_HOURS_7D = 3.0                  # Max hours before fairness restriction
HEAVY_LOCK_WINDOW_MINUTES = 60      # Minutes before shift when heavy users can claim

MIN_DURATION_HOURS = 0.25           # Minimum shift duration
MAX_DURATION_HOURS = 24.0           # Maximum shift duration

DEFAULT_TIMEZONE = "UTC"            # Default timezone for new schedules
```

---

## Discord Commands

### Schedule Management

| Command | Description | Who Can Use |
|---------|-------------|-------------|
| `/schedule_add` | Add a time slot to your schedule | Moderators |
| `/schedule_remove` | Remove a time slot | Moderators |
| `/schedule_view` | View a user's schedule | Anyone |
| `/schedule_clear` | Clear all slots | Moderators (own), Admins (any) |

### Shift Commands

| Command | Description | Who Can Use |
|---------|-------------|-------------|
| `/drop_shift` | Post a generic shift | Moderators |
| `/drop_mod_shift` | Drop a scheduled shift | Moderators (own), Admins (any) |
| `/shift_edit` | Edit an unclaimed shift | Owner or Admin |
| `/shift_cancel` | Cancel a shift | Owner or Admin |
| `/shift_stats` | View shift statistics | Anyone |
| `/sync_commands` | Sync slash commands | Admins |

### Fallback Commands

| Command | Description |
|---------|-------------|
| `!ping` | Check if bot is responsive |
| `!sync` | Sync slash commands |

---

## DST/Timezone Handling

### How It Works

1. Schedules are stored with a timezone (e.g., `America/New_York`)
2. When calculating the next shift time, the bot:
   - Gets current time in the user's timezone
   - Calculates the next occurrence of that day/hour
   - Converts to UTC for storage/display
3. DST transitions are handled automatically by `ZoneInfo`

### Example

A moderator in New York sets a shift for Monday 14:00:
- During EST (winter): Stored as Monday 19:00 UTC
- During EDT (summer): Stored as Monday 18:00 UTC

The shift always appears as "Monday 2:00 PM" to the moderator, regardless of DST.

### Supported Timezones

Common timezones with autocomplete:
- `UTC`
- `America/New_York` (US Eastern)
- `America/Chicago` (US Central)
- `America/Denver` (US Mountain)
- `America/Los_Angeles` (US Pacific)
- `Europe/London` (UK)
- `Europe/Paris` (Central Europe)
- `Asia/Tokyo` (Japan)
- `Australia/Sydney` (Australia Eastern)

Any valid IANA timezone is supported.

---

## Fairness System

Prevents one person from claiming too many shifts.

1. Bot tracks total hours claimed by each user in the last 7 days
2. If `total_hours >= MAX_HOURS_7D` (default: 3.0), user is "heavy"
3. Heavy users can only claim shifts within `HEAVY_LOCK_WINDOW_MINUTES` (default: 60) of start time
4. This gives lighter users first chance at shifts

---

## Permission System

### Three Tiers

1. **Everyone:** View schedules, view stats, claim shifts
2. **Moderators (MOD_ROLE_ID):** Drop shifts, edit own shifts, manage own schedule
3. **Admins (ADMIN_ROLE_ID):** Drop any shift, edit any shift, clear any schedule, sync commands

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | Yes | Bot token from Discord Developer Portal |
| `DATABASE_URL` | Yes | PostgreSQL connection string |

### Connection String Format
```
postgresql://user:password@host:port/database
```

### Local Development

Create `.env` file:
```
DISCORD_TOKEN=your_token_here
DATABASE_URL=postgresql://user:password@localhost:5432/shifts
```

---

## Deployment

1. Create Web Service on your hosting platform, connect GitHub repo
2. Build command: `pip install -r requirements.txt`
3. Start command: `python bot.py`
4. Set environment variables in dashboard:
   - `DISCORD_TOKEN`
   - `DATABASE_URL`
5. Use a PostgreSQL provider (e.g., Neon.tech, Supabase, or self-hosted)

---

## Key Functions

### Schedule Functions

```python
add_schedule_slot(user_id, day_of_week, hour, minute, tz) -> (bool, str)
remove_schedule_slot(user_id, day_of_week, hour, minute) -> (bool, str)
get_schedule_for_user(user_id) -> list[(day, hour, minute, tz)]
clear_schedule_for_user(user_id) -> int  # returns count deleted
user_has_schedule(user_id) -> bool
```

### Shift Functions

```python
save_shift(message_id, channel_id, description, created_by, ...) -> bool
can_claim_and_update(message_id, user_id) -> (status, total_hours)
cancel_shift(message_id, user_id, is_admin) -> status
update_shift(message_id, user_id, is_admin, description, start_time_utc, duration_hours) -> (status, data)
get_shift_count_for_user(user_id) -> (count, hours)
get_cancellable_shifts_for_user(user_id, is_admin) -> list
get_editable_shifts_for_user(user_id, is_admin) -> list
get_shift_details(message_id) -> tuple | None
```

### DateTime Functions

```python
next_datetime_for_slot(day_of_week, hour, minute, tz_name) -> datetime  # UTC
format_slot_for_display(day_of_week, hour, minute, tz_name) -> str
get_utc_offset_display(tz_name) -> str  # e.g., "UTC-5"
```

---

## Shift Editing

### Overview

The `/shift_edit` command allows moderators and admins to modify shift details **before** they are claimed. Once a shift is claimed, it cannot be edited (only cancelled).

### What Can Be Edited

- **Description** - Change the shift description text
- **Start Time** - Change the date and time (must provide both date and time together)
- **Duration** - Change the shift duration in hours

### Restrictions

- ✅ Can only edit **unclaimed** shifts
- ✅ Can only edit shifts you created (admins can edit any shift)
- ❌ Cannot edit **claimed** shifts
- ❌ Cannot edit **cancelled** shifts
- ❌ Must provide at least one field to update

### Usage Examples

**Edit just the description:**
```
/shift_edit shift:"Shift Available" description:"Updated shift description"
```

**Change the start time:**
```
/shift_edit shift:"Shift Available" date:2024-12-25 time:14:30
```
*Note: Both `date` and `time` must be provided together when changing the start time.*

**Change the duration:**
```
/shift_edit shift:"Shift Available" duration_hours:2.5
```

**Edit multiple fields at once:**
```
/shift_edit shift:"Shift Available" description:"New description" duration_hours:3.0
```

### Time Format

- **Date:** `YYYY-MM-DD` (e.g., `2024-12-25`)
- **Time:** `HH:MM` in 24-hour format (e.g., `14:30` for 2:30 PM)
- Times are stored in UTC - the bot will validate that the new time is not in the past

### What Happens When You Edit

1. The database is updated with your changes
2. The Discord message embed is automatically updated
3. A "Last edited by" field is added/updated on the message
4. The shift remains available for claiming (if it was unclaimed)

### Tips

- **Edit vs Cancel:** Use `/shift_edit` for small corrections (typos, wrong time). Use `/shift_cancel` if you need to completely remove a shift.
- **Time Changes:** When changing the start time, make sure the new time is in the future. The bot will reject past dates/times.
- **Multiple Edits:** You can edit a shift multiple times before it's claimed. Each edit updates the "Last edited by" field.
- **Autocomplete:** The shift dropdown only shows shifts you can actually edit (your own unclaimed shifts, or any unclaimed shift if you're an admin).

---

## Shift Workflow

### Complete Shift Lifecycle

1. **Setup Schedule** (One-time per moderator)
   - Use `/schedule_add` to add your weekly time slots
   - Set your timezone so DST is handled automatically

2. **Drop a Shift**
   - **Generic shift:** Use `/drop_shift` for any shift
   - **Scheduled shift:** Use `/drop_mod_shift` to drop one of your scheduled slots
   - The shift appears in the channel with a "Claim shift" button

3. **Edit a Shift** (Optional, before claiming)
   - Use `/shift_edit` to modify description, time, or duration
   - Only works on unclaimed shifts
   - The Discord message updates automatically

4. **Claim a Shift**
   - Click the "Claim shift" button on any available shift
   - The bot checks fairness rules (max 3 hours in last 7 days)
   - Button becomes disabled and shows "Claimed"

5. **Cancel a Shift** (If needed)
   - Use `/shift_cancel` to cancel a shift
   - Works on both claimed and unclaimed shifts
   - If claimed, the claimer loses coverage

### Example Workflow

**Moderator Alice:**
1. Sets up schedule: `/schedule_add day:Monday hour:14 timezone:America/New_York`
2. Drops a shift: `/drop_mod_shift target:@Alice slot:"Monday 14:00 (America/New_York)"`
3. Realizes mistake: `/shift_edit shift:"..." description:"Corrected description"`
4. Shift gets claimed by Moderator Bob

**Moderator Bob:**
1. Sees shift in channel
2. Clicks "Claim shift" button
3. Shift is now assigned to Bob
4. Alice can no longer edit it (it's claimed)

---

## Common Tasks

### Add a New Moderator

1. Have them use `/schedule_add` to add their time slots
2. They select day, hour, minute, and timezone
3. DST is handled automatically

### Remove a Moderator

1. Use `/schedule_clear @user` (admin only)
2. Or have them use `/schedule_remove` for individual slots

### Change Channel Restriction

Edit `ALLOWED_CHANNEL_ID` in bot.py (replace with your channel ID) and redeploy.

### Change Role Requirements

Edit `MOD_ROLE_ID` and `ADMIN_ROLE_ID` in bot.py (replace with your role IDs) and redeploy.

### Edit a Shift

1. Use `/shift_edit` and select the shift from the dropdown
2. Provide the fields you want to change:
   - `description` - New description text
   - `date` and `time` - New start time (provide both together)
   - `duration_hours` - New duration (0.25 to 24 hours)
3. The shift message updates automatically
4. Note: Only unclaimed shifts can be edited

---

## Troubleshooting

### "Slash commands don't appear"
- Run `!sync` or `/sync_commands`
- Wait a few minutes for Discord to update

### "Invalid timezone"
- Use IANA format: `America/New_York`, not `EST`
- Check spelling and case sensitivity

### "No schedule configured"
- User must add slots with `/schedule_add` first
- Schedules are per-user in database, not hardcoded

### "Button doesn't work after restart"
- `ShiftClaimView` is registered in `on_ready()`
- Check `custom_id="shift_claim_button"` matches

### "You cannot edit a shift that has already been claimed"
- Once a shift is claimed, it cannot be edited
- Cancel the shift and create a new one if changes are needed
- Or use `/shift_cancel` to cancel, then create a new shift

### "You can only edit your own shifts"
- Only the shift creator can edit their shifts
- Admins can edit any shift
- Check that you have the correct role permissions

### "Both date and time must be provided together"
- When changing the start time, you must provide both `date` and `time` parameters
- You cannot change just the date or just the time separately
- If you only want to change the description or duration, don't include date/time

---

## Migration Notes

### From Hardcoded Schedules

The old `MOD_SCHEDULES` dictionary has been removed. To migrate existing schedules:

1. Each moderator uses `/schedule_add` to recreate their slots
2. Or admin can add slots for them (would require custom code)

### Database Migration

If upgrading from an older version, the `mod_schedules` table is created automatically on startup. No manual migration needed.

---

---

## Command Reference

### Quick Command Guide

**For Moderators:**
- `/schedule_add` - Add your weekly schedule slots
- `/schedule_view` - View your or someone else's schedule
- `/drop_shift` - Post a generic shift
- `/drop_mod_shift` - Drop one of your scheduled shifts
- `/shift_edit` - Edit an unclaimed shift you created
- `/shift_cancel` - Cancel a shift you posted
- `/shift_stats` - View your shift statistics

**For Admins:**
- All moderator commands, plus:
- `/schedule_clear @user` - Clear anyone's schedule
- `/drop_mod_shift` with any user - Drop shifts for any moderator
- `/shift_edit` - Edit any unclaimed shift
- `/shift_cancel` - Cancel any shift
- `/sync_commands` - Sync slash commands

**For Everyone:**
- `/schedule_view` - View schedules
- `/shift_stats` - View statistics
- Click "Claim shift" button - Claim available shifts

---

*Last updated: December 2024*
