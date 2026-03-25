"""Outlook calendar integration for smart meeting context.

Uses the Outlook COM API (win32com) to query the user's calendar and match
the current recording to a calendar event. This provides:
- Meeting subject for descriptive folder naming
- Organizer and attendee names
- Meeting body/agenda for context

Works with locally installed Outlook on Windows. Requires pywin32.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CalendarEvent:
    """Represents a matched Outlook calendar event."""

    subject: str = ""
    organizer: str = ""
    attendees: list[str] = field(default_factory=list)
    start_time: str = ""
    end_time: str = ""
    location: str = ""
    body_preview: str = ""
    is_recurring: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def find_current_meeting(buffer_minutes: int = 10) -> Optional[CalendarEvent]:
    """Find the Outlook calendar event happening right now.

    Searches events within a time window around the current time to account
    for meetings starting slightly early/late.

    Args:
        buffer_minutes: Minutes before/after current time to search.

    Returns:
        CalendarEvent if a matching event is found, None otherwise.
    """
    try:
        import win32com.client
        import pywintypes
    except ImportError:
        logger.warning("pywin32 not installed. Outlook calendar integration disabled.")
        return None

    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")
        calendar_folder = namespace.GetDefaultFolder(9)  # olFolderCalendar = 9
    except Exception:
        logger.warning("Could not connect to Outlook. Is Outlook running?")
        return None

    now = datetime.now()
    search_start = now - timedelta(minutes=buffer_minutes)
    search_end = now + timedelta(minutes=buffer_minutes)

    start_str = search_start.strftime("%m/%d/%Y %H:%M %p")
    end_str = search_end.strftime("%m/%d/%Y %H:%M %p")

    try:
        items = calendar_folder.Items
        items.IncludeRecurrences = True
        items.Sort("[Start]")

        restriction = (
            f"[Start] <= '{end_str}' AND [End] >= '{start_str}'"
        )
        restricted = items.Restrict(restriction)

        best_match = None
        best_overlap = timedelta(0)

        for item in restricted:
            try:
                item_start = _com_date_to_datetime(item.Start)
                item_end = _com_date_to_datetime(item.End)

                # Calculate overlap with current time window
                overlap_start = max(item_start, search_start)
                overlap_end = min(item_end, search_end)
                overlap = overlap_end - overlap_start

                if overlap > best_overlap:
                    best_overlap = overlap
                    attendee_list = _extract_attendees(item)
                    body_preview = (item.Body or "")[:500].strip()

                    best_match = CalendarEvent(
                        subject=item.Subject or "",
                        organizer=item.Organizer or "",
                        attendees=attendee_list,
                        start_time=item_start.isoformat(),
                        end_time=item_end.isoformat(),
                        location=item.Location or "",
                        body_preview=body_preview,
                        is_recurring=item.IsRecurring,
                    )
            except Exception:
                logger.debug("Skipping calendar item", exc_info=True)
                continue

        if best_match:
            logger.info(
                "Matched calendar event: '%s' (%s - %s)",
                best_match.subject,
                best_match.start_time,
                best_match.end_time,
            )
        else:
            logger.info("No matching calendar event found.")

        return best_match

    except Exception:
        logger.exception("Error querying Outlook calendar")
        return None


def get_upcoming_meetings(window_minutes: int = 60) -> list[CalendarEvent]:
    """Get calendar events happening now or soon.

    Returns events from ``window_minutes`` ago through ``window_minutes``
    into the future, sorted by start time.  Useful for a UI picker that
    lets the user choose a meeting title.
    """
    try:
        import win32com.client
    except ImportError:
        return []

    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")
        calendar_folder = namespace.GetDefaultFolder(9)
    except Exception:
        logger.debug("Could not connect to Outlook for upcoming meetings")
        return []

    now = datetime.now()
    search_start = now - timedelta(minutes=window_minutes)
    search_end = now + timedelta(minutes=window_minutes)

    start_str = search_start.strftime("%m/%d/%Y %H:%M %p")
    end_str = search_end.strftime("%m/%d/%Y %H:%M %p")

    events: list[CalendarEvent] = []
    try:
        items = calendar_folder.Items
        items.IncludeRecurrences = True
        items.Sort("[Start]")
        restricted = items.Restrict(
            f"[Start] <= '{end_str}' AND [End] >= '{start_str}'"
        )
        for item in restricted:
            try:
                item_start = _com_date_to_datetime(item.Start)
                item_end = _com_date_to_datetime(item.End)
                events.append(CalendarEvent(
                    subject=item.Subject or "",
                    organizer=item.Organizer or "",
                    attendees=_extract_attendees(item),
                    start_time=item_start.isoformat(),
                    end_time=item_end.isoformat(),
                    location=item.Location or "",
                    body_preview="",
                    is_recurring=item.IsRecurring,
                ))
            except Exception:
                continue
    except Exception:
        logger.debug("Error querying upcoming meetings", exc_info=True)

    return events


def _com_date_to_datetime(com_date) -> datetime:
    """Convert a COM date object to a Python datetime."""
    try:
        import pywintypes
        if isinstance(com_date, pywintypes.TimeType):
            return datetime(
                com_date.year, com_date.month, com_date.day,
                com_date.hour, com_date.minute, com_date.second,
            )
    except (ImportError, AttributeError):
        pass
    # Fallback: try treating it as a datetime-like object
    return datetime(
        com_date.year, com_date.month, com_date.day,
        com_date.hour, com_date.minute, com_date.second,
    )


def _extract_attendees(item) -> list[str]:
    """Extract attendee names from an Outlook appointment item."""
    attendees = []
    try:
        recipients = item.Recipients
        for i in range(1, recipients.Count + 1):
            recipient = recipients.Item(i)
            name = recipient.Name or recipient.Address or ""
            if name:
                attendees.append(name)
    except Exception:
        logger.debug("Could not extract attendees", exc_info=True)
    return attendees


def get_meeting_folder_name(event: CalendarEvent, app_name: str) -> str:
    """Generate a descriptive folder name from a calendar event.

    Args:
        event: The matched calendar event.
        app_name: The meeting application name (e.g., "Zoom").

    Returns:
        A sanitized folder name like "2026-02-16_14-30-00_Weekly_Standup_Zoom"
    """
    subject = event.subject.strip()
    if not subject:
        return ""

    # Sanitize: keep alphanumeric, spaces become underscores
    safe_subject = "".join(
        c if c.isalnum() or c in "._- " else "" for c in subject
    ).strip()
    safe_subject = safe_subject.replace(" ", "_")

    # Truncate long subjects
    if len(safe_subject) > 60:
        safe_subject = safe_subject[:60].rstrip("_")

    return safe_subject
