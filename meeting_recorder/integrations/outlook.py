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


def find_current_meeting(
    buffer_minutes: int = 10, read_details: bool = False
) -> Optional[CalendarEvent]:
    """Find the Outlook calendar event happening right now.

    Searches events within a time window around the current time to account
    for meetings starting slightly early/late.

    Args:
        buffer_minutes: Minutes before/after current time to search.
        read_details: Also read organizer/attendees/body. These properties
            trigger Outlook's programmatic-access security prompt; leave False
            to stay prompt-free (subject/time/location only).

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
                    if read_details:
                        attendee_list = _extract_attendees(item)
                        organizer = item.Organizer or ""
                        body_preview = (item.Body or "")[:500].strip()
                    else:
                        attendee_list = []
                        organizer = ""
                        body_preview = ""

                    best_match = CalendarEvent(
                        subject=item.Subject or "",
                        organizer=organizer,
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


def get_upcoming_meetings(
    window_minutes: int = 60,
    reference_time: datetime | None = None,
    read_details: bool = False,
) -> list[CalendarEvent]:
    """Get calendar events around a reference time.

    Args:
        window_minutes: Search window size (±minutes from reference).
        reference_time: Center of the search window.  Defaults to now.
        read_details: Also read organizer/attendees (security-prompt-guarded
            COM properties — see find_current_meeting).

    Returns events from ``reference_time - window_minutes`` through
    ``reference_time + window_minutes``, sorted by start time.
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

    center = reference_time or datetime.now()
    search_start = center - timedelta(minutes=window_minutes)
    search_end = center + timedelta(minutes=window_minutes)

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
                    organizer=(item.Organizer or "") if read_details else "",
                    attendees=_extract_attendees(item) if read_details else [],
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
