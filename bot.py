"""
================================================================================
SHIFT BOT - POSTGRESQL VERSION (for Render.com deployment)
================================================================================

A Discord bot for managing moderator shift coverage. Allows moderators to:
  - Drop shifts they can't cover
  - Claim available shifts from other moderators
  - View their shift statistics
  - Manage their own schedules via commands

FEATURES:
---------
1. Database-stored schedules (no hardcoded schedules)
2. DST-aware timezone support (schedules stored in user's local timezone)
3. Schedule management via Discord commands
4. Fairness system to prevent shift hogging
5. Persistent claim buttons that work after bot restarts

SETUP INSTRUCTIONS:
-------------------
1. Set environment variables:
   - DATABASE_URL: Your PostgreSQL connection string
   - DISCORD_TOKEN: Your Discord bot token

2. Install required packages:
   pip install discord.py psycopg2-binary python-dotenv pytz

3. Configure the settings below (ALLOWED_CHANNEL_ID, MOD_ROLE_ID, etc.)

4. Run the bot:
   python bot.py

5. First time only: Type !sync in Discord to register slash commands

================================================================================
"""

import os
import psycopg2
import logging
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands
from discord import app_commands

# Load environment variables from .env file for local development
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, assume env vars are set directly


# ==============================================================================
# CONFIGURATION SECTION
# ==============================================================================

# Your bot token - loaded from environment variable
TOKEN = os.environ.get("DISCORD_TOKEN", "")

# Database connection URL - loaded from environment variable
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# The channel ID where shift commands can be used
ALLOWED_CHANNEL_ID = 1445805874382901388  # Replace with your channel ID

# The role ID that can use moderator commands (drop shifts, cancel shifts)
MOD_ROLE_ID = 1445982976809767002  # Replace with your moderator role ID

# The role ID that can use admin commands (sync commands)
ADMIN_ROLE_ID = 1445985907093012561  # Replace with your admin role ID

# Fairness settings to prevent one person from claiming too many shifts
MAX_HOURS_7D = 3.0                  # Max hours in last 7 days before being "heavy"
HEAVY_LOCK_WINDOW_MINUTES = 60      # Heavy mods can only claim if <60 min before start

# Duration limits for shift duration_hours parameter
MIN_DURATION_HOURS = 0.25  # Minimum 15 minutes
MAX_DURATION_HOURS = 24.0  # Maximum 24 hours

# Default timezone for new schedules (can be changed per-user)
DEFAULT_TIMEZONE = "UTC"


# ==============================================================================
# LOGGING SETUP
# ==============================================================================

logger = logging.getLogger("ShiftBot")
logger.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

log_format = logging.Formatter(
    "[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
console_handler.setFormatter(log_format)
logger.addHandler(console_handler)


# ==============================================================================
# CONSTANTS
# ==============================================================================

DAY_NAME_TO_WEEKDAY = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

WEEKDAY_TO_DAY_NAME = {v: k for k, v in DAY_NAME_TO_WEEKDAY.items()}

# Common timezones for autocomplete
COMMON_TIMEZONES = [
    "UTC",
    "America/New_York",      # US Eastern
    "America/Chicago",       # US Central
    "America/Denver",        # US Mountain
    "America/Los_Angeles",   # US Pacific
    "Europe/London",         # UK
    "Europe/Paris",          # Central Europe
    "Europe/Berlin",         # Germany
    "Asia/Tokyo",            # Japan
    "Asia/Shanghai",         # China
    "Asia/Kolkata",          # India
    "Australia/Sydney",      # Australia Eastern
    "Pacific/Auckland",      # New Zealand
]


# ==============================================================================
# DATABASE HELPER FUNCTIONS
# ==============================================================================


@contextmanager
def get_db_connection():
    """Context manager for PostgreSQL database connections."""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        yield conn
    finally:
        if conn:
            conn.close()


def init_db():
    """Initialize the database by creating tables if they don't exist."""
    with get_db_connection() as conn:
        cur = conn.cursor()

        # Create the shifts table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS shifts (
                id SERIAL PRIMARY KEY,
                message_id BIGINT UNIQUE,
                channel_id BIGINT,
                description TEXT,
                created_by BIGINT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                start_time_utc TIMESTAMPTZ,
                duration_hours REAL DEFAULT 1.0,
                assigned_user_id BIGINT,
                claimed_by BIGINT,
                claimed_at TIMESTAMPTZ,
                cancelled BOOLEAN DEFAULT FALSE
            )
            """
        )

        # Create the mod_schedules table for storing moderator schedules
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS mod_schedules (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                day_of_week INTEGER NOT NULL,
                hour INTEGER NOT NULL,
                minute INTEGER DEFAULT 0,
                timezone TEXT DEFAULT 'UTC',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(user_id, day_of_week, hour, minute)
            )
            """
        )

        # Create index for faster lookups
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mod_schedules_user_id
            ON mod_schedules(user_id)
            """
        )

        conn.commit()
        logger.info("Database initialized successfully")


# ==============================================================================
# SCHEDULE DATABASE FUNCTIONS
# ==============================================================================


def add_schedule_slot(user_id: int, day_of_week: int, hour: int, minute: int = 0, tz: str = "UTC") -> tuple[bool, str]:
    """
    Add a schedule slot for a moderator.

    Returns (success, message) tuple.
    """
    try:
        # Validate timezone
        try:
            ZoneInfo(tz)
        except Exception:
            return False, f"Invalid timezone: {tz}"

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO mod_schedules (user_id, day_of_week, hour, minute, timezone)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, day_of_week, hour, minute) DO NOTHING
                RETURNING id
                """,
                (user_id, day_of_week, hour, minute, tz),
            )
            result = cur.fetchone()
            conn.commit()

            if result:
                return True, "Schedule slot added successfully."
            else:
                return False, "This slot already exists in your schedule."

    except psycopg2.Error as e:
        logger.error(f"Database error adding schedule slot: {e}")
        return False, "Database error occurred."


def remove_schedule_slot(user_id: int, day_of_week: int, hour: int, minute: int = 0) -> tuple[bool, str]:
    """
    Remove a schedule slot for a moderator.

    Returns (success, message) tuple.
    """
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                DELETE FROM mod_schedules
                WHERE user_id = %s AND day_of_week = %s AND hour = %s AND minute = %s
                RETURNING id
                """,
                (user_id, day_of_week, hour, minute),
            )
            result = cur.fetchone()
            conn.commit()

            if result:
                return True, "Schedule slot removed successfully."
            else:
                return False, "This slot was not found in your schedule."

    except psycopg2.Error as e:
        logger.error(f"Database error removing schedule slot: {e}")
        return False, "Database error occurred."


def get_schedule_for_user(user_id: int) -> list[tuple[int, int, int, str]]:
    """
    Get all schedule slots for a user.

    Returns list of (day_of_week, hour, minute, timezone) tuples.
    """
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT day_of_week, hour, minute, timezone
                FROM mod_schedules
                WHERE user_id = %s
                ORDER BY day_of_week, hour, minute
                """,
                (user_id,),
            )
            return cur.fetchall()

    except psycopg2.Error as e:
        logger.error(f"Database error getting schedule for user {user_id}: {e}")
        return []


def clear_schedule_for_user(user_id: int) -> int:
    """
    Clear all schedule slots for a user.

    Returns number of slots deleted.
    """
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                DELETE FROM mod_schedules
                WHERE user_id = %s
                RETURNING id
                """,
                (user_id,),
            )
            deleted = len(cur.fetchall())
            conn.commit()
            return deleted

    except psycopg2.Error as e:
        logger.error(f"Database error clearing schedule for user {user_id}: {e}")
        return 0


def user_has_schedule(user_id: int) -> bool:
    """Check if a user has any schedule slots configured."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM mod_schedules WHERE user_id = %s LIMIT 1",
                (user_id,),
            )
            return cur.fetchone() is not None

    except psycopg2.Error as e:
        logger.error(f"Database error checking schedule for user {user_id}: {e}")
        return False


# ==============================================================================
# SHIFT DATABASE FUNCTIONS
# ==============================================================================


def save_shift(
    message_id: int,
    channel_id: int,
    description: str,
    created_by: int,
    start_time_utc: datetime | None = None,
    duration_hours: float = 1.0,
    assigned_user_id: int | None = None,
) -> bool:
    """Save a new shift to the database."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO shifts (
                    message_id, channel_id, description, created_by,
                    created_at, start_time_utc, duration_hours, assigned_user_id
                )
                VALUES (%s, %s, %s, %s, NOW(), %s, %s, %s)
                """,
                (message_id, channel_id, description, created_by,
                 start_time_utc, duration_hours, assigned_user_id),
            )
            conn.commit()
            logger.debug(f"Saved shift: message_id={message_id}, created_by={created_by}")
            return True

    except psycopg2.Error as e:
        logger.error(f"Database error saving shift: {e}")
        return False


def get_total_hours_last_7d(user_id: int) -> float:
    """Calculate total hours claimed by a user in the last 7 days."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COALESCE(SUM(duration_hours), 0.0)
                FROM shifts
                WHERE claimed_by = %s
                  AND cancelled = FALSE
                  AND (start_time_utc IS NULL OR start_time_utc >= NOW() - INTERVAL '7 days')
                """,
                (user_id,),
            )
            (total,) = cur.fetchone()
            return float(total or 0.0)

    except psycopg2.Error as e:
        logger.error(f"Database error getting hours for user {user_id}: {e}")
        return 0.0


def can_claim_and_update(message_id: int, user_id: int) -> tuple[str, float]:
    """Check if a user can claim a shift and update the database if so."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT created_by, claimed_by, start_time_utc, duration_hours, cancelled
                FROM shifts WHERE message_id = %s
                """,
                (message_id,),
            )
            row = cur.fetchone()

            if row is None:
                return "not_found", 0.0

            created_by, claimed_by, start_time_utc, duration_hours, cancelled = row

            if cancelled:
                return "cancelled", 0.0
            if created_by == user_id:
                return "own_shift", 0.0
            if claimed_by is not None:
                return "already_claimed", 0.0

            total_hours = get_total_hours_last_7d(user_id)

            if start_time_utc:
                shift_start = start_time_utc
                if shift_start.tzinfo is None:
                    shift_start = shift_start.replace(tzinfo=timezone.utc)

                if total_hours >= MAX_HOURS_7D:
                    now = datetime.now(timezone.utc)
                    minutes_until = (shift_start - now).total_seconds() / 60
                    if minutes_until > HEAVY_LOCK_WINDOW_MINUTES:
                        return "over_cap", total_hours

            cur.execute(
                """
                UPDATE shifts SET claimed_by = %s, claimed_at = NOW()
                WHERE message_id = %s
                """,
                (user_id, message_id),
            )
            conn.commit()
            logger.info(f"Shift {message_id} claimed by user {user_id}")
            return "claimed", total_hours

    except psycopg2.Error as e:
        logger.error(f"Database error during claim: {e}")
        return "not_found", 0.0


def cancel_shift(message_id: int, user_id: int, is_admin: bool = False) -> str:
    """Cancel a shift."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT created_by, claimed_by, cancelled FROM shifts WHERE message_id = %s",
                (message_id,),
            )
            row = cur.fetchone()

            if row is None:
                return "not_found"

            created_by, claimed_by, cancelled = row

            if created_by != user_id and not is_admin:
                return "not_owner"
            if cancelled:
                return "already_cancelled"

            cur.execute(
                "UPDATE shifts SET cancelled = TRUE WHERE message_id = %s",
                (message_id,),
            )
            conn.commit()

            if claimed_by is not None:
                logger.info(f"Shift {message_id} cancelled by user {user_id} (was claimed by {claimed_by})")
                return "cancelled_claimed"
            else:
                logger.info(f"Shift {message_id} cancelled by user {user_id}")
                return "cancelled"

    except psycopg2.Error as e:
        logger.error(f"Database error during cancellation: {e}")
        return "not_found"


def get_shift_count_for_user(user_id: int) -> tuple[int, float]:
    """Get shift statistics for a user."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(duration_hours), 0.0)
                FROM shifts WHERE claimed_by = %s AND cancelled = FALSE
                """,
                (user_id,),
            )
            row = cur.fetchone()
            return int(row[0] or 0), float(row[1] or 0.0)

    except psycopg2.Error as e:
        logger.error(f"Database error getting stats for user {user_id}: {e}")
        return 0, 0.0


def get_cancellable_shifts_for_user(user_id: int, is_admin: bool = False) -> list[tuple[int, str, datetime | None, bool]]:
    """Get all shifts that a user can cancel."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()

            if is_admin:
                cur.execute(
                    """
                    SELECT message_id, description, start_time_utc, created_at, claimed_by
                    FROM shifts WHERE cancelled = FALSE
                    ORDER BY created_at DESC LIMIT 25
                    """,
                )
            else:
                cur.execute(
                    """
                    SELECT message_id, description, start_time_utc, created_at, claimed_by
                    FROM shifts WHERE created_by = %s AND cancelled = FALSE
                    ORDER BY created_at DESC LIMIT 25
                    """,
                    (user_id,),
                )

            rows = cur.fetchall()
            result = []
            for row in rows:
                message_id = row[0]
                description = row[1] or "Shift"
                start_time = row[2]
                is_claimed = row[4] is not None
                result.append((message_id, description, start_time, is_claimed))
            return result

    except psycopg2.Error as e:
        logger.error(f"Database error getting cancellable shifts: {e}")
        return []


def get_editable_shifts_for_user(user_id: int, is_admin: bool = False) -> list[tuple[int, str, datetime | None, float]]:
    """Get all shifts that a user can edit (must be unclaimed)."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()

            if is_admin:
                cur.execute(
                    """
                    SELECT message_id, description, start_time_utc, duration_hours
                    FROM shifts 
                    WHERE cancelled = FALSE AND claimed_by IS NULL
                    ORDER BY created_at DESC LIMIT 25
                    """,
                )
            else:
                cur.execute(
                    """
                    SELECT message_id, description, start_time_utc, duration_hours
                    FROM shifts 
                    WHERE created_by = %s AND cancelled = FALSE AND claimed_by IS NULL
                    ORDER BY created_at DESC LIMIT 25
                    """,
                    (user_id,),
                )

            rows = cur.fetchall()
            result = []
            for row in rows:
                message_id = row[0]
                description = row[1] or "Shift"
                start_time = row[2]
                duration = row[3] or 1.0
                result.append((message_id, description, start_time, duration))
            return result

    except psycopg2.Error as e:
        logger.error(f"Database error getting editable shifts: {e}")
        return []


def get_shift_details(message_id: int) -> tuple | None:
    """Get full shift details for editing."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT created_by, description, start_time_utc, duration_hours, 
                       assigned_user_id, claimed_by, cancelled, channel_id
                FROM shifts WHERE message_id = %s
                """,
                (message_id,),
            )
            row = cur.fetchone()
            if row:
                return row
            return None
    except psycopg2.Error as e:
        logger.error(f"Database error getting shift details: {e}")
        return None


def update_shift(
    message_id: int,
    user_id: int,
    is_admin: bool,
    description: str | None = None,
    start_time_utc: datetime | None = None,
    duration_hours: float | None = None,
) -> tuple[str, dict]:
    """
    Update shift details.
    
    Returns (status, data) tuple where status is:
    - "success": Update successful
    - "not_found": Shift doesn't exist
    - "not_owner": User doesn't own this shift
    - "already_claimed": Shift is already claimed (can't edit)
    - "cancelled": Shift is cancelled (can't edit)
    - "no_changes": No fields provided to update
    
    data contains the updated shift information.
    """
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            
            # Get current shift details
            cur.execute(
                """
                SELECT created_by, description, start_time_utc, duration_hours, 
                       claimed_by, cancelled
                FROM shifts WHERE message_id = %s
                """,
                (message_id,),
            )
            row = cur.fetchone()
            
            if row is None:
                return "not_found", {}
            
            created_by, old_desc, old_start, old_duration, claimed_by, cancelled = row
            
            # Permission check
            if created_by != user_id and not is_admin:
                return "not_owner", {}
            
            # Can't edit if claimed or cancelled
            if claimed_by is not None:
                return "already_claimed", {}
            if cancelled:
                return "cancelled", {}
            
            # Check if any changes provided
            if description is None and start_time_utc is None and duration_hours is None:
                return "no_changes", {}
            
            # Build update query dynamically
            updates = []
            params = []
            
            if description is not None:
                updates.append("description = %s")
                params.append(description)
            
            if start_time_utc is not None:
                updates.append("start_time_utc = %s")
                params.append(start_time_utc)
            
            if duration_hours is not None:
                if not (MIN_DURATION_HOURS <= duration_hours <= MAX_DURATION_HOURS):
                    return "invalid_duration", {}
                updates.append("duration_hours = %s")
                params.append(duration_hours)
            
            # Add message_id for WHERE clause
            params.append(message_id)
            
            # Execute update
            update_query = f"""
                UPDATE shifts 
                SET {', '.join(updates)}
                WHERE message_id = %s
            """
            cur.execute(update_query, params)
            conn.commit()
            
            # Get updated values
            new_desc = description if description is not None else old_desc
            new_start = start_time_utc if start_time_utc is not None else old_start
            new_duration = duration_hours if duration_hours is not None else old_duration
            
            logger.info(f"Shift {message_id} updated by user {user_id}")
            
            return "success", {
                "description": new_desc,
                "start_time_utc": new_start,
                "duration_hours": new_duration,
            }
            
    except psycopg2.Error as e:
        logger.error(f"Database error updating shift: {e}")
        return "not_found", {}


# ==============================================================================
# DATETIME HELPER FUNCTIONS (DST-AWARE)
# ==============================================================================


def next_datetime_for_slot(day_of_week: int, hour: int, minute: int, tz_name: str) -> datetime:
    """
    Calculate the next upcoming datetime for a given day, hour, and minute
    in the specified timezone, properly handling DST transitions.

    Returns a UTC datetime.
    """
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")

    # Get current time in the user's timezone
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)

    # Calculate days until target weekday
    days_ahead = (day_of_week - now_local.weekday()) % 7

    # Create candidate datetime in local timezone
    candidate_local = now_local.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    ) + timedelta(days=days_ahead)

    # If that time already passed this week, push to next week
    if candidate_local <= now_local:
        candidate_local += timedelta(days=7)

    # Handle DST transitions - the ZoneInfo handles this automatically
    # but we need to make sure the time exists and is unambiguous
    try:
        # Re-localize to handle DST properly
        candidate_naive = candidate_local.replace(tzinfo=None)
        candidate_local = candidate_naive.replace(tzinfo=tz)
    except Exception:
        pass  # If localization fails, use the original

    # Convert to UTC for storage
    return candidate_local.astimezone(timezone.utc)


def format_slot_for_display(day_of_week: int, hour: int, minute: int, tz_name: str) -> str:
    """Format a schedule slot for display."""
    day_name = WEEKDAY_TO_DAY_NAME.get(day_of_week, "Unknown").capitalize()
    if minute == 0:
        return f"{day_name} {hour:02d}:00 ({tz_name})"
    else:
        return f"{day_name} {hour:02d}:{minute:02d} ({tz_name})"


def get_utc_offset_display(tz_name: str) -> str:
    """Get the current UTC offset for a timezone (handles DST)."""
    try:
        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)
        offset = now.strftime("%z")
        # Format as UTC+X or UTC-X
        if offset:
            sign = offset[0]
            hours = int(offset[1:3])
            mins = int(offset[3:5])
            if mins == 0:
                return f"UTC{sign}{hours}"
            else:
                return f"UTC{sign}{hours}:{mins:02d}"
    except Exception:
        pass
    return tz_name


# ==============================================================================
# PERMISSION CHECK HELPERS
# ==============================================================================


def has_mod_role(member: discord.Member) -> bool:
    """Check if a member has the moderator role."""
    if MOD_ROLE_ID is None:
        return True
    return any(role.id == MOD_ROLE_ID for role in member.roles)


def has_admin_role(member: discord.Member) -> bool:
    """Check if a member has the admin role."""
    if ADMIN_ROLE_ID is None:
        return member.guild_permissions.manage_guild
    return any(role.id == ADMIN_ROLE_ID for role in member.roles)


# ==============================================================================
# DISCORD BOT SETUP
# ==============================================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ==============================================================================
# PERSISTENT VIEW (CLAIM BUTTON)
# ==============================================================================


class ShiftClaimView(discord.ui.View):
    """Persistent view containing the claim button."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Claim shift",
        style=discord.ButtonStyle.green,
        custom_id="shift_claim_button",
    )
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            message = interaction.message
            status, total_hours = can_claim_and_update(message.id, interaction.user.id)

            if status == "not_found":
                await interaction.response.send_message(
                    "I couldn't find this shift in the database.",
                    ephemeral=True,
                )
                return

            if status == "cancelled":
                await interaction.response.send_message(
                    "This shift has been cancelled.",
                    ephemeral=True,
                )
                return

            if status == "own_shift":
                await interaction.response.send_message(
                    "You can't claim a shift you dropped yourself.",
                    ephemeral=True,
                )
                return

            if status == "already_claimed":
                await interaction.response.send_message(
                    "This shift has already been claimed.",
                    ephemeral=True,
                )
                return

            if status == "over_cap":
                await interaction.response.send_message(
                    f"You're at **{total_hours:.1f}h** in the last 7 days.\n"
                    f"You can only claim this shift in the last **{HEAVY_LOCK_WINDOW_MINUTES} minutes** before it starts.",
                    ephemeral=True,
                )
                return

            # Success - update embed
            embed = message.embeds[0] if message.embeds else discord.Embed(title="Shift claimed")
            embed.add_field(name="Claimed by", value=interaction.user.mention, inline=False)
            embed.color = 0x808080

            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
                    child.label = "Claimed"
                    child.style = discord.ButtonStyle.gray

            await message.edit(embed=embed, view=self)
            await interaction.response.send_message("You claimed this shift!", ephemeral=True)

        except Exception as e:
            logger.error(f"Error in claim button handler: {e}")
            await interaction.response.send_message(
                "An error occurred. Please try again.",
                ephemeral=True,
            )


# ==============================================================================
# BOT EVENTS
# ==============================================================================


@bot.event
async def on_ready():
    bot.add_view(ShiftClaimView())
    init_db()
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info("Persistent views registered")
    logger.info("TIP: If slash commands don't appear, use /sync_commands or !sync")


@bot.event
async def on_error(event: str, *args, **kwargs):
    logger.exception(f"Error in event {event}")


# ==============================================================================
# SCHEDULE MANAGEMENT COMMANDS
# ==============================================================================


@bot.tree.command(
    name="schedule_add",
    description="Add a time slot to your schedule.",
)
@app_commands.describe(
    day="Day of the week.",
    hour="Hour (0-23).",
    minute="Minute (0-59, default 0).",
    timezone="Your timezone (e.g., America/New_York). Handles DST automatically.",
)
@app_commands.choices(day=[
    app_commands.Choice(name="Monday", value=0),
    app_commands.Choice(name="Tuesday", value=1),
    app_commands.Choice(name="Wednesday", value=2),
    app_commands.Choice(name="Thursday", value=3),
    app_commands.Choice(name="Friday", value=4),
    app_commands.Choice(name="Saturday", value=5),
    app_commands.Choice(name="Sunday", value=6),
])
async def schedule_add(
    interaction: discord.Interaction,
    day: int,
    hour: int,
    timezone: str = DEFAULT_TIMEZONE,
    minute: int = 0,
):
    if not has_mod_role(interaction.user):
        await interaction.response.send_message(
            "You need the moderator role to manage schedules.",
            ephemeral=True,
        )
        return

    # Validate inputs
    if not (0 <= hour <= 23):
        await interaction.response.send_message("Hour must be between 0 and 23.", ephemeral=True)
        return
    if not (0 <= minute <= 59):
        await interaction.response.send_message("Minute must be between 0 and 59.", ephemeral=True)
        return

    # Validate timezone
    try:
        tz = ZoneInfo(timezone)
        offset_display = get_utc_offset_display(timezone)
    except Exception:
        await interaction.response.send_message(
            f"Invalid timezone: `{timezone}`\n"
            f"Use a valid IANA timezone like `America/New_York` or `Europe/London`.",
            ephemeral=True,
        )
        return

    success, message = add_schedule_slot(interaction.user.id, day, hour, minute, timezone)

    if success:
        slot_display = format_slot_for_display(day, hour, minute, timezone)
        await interaction.response.send_message(
            f"Added to your schedule: **{slot_display}**\n"
            f"Current offset: {offset_display} (adjusts automatically for DST)",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(message, ephemeral=True)


@schedule_add.autocomplete("timezone")
async def timezone_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete for timezone parameter."""
    choices = []
    current_lower = current.lower()

    for tz in COMMON_TIMEZONES:
        if current_lower in tz.lower():
            offset = get_utc_offset_display(tz)
            choices.append(app_commands.Choice(name=f"{tz} ({offset})", value=tz))

    return choices[:25]


@bot.tree.command(
    name="schedule_remove",
    description="Remove a time slot from your schedule.",
)
@app_commands.describe(
    slot="Select a slot to remove.",
)
async def schedule_remove(
    interaction: discord.Interaction,
    slot: str,
):
    if not has_mod_role(interaction.user):
        await interaction.response.send_message(
            "You need the moderator role to manage schedules.",
            ephemeral=True,
        )
        return

    try:
        parts = slot.split("|")
        day_of_week = int(parts[0])
        hour = int(parts[1])
        minute = int(parts[2]) if len(parts) > 2 else 0
    except Exception:
        await interaction.response.send_message(
            "Invalid slot selected. Please choose from the dropdown.",
            ephemeral=True,
        )
        return

    success, message = remove_schedule_slot(interaction.user.id, day_of_week, hour, minute)
    await interaction.response.send_message(message, ephemeral=True)


@schedule_remove.autocomplete("slot")
async def schedule_remove_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete showing user's current schedule slots."""
    slots = get_schedule_for_user(interaction.user.id)
    choices = []

    for day_of_week, hour, minute, tz in slots:
        label = format_slot_for_display(day_of_week, hour, minute, tz)
        value = f"{day_of_week}|{hour}|{minute}"

        if current and current.lower() not in label.lower():
            continue

        choices.append(app_commands.Choice(name=label, value=value))

    return choices[:25]


@bot.tree.command(
    name="schedule_view",
    description="View a moderator's schedule.",
)
@app_commands.describe(
    user="User to view (defaults to yourself).",
)
async def schedule_view(
    interaction: discord.Interaction,
    user: discord.Member | None = None,
):
    target = user or interaction.user
    slots = get_schedule_for_user(target.id)

    if not slots:
        await interaction.response.send_message(
            f"{target.mention} has no schedule configured.\n"
            f"Use `/schedule_add` to add time slots.",
            ephemeral=True,
        )
        return

    # Group by timezone for cleaner display
    by_tz: dict[str, list[str]] = {}
    for day_of_week, hour, minute, tz in slots:
        day_name = WEEKDAY_TO_DAY_NAME.get(day_of_week, "Unknown").capitalize()
        if minute == 0:
            time_str = f"{day_name} {hour:02d}:00"
        else:
            time_str = f"{day_name} {hour:02d}:{minute:02d}"

        if tz not in by_tz:
            by_tz[tz] = []
        by_tz[tz].append(time_str)

    lines = [f"**Schedule for {target.mention}:**\n"]
    for tz, times in by_tz.items():
        offset = get_utc_offset_display(tz)
        lines.append(f"**{tz}** ({offset}):")
        for t in times:
            lines.append(f"  - {t}")

    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(
    name="schedule_add_admin",
    description="Add a schedule slot for a moderator (admin only).",
)
@app_commands.describe(
    user="Moderator to add schedule for.",
    day="Day of the week.",
    hour="Hour (0-23).",
    minute="Minute (0-59, default 0).",
    timezone="Timezone (e.g., America/New_York). Handles DST automatically.",
)
@app_commands.choices(day=[
    app_commands.Choice(name="Monday", value=0),
    app_commands.Choice(name="Tuesday", value=1),
    app_commands.Choice(name="Wednesday", value=2),
    app_commands.Choice(name="Thursday", value=3),
    app_commands.Choice(name="Friday", value=4),
    app_commands.Choice(name="Saturday", value=5),
    app_commands.Choice(name="Sunday", value=6),
])
async def schedule_add_admin(
    interaction: discord.Interaction,
    user: discord.Member,
    day: int,
    hour: int,
    timezone: str = DEFAULT_TIMEZONE,
    minute: int = 0,
):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message(
            "You need administrator permissions to use this command.",
            ephemeral=True,
        )
        return

    # Validate inputs
    if not (0 <= hour <= 23):
        await interaction.response.send_message("Hour must be between 0 and 23.", ephemeral=True)
        return
    if not (0 <= minute <= 59):
        await interaction.response.send_message("Minute must be between 0 and 59.", ephemeral=True)
        return

    # Validate timezone
    try:
        tz = ZoneInfo(timezone)
        offset_display = get_utc_offset_display(timezone)
    except Exception:
        await interaction.response.send_message(
            f"Invalid timezone: `{timezone}`\n"
            f"Use a valid IANA timezone like `America/New_York` or `Europe/London`.",
            ephemeral=True,
        )
        return

    success, message = add_schedule_slot(user.id, day, hour, minute, timezone)

    if success:
        slot_display = format_slot_for_display(day, hour, minute, timezone)
        await interaction.response.send_message(
            f"Added to {user.mention}'s schedule: **{slot_display}**\n"
            f"Current offset: {offset_display} (adjusts automatically for DST)",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            f"Could not add slot: {message}",
            ephemeral=True,
        )


@schedule_add_admin.autocomplete("timezone")
async def schedule_add_admin_timezone_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete for timezone parameter in admin schedule add."""
    choices = []
    current_lower = current.lower()

    for tz in COMMON_TIMEZONES:
        if current_lower in tz.lower():
            offset = get_utc_offset_display(tz)
            choices.append(app_commands.Choice(name=f"{tz} ({offset})", value=tz))

    return choices[:25]


@bot.tree.command(
    name="schedule_clear",
    description="Clear all slots from your schedule.",
)
@app_commands.describe(
    user="User to clear (admin only, defaults to yourself).",
)
async def schedule_clear(
    interaction: discord.Interaction,
    user: discord.Member | None = None,
):
    target = user or interaction.user

    # Only admins can clear other people's schedules
    if target.id != interaction.user.id and not has_admin_role(interaction.user):
        await interaction.response.send_message(
            "You can only clear your own schedule.",
            ephemeral=True,
        )
        return

    deleted = clear_schedule_for_user(target.id)

    if deleted > 0:
        await interaction.response.send_message(
            f"Cleared **{deleted}** slot(s) from {target.mention}'s schedule.",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            f"{target.mention} had no schedule to clear.",
            ephemeral=True,
        )


# ==============================================================================
# SHIFT COMMANDS
# ==============================================================================


@bot.tree.command(
    name="sync_commands",
    description="Sync slash commands with Discord (admin only).",
)
async def sync_commands(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message(
            "You need administrator permissions.",
            ephemeral=True,
        )
        return

    try:
        await interaction.response.defer(ephemeral=True)
        synced = await bot.tree.sync()
        await interaction.followup.send(f"Synced **{len(synced)}** command(s)!", ephemeral=True)
        logger.info(f"Commands synced by {interaction.user}: {len(synced)} commands")
    except Exception as e:
        logger.error(f"Error syncing commands: {e}")
        await interaction.followup.send(f"Error: {e}", ephemeral=True)


@bot.tree.command(
    name="drop_shift",
    description="Post a generic shift with a claim button.",
)
@app_commands.describe(
    description="Description for the shift.",
)
async def drop_shift(
    interaction: discord.Interaction,
    description: str = "Upcoming shift",
):
    if interaction.channel.id != ALLOWED_CHANNEL_ID:
        await interaction.response.send_message(
            f"This command can only be used in <#{ALLOWED_CHANNEL_ID}>.",
            ephemeral=True,
        )
        return

    if not has_mod_role(interaction.user):
        await interaction.response.send_message(
            "You need the moderator role to drop shifts.",
            ephemeral=True,
        )
        return

    try:
        final_desc = description.strip() or "Upcoming shift"

        embed = discord.Embed(
            title="Shift Available",
            description=final_desc,
            color=0x00FF00,
        )
        embed.add_field(name="Posted by", value=interaction.user.mention, inline=False)

        view = ShiftClaimView()
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()

        save_shift(
            message_id=msg.id,
            channel_id=msg.channel.id,
            description=final_desc,
            created_by=interaction.user.id,
        )
        logger.info(f"Generic shift dropped by {interaction.user}")

    except Exception as e:
        logger.error(f"Error in drop_shift: {e}")
        if not interaction.response.is_done():
            await interaction.response.send_message("An error occurred.", ephemeral=True)


@bot.tree.command(
    name="drop_mod_shift",
    description="Drop one of YOUR scheduled shifts for others to claim.",
)
@app_commands.describe(
    target="Select yourself (admins can select others).",
    slot="One of this moderator's scheduled times.",
    duration_hours="Duration in hours (0.25 to 24).",
    date="Specific date (YYYY-MM-DD). Leave empty for next occurrence.",
)
async def drop_mod_shift(
    interaction: discord.Interaction,
    target: discord.Member,
    slot: str,
    duration_hours: float = 1.0,
    date: str | None = None,
):
    if interaction.channel.id != ALLOWED_CHANNEL_ID:
        await interaction.response.send_message(
            f"This command can only be used in <#{ALLOWED_CHANNEL_ID}>.",
            ephemeral=True,
        )
        return

    if not has_mod_role(interaction.user):
        await interaction.response.send_message(
            "You need the moderator role.",
            ephemeral=True,
        )
        return

    if target.id != interaction.user.id and not has_admin_role(interaction.user):
        await interaction.response.send_message(
            "You can only drop your own shifts.",
            ephemeral=True,
        )
        return

    if not (MIN_DURATION_HOURS <= duration_hours <= MAX_DURATION_HOURS):
        await interaction.response.send_message(
            f"Duration must be between {MIN_DURATION_HOURS} and {MAX_DURATION_HOURS} hours.",
            ephemeral=True,
        )
        return

    try:
        # Parse slot value (format: "day|hour|minute|timezone")
        parts = slot.split("|")
        day_of_week = int(parts[0])
        hour = int(parts[1])
        minute = int(parts[2]) if len(parts) > 2 else 0
        tz_name = parts[3] if len(parts) > 3 else "UTC"
    except Exception:
        await interaction.response.send_message(
            "Invalid slot. Please select from the dropdown.",
            ephemeral=True,
        )
        return

    # Check if user has schedule
    if not user_has_schedule(target.id):
        await interaction.response.send_message(
            f"{target.mention} has no schedule configured.\n"
            f"Use `/schedule_add` to set up a schedule first.",
            ephemeral=True,
        )
        return

    try:
        if date:
            # Parse specific date
            parsed_date = datetime.strptime(date.strip(), "%Y-%m-%d")

            # Validate day of week matches
            if parsed_date.weekday() != day_of_week:
                expected_day = WEEKDAY_TO_DAY_NAME.get(day_of_week, "Unknown").capitalize()
                actual_day = WEEKDAY_TO_DAY_NAME.get(parsed_date.weekday(), "Unknown").capitalize()
                await interaction.response.send_message(
                    f"Date mismatch! You selected a **{expected_day}** slot, "
                    f"but **{date}** is a **{actual_day}**.",
                    ephemeral=True,
                )
                return

            # Create datetime in user's timezone
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = ZoneInfo("UTC")

            shift_start_local = parsed_date.replace(
                hour=hour, minute=minute, second=0, microsecond=0, tzinfo=tz
            )
            shift_start = shift_start_local.astimezone(timezone.utc)

            # Validate not in past
            if shift_start < datetime.now(timezone.utc):
                await interaction.response.send_message(
                    f"The date **{date}** at **{hour:02d}:{minute:02d}** is in the past.",
                    ephemeral=True,
                )
                return
        else:
            # Calculate next occurrence
            shift_start = next_datetime_for_slot(day_of_week, hour, minute, tz_name)

        # Create description
        day_name = WEEKDAY_TO_DAY_NAME.get(day_of_week, "Unknown").capitalize()
        time_str = f"{hour:02d}:{minute:02d}" if minute else f"{hour:02d}:00"
        desc = f"Dropped scheduled shift for {target.mention}: **{day_name}** at **{time_str}** ({tz_name})"

        # Create embed
        embed = discord.Embed(
            title="Moderator Shift Available",
            description=desc,
            color=0x00AAFF,
        )
        embed.add_field(name="Posted by", value=interaction.user.mention, inline=True)
        embed.add_field(name="Originally assigned to", value=target.mention, inline=True)
        embed.add_field(
            name="Start Time",
            value=f"<t:{int(shift_start.timestamp())}:F>",
            inline=False,
        )
        embed.add_field(name="Duration", value=f"{duration_hours:.1f} hour(s)", inline=True)

        view = ShiftClaimView()
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()

        save_shift(
            message_id=msg.id,
            channel_id=msg.channel.id,
            description=desc,
            created_by=interaction.user.id,
            start_time_utc=shift_start,
            duration_hours=float(duration_hours),
            assigned_user_id=target.id,
        )

        logger.info(f"Mod shift dropped by {interaction.user}: {target.display_name}'s {day_name} {time_str} slot")

    except ValueError:
        await interaction.response.send_message(
            "Invalid date format. Use **YYYY-MM-DD**.",
            ephemeral=True,
        )
    except Exception as e:
        logger.error(f"Error in drop_mod_shift: {e}")
        if not interaction.response.is_done():
            await interaction.response.send_message("An error occurred.", ephemeral=True)


@drop_mod_shift.autocomplete("slot")
async def drop_mod_shift_slot_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete for slot parameter - pulls from database."""
    namespace = interaction.namespace
    target = getattr(namespace, "target", None)

    user_id = None
    if hasattr(target, "id"):
        user_id = target.id
    elif isinstance(target, str):
        try:
            user_id = int(target)
        except ValueError:
            pass

    if user_id is None:
        return []

    slots = get_schedule_for_user(user_id)
    choices = []

    for day_of_week, hour, minute, tz in slots:
        label = format_slot_for_display(day_of_week, hour, minute, tz)
        value = f"{day_of_week}|{hour}|{minute}|{tz}"

        if current and current.lower() not in label.lower():
            continue

        choices.append(app_commands.Choice(name=label, value=value))

    return choices[:25]


@bot.tree.command(
    name="shift_cancel",
    description="Cancel a shift you posted.",
)
@app_commands.describe(
    shift="Select a shift to cancel.",
)
async def shift_cancel_command(
    interaction: discord.Interaction,
    shift: str,
):
    if interaction.channel.id != ALLOWED_CHANNEL_ID:
        await interaction.response.send_message(
            f"This command can only be used in <#{ALLOWED_CHANNEL_ID}>.",
            ephemeral=True,
        )
        return

    try:
        msg_id = int(shift.strip())
    except ValueError:
        await interaction.response.send_message(
            "Invalid shift selected.",
            ephemeral=True,
        )
        return

    try:
        is_admin = has_admin_role(interaction.user)
        status = cancel_shift(msg_id, interaction.user.id, is_admin=is_admin)

        if status == "not_found":
            await interaction.response.send_message("Shift not found.", ephemeral=True)
            return
        if status == "not_owner":
            await interaction.response.send_message("You can only cancel your own shifts.", ephemeral=True)
            return
        if status == "already_cancelled":
            await interaction.response.send_message("Already cancelled.", ephemeral=True)
            return

        # Update message
        try:
            channel = bot.get_channel(interaction.channel.id)
            message = await channel.fetch_message(msg_id)

            embed = message.embeds[0] if message.embeds else discord.Embed()
            embed.title = "Shift Cancelled"
            embed.color = 0xFF0000
            embed.add_field(name="Cancelled by", value=interaction.user.mention, inline=False)

            view = discord.ui.View()
            button = discord.ui.Button(label="Cancelled", style=discord.ButtonStyle.gray, disabled=True)
            view.add_item(button)

            await message.edit(embed=embed, view=view)
        except discord.NotFound:
            pass
        except Exception as e:
            logger.warning(f"Could not update cancelled shift message: {e}")

        was_claimed = (status == "cancelled_claimed")
        if was_claimed:
            await interaction.response.send_message(
                "Shift cancelled. **Note:** This shift was claimed - the claimer lost coverage.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message("Shift cancelled.", ephemeral=True)

    except Exception as e:
        logger.error(f"Error in shift_cancel: {e}")
        await interaction.response.send_message("An error occurred.", ephemeral=True)


@shift_cancel_command.autocomplete("shift")
async def shift_cancel_autocomplete(interaction: discord.Interaction, current: str):
    is_admin = has_admin_role(interaction.user)
    cancellable_shifts = get_cancellable_shifts_for_user(interaction.user.id, is_admin=is_admin)

    choices = []
    for message_id, description, start_time, is_claimed in cancellable_shifts:
        prefix = "[CLAIMED] " if is_claimed else ""
        max_len = 80 - len(prefix)
        if len(description) > max_len:
            label = prefix + description[:max_len - 3] + "..."
        else:
            label = prefix + description

        if current and current.lower() not in label.lower():
            continue

        choices.append(app_commands.Choice(name=label, value=str(message_id)))

    return choices[:25]


@bot.tree.command(
    name="shift_edit",
    description="Edit a shift you posted (only unclaimed shifts can be edited).",
)
@app_commands.describe(
    shift="Select a shift to edit.",
    description="New description for the shift.",
    date="New date (YYYY-MM-DD). Leave empty to keep current date.",
    time="New time (HH:MM in 24-hour format). Leave empty to keep current time.",
    duration_hours="New duration in hours (0.25 to 24).",
)
async def shift_edit_command(
    interaction: discord.Interaction,
    shift: str,
    description: str | None = None,
    date: str | None = None,
    time: str | None = None,
    duration_hours: float | None = None,
):
    if interaction.channel.id != ALLOWED_CHANNEL_ID:
        await interaction.response.send_message(
            f"This command can only be used in <#{ALLOWED_CHANNEL_ID}>.",
            ephemeral=True,
        )
        return

    if not has_mod_role(interaction.user):
        await interaction.response.send_message(
            "You need the moderator role to edit shifts.",
            ephemeral=True,
        )
        return

    try:
        msg_id = int(shift.strip())
    except ValueError:
        await interaction.response.send_message(
            "Invalid shift selected.",
            ephemeral=True,
        )
        return

    try:
        is_admin = has_admin_role(interaction.user)
        
        # Get current shift details
        shift_data = get_shift_details(msg_id)
        if shift_data is None:
            await interaction.response.send_message("Shift not found.", ephemeral=True)
            return
        
        created_by, old_desc, old_start, old_duration, assigned_user_id, claimed_by, cancelled, channel_id = shift_data
        
        # Permission check
        if created_by != interaction.user.id and not is_admin:
            await interaction.response.send_message(
                "You can only edit your own shifts.",
                ephemeral=True,
            )
            return
        
        # Can't edit if claimed or cancelled
        if claimed_by is not None:
            await interaction.response.send_message(
                "You cannot edit a shift that has already been claimed.",
                ephemeral=True,
            )
            return
        
        if cancelled:
            await interaction.response.send_message(
                "You cannot edit a cancelled shift.",
                ephemeral=True,
            )
            return
        
        # Parse new start time if date/time provided
        new_start_time = None
        if date or time:
            if not date or not time:
                await interaction.response.send_message(
                    "Both date and time must be provided together to change the start time.",
                    ephemeral=True,
                )
                return
            
            try:
                # Parse date
                parsed_date = datetime.strptime(date.strip(), "%Y-%m-%d")
                
                # Parse time
                time_parts = time.strip().split(":")
                if len(time_parts) != 2:
                    raise ValueError("Invalid time format")
                hour = int(time_parts[0])
                minute = int(time_parts[1])
                
                if not (0 <= hour <= 23) or not (0 <= minute <= 59):
                    raise ValueError("Invalid time range")
                
                # Create datetime in UTC (assuming user provides in UTC, or we could add timezone param)
                new_start_time = parsed_date.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                ).replace(tzinfo=timezone.utc)
                
                # Validate not in past
                if new_start_time < datetime.now(timezone.utc):
                    await interaction.response.send_message(
                        f"The date and time you provided is in the past.",
                        ephemeral=True,
                    )
                    return
                    
            except ValueError as e:
                await interaction.response.send_message(
                    f"Invalid date or time format. Use **YYYY-MM-DD** for date and **HH:MM** (24-hour) for time.\n"
                    f"Error: {str(e)}",
                    ephemeral=True,
                )
                return
        
        # Validate duration if provided
        if duration_hours is not None:
            if not (MIN_DURATION_HOURS <= duration_hours <= MAX_DURATION_HOURS):
                await interaction.response.send_message(
                    f"Duration must be between {MIN_DURATION_HOURS} and {MAX_DURATION_HOURS} hours.",
                    ephemeral=True,
                )
                return
        
        # Check if any changes provided
        if description is None and new_start_time is None and duration_hours is None:
            await interaction.response.send_message(
                "Please provide at least one field to update (description, date/time, or duration).",
                ephemeral=True,
            )
            return
        
        # Update the shift
        status, updated_data = update_shift(
            msg_id,
            interaction.user.id,
            is_admin,
            description=description.strip() if description else None,
            start_time_utc=new_start_time,
            duration_hours=duration_hours,
        )
        
        if status == "not_found":
            await interaction.response.send_message("Shift not found.", ephemeral=True)
            return
        elif status == "not_owner":
            await interaction.response.send_message("You can only edit your own shifts.", ephemeral=True)
            return
        elif status == "already_claimed":
            await interaction.response.send_message("This shift has already been claimed.", ephemeral=True)
            return
        elif status == "cancelled":
            await interaction.response.send_message("This shift has been cancelled.", ephemeral=True)
            return
        elif status == "invalid_duration":
            await interaction.response.send_message(
                f"Duration must be between {MIN_DURATION_HOURS} and {MAX_DURATION_HOURS} hours.",
                ephemeral=True,
            )
            return
        
        # Update Discord message
        try:
            channel = bot.get_channel(channel_id)
            if channel:
                message = await channel.fetch_message(msg_id)
                
                embed = message.embeds[0] if message.embeds else discord.Embed()
                
                # Update description if changed
                if description:
                    embed.description = description.strip()
                
                # Update start time field if changed
                if new_start_time:
                    # Find and update the "Start Time" field
                    for i, field in enumerate(embed.fields):
                        if field.name == "Start Time":
                            embed.set_field_at(
                                i,
                                name="Start Time",
                                value=f"<t:{int(new_start_time.timestamp())}:F>",
                                inline=False,
                            )
                            break
                    else:
                        # Field doesn't exist, add it
                        embed.add_field(
                            name="Start Time",
                            value=f"<t:{int(new_start_time.timestamp())}:F>",
                            inline=False,
                        )
                
                # Update duration field if changed
                if duration_hours is not None:
                    for i, field in enumerate(embed.fields):
                        if field.name == "Duration":
                            embed.set_field_at(
                                i,
                                name="Duration",
                                value=f"{duration_hours:.1f} hour(s)",
                                inline=True,
                            )
                            break
                    else:
                        # Field doesn't exist, add it
                        embed.add_field(
                            name="Duration",
                            value=f"{duration_hours:.1f} hour(s)",
                            inline=True,
                        )
                
                # Add/edit "Last edited" field
                for i, field in enumerate(embed.fields):
                    if field.name == "Last edited":
                        embed.set_field_at(
                            i,
                            name="Last edited",
                            value=f"by {interaction.user.mention}",
                            inline=False,
                        )
                        break
                else:
                    embed.add_field(
                        name="Last edited",
                        value=f"by {interaction.user.mention}",
                        inline=False,
                    )
                
                await message.edit(embed=embed)
        except discord.NotFound:
            pass
        except Exception as e:
            logger.warning(f"Could not update edited shift message: {e}")
        
        # Build response message
        changes = []
        if description:
            changes.append("description")
        if new_start_time:
            changes.append("start time")
        if duration_hours is not None:
            changes.append("duration")
        
        await interaction.response.send_message(
            f"Shift updated successfully! Changed: **{', '.join(changes)}**.",
            ephemeral=True,
        )
        
    except Exception as e:
        logger.error(f"Error in shift_edit: {e}")
        await interaction.response.send_message("An error occurred.", ephemeral=True)


@shift_edit_command.autocomplete("shift")
async def shift_edit_autocomplete(interaction: discord.Interaction, current: str):
    is_admin = has_admin_role(interaction.user)
    editable_shifts = get_editable_shifts_for_user(interaction.user.id, is_admin=is_admin)

    choices = []
    for message_id, description, start_time, duration in editable_shifts:
        max_len = 80
        if len(description) > max_len:
            label = description[:max_len - 3] + "..."
        else:
            label = description

        if current and current.lower() not in label.lower():
            continue

        choices.append(app_commands.Choice(name=label, value=str(message_id)))

    return choices[:25]


@bot.tree.command(
    name="shift_stats",
    description="Show shift statistics for a moderator.",
)
@app_commands.describe(
    user="User to check (defaults to yourself).",
)
async def shift_stats(
    interaction: discord.Interaction,
    user: discord.Member | None = None,
):
    try:
        target = user or interaction.user
        count, hours = get_shift_count_for_user(target.id)

        await interaction.response.send_message(
            f"**Shift Stats for {target.mention}**\n"
            f"- Shifts claimed: **{count}**\n"
            f"- Total hours: **{hours:.1f}h**",
            ephemeral=True,
        )
    except Exception as e:
        logger.error(f"Error in shift_stats: {e}")
        await interaction.response.send_message("An error occurred.", ephemeral=True)


# ==============================================================================
# PREFIX COMMANDS (FALLBACK)
# ==============================================================================


@bot.command()
async def ping(ctx):
    """Check if bot is responsive."""
    await ctx.send("Pong! Bot is online.")


@bot.command()
@commands.has_permissions(manage_guild=True)
async def sync(ctx):
    """Sync slash commands."""
    try:
        synced = await bot.tree.sync()
        await ctx.send(f"Synced **{len(synced)}** command(s)!")
        logger.info(f"Commands synced via prefix command by {ctx.author}")
    except Exception as e:
        await ctx.send(f"Error: {e}")
        logger.error(f"Error syncing commands: {e}")


# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================


if __name__ == "__main__":
    if not TOKEN:
        logger.error("ERROR: DISCORD_TOKEN not set!")
        exit(1)

    if not DATABASE_URL:
        logger.error("ERROR: DATABASE_URL not set!")
        exit(1)

    try:
        init_db()
    except psycopg2.Error as e:
        logger.error(f"Failed to initialize database: {e}")
        exit(1)

    logger.info("Starting bot...")

    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        logger.error("Invalid bot token!")
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
