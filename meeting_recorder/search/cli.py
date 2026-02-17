"""Command-line interface for searching meeting recordings."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from meeting_recorder.config import Config
from meeting_recorder.search.index import RecordingIndex


def main(argv: list[str] | None = None) -> int:
    """Run the search CLI.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    parser = argparse.ArgumentParser(
        prog="meeting-recorder-search",
        description="Search meeting recordings",
    )
    parser.add_argument("query", nargs="?", default="", help="Search query (FTS5 syntax)")
    parser.add_argument("--speaker", default="", help="Filter by speaker name")
    parser.add_argument("--from", dest="date_from", default="", help="Filter from date (ISO format)")
    parser.add_argument("--to", dest="date_to", default="", help="Filter to date (ISO format)")
    parser.add_argument("--attendee", default="", help="Filter by attendee name")
    parser.add_argument("--subject", default="", help="Filter by meeting subject")
    parser.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    parser.add_argument("--reindex", action="store_true", help="Rebuild the entire search index")

    args = parser.parse_args(argv)

    config = Config.load()
    index = RecordingIndex()

    try:
        if args.reindex:
            count = index.index_all(config.output_dir)
            print(f"Indexed {count} recordings.")
            return 0

        if not args.query and not args.speaker and not args.date_from and not args.date_to and not args.attendee and not args.subject:
            parser.error("At least one search criterion is required. Use --reindex to rebuild the index.")

        results = index.search(
            query=args.query,
            speaker=args.speaker,
            date_from=args.date_from,
            date_to=args.date_to,
            attendee=args.attendee,
            subject=args.subject,
            limit=args.limit,
        )

        if not results:
            print("No results found.")
            return 0

        for i, r in enumerate(results, 1):
            print(f"\n--- Result {i} ---")
            print(f"  Date:     {r.date}")
            if r.subject:
                print(f"  Subject:  {r.subject}")
            print(f"  App:      {r.app_name}")
            if r.speakers:
                print(f"  Speakers: {r.speakers}")
            if r.attendees:
                print(f"  Attendees: {r.attendees}")
            print(f"  Path:     {r.recording_dir}")
            if r.snippet:
                print(f"  Preview:  {r.snippet}")

        print(f"\n{len(results)} result(s) found.")
        return 0

    finally:
        index.close()


if __name__ == "__main__":
    sys.exit(main())
