"""Topic trend analysis across meetings.

Tracks which keywords and topics dominate meetings over time,
helping identify shifts in focus, emerging concerns, and recurring themes.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Reuse stop words from auto_tag
from meeting_recorder.storage.auto_tag import _STOP_WORDS, _TOPIC_PATTERNS


@dataclass
class WeekTopics:
    """Topics for a single week."""
    week_start: str  # ISO date (Monday)
    recording_count: int
    top_keywords: list[tuple[str, int]]  # (word, count) sorted desc
    topic_scores: dict[str, float]  # topic_name -> relevance score
    total_words: int


@dataclass
class TopicTrend:
    """A topic/keyword tracked across weeks."""
    name: str
    weeks: list[tuple[str, int]]  # (week_start, count_or_score)
    total: int
    trend: str  # "rising", "falling", "stable", "new", "gone"


@dataclass
class TrendReport:
    """Full topic trend analysis."""
    weeks: list[WeekTopics]
    trends: list[TopicTrend]
    emerging: list[str]  # Topics that appeared recently
    declining: list[str]  # Topics that disappeared recently


def analyze_topic_trends(
    recordings_dir: Path,
    weeks: int = 8,
    top_n: int = 15,
) -> TrendReport:
    """Analyze topic trends across recent weeks.

    Args:
        recordings_dir: Base recordings directory.
        weeks: Number of weeks to analyze.
        top_n: Number of top keywords per week.

    Returns:
        TrendReport with weekly breakdowns and trend analysis.
    """
    if not recordings_dir.exists():
        return TrendReport(weeks=[], trends=[], emerging=[], declining=[])

    # Collect transcript text per week
    today = date.today()
    week_texts: dict[str, list[str]] = defaultdict(list)
    week_counts: dict[str, int] = defaultdict(int)

    for rec_dir in recordings_dir.iterdir():
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue
        date_str = rec_dir.name[:10]
        try:
            rec_date = date.fromisoformat(date_str)
        except ValueError:
            continue

        # Find which week this belongs to
        monday = rec_date - timedelta(days=rec_date.weekday())
        week_key = monday.isoformat()

        # Only include recent weeks
        weeks_ago = (today - monday).days / 7
        if weeks_ago > weeks:
            continue

        # Read transcript text
        txt_path = rec_dir / "transcript.txt"
        if txt_path.exists():
            try:
                text = txt_path.read_text(encoding="utf-8")
                if text.strip():
                    week_texts[week_key].append(text)
                    week_counts[week_key] += 1
            except Exception:
                pass

    if not week_texts:
        return TrendReport(weeks=[], trends=[], emerging=[], declining=[])

    # Analyze each week
    week_results: list[WeekTopics] = []
    all_week_keywords: dict[str, dict[str, int]] = {}  # week -> {word: count}

    for week_offset in range(weeks):
        monday = today - timedelta(days=today.weekday()) - timedelta(weeks=week_offset)
        week_key = monday.isoformat()

        texts = week_texts.get(week_key, [])
        if not texts:
            week_results.append(WeekTopics(
                week_start=week_key,
                recording_count=0,
                top_keywords=[],
                topic_scores={},
                total_words=0,
            ))
            all_week_keywords[week_key] = {}
            continue

        combined = " ".join(texts)
        keywords = _extract_keywords(combined, top_n=top_n)
        topics = _score_topics(combined)
        word_count = len(combined.split())

        week_results.append(WeekTopics(
            week_start=week_key,
            recording_count=week_counts.get(week_key, 0),
            top_keywords=keywords,
            topic_scores=topics,
            total_words=word_count,
        ))
        all_week_keywords[week_key] = dict(keywords)

    # Build trends for keywords that appear in multiple weeks
    all_keywords: set[str] = set()
    for kw_dict in all_week_keywords.values():
        all_keywords.update(kw_dict.keys())

    trends: list[TopicTrend] = []
    for keyword in all_keywords:
        week_data = []
        for wr in week_results:
            count = all_week_keywords.get(wr.week_start, {}).get(keyword, 0)
            week_data.append((wr.week_start, count))

        total = sum(c for _, c in week_data)
        if total < 3:  # Skip very rare keywords
            continue

        trend_dir = _classify_trend(week_data)
        trends.append(TopicTrend(
            name=keyword,
            weeks=week_data,
            total=total,
            trend=trend_dir,
        ))

    # Sort by total frequency
    trends.sort(key=lambda t: -t.total)
    trends = trends[:30]  # Cap at 30 trends

    # Identify emerging and declining topics
    recent_weeks = [wr.week_start for wr in week_results[:2]]
    older_weeks = [wr.week_start for wr in week_results[2:4]]

    recent_kws: set[str] = set()
    older_kws: set[str] = set()
    for wk in recent_weeks:
        recent_kws.update(all_week_keywords.get(wk, {}).keys())
    for wk in older_weeks:
        older_kws.update(all_week_keywords.get(wk, {}).keys())

    emerging = sorted(recent_kws - older_kws)[:10]
    declining = sorted(older_kws - recent_kws)[:10]

    return TrendReport(
        weeks=week_results,
        trends=trends,
        emerging=emerging,
        declining=declining,
    )


def format_topic_trends(report: TrendReport) -> str:
    """Format topic trend analysis as readable text."""
    if not report.weeks or not any(w.recording_count > 0 for w in report.weeks):
        return "No meeting data available for topic trend analysis."

    lines: list[str] = []
    lines.append("TOPIC TRENDS")
    lines.append("=" * 50)
    lines.append("")

    # Weekly overview
    for week in report.weeks:
        if week.recording_count == 0:
            continue
        lines.append(f"Week of {week.week_start}  ({week.recording_count} recording{'s' if week.recording_count != 1 else ''})")
        lines.append("-" * 40)
        if week.top_keywords:
            kw_strs = [f"{w} ({c})" for w, c in week.top_keywords[:8]]
            lines.append(f"  Keywords: {', '.join(kw_strs)}")
        if week.topic_scores:
            top_topics = sorted(week.topic_scores.items(), key=lambda x: -x[1])[:5]
            topic_strs = [name for name, _ in top_topics]
            lines.append(f"  Topics:   {', '.join(topic_strs)}")
        lines.append("")

    # Trends
    if report.trends:
        lines.append("KEYWORD TRENDS")
        lines.append("-" * 40)
        for trend in report.trends[:15]:
            arrow = {
                "rising": "\u2197",
                "falling": "\u2198",
                "stable": "\u2192",
                "new": "\u2728",
                "gone": "\u274c",
            }.get(trend.trend, "")
            # Build sparkline
            values = [c for _, c in trend.weeks]
            sparkline = _sparkline(values)
            lines.append(f"  {arrow} {trend.name:<20} {sparkline}  total: {trend.total}")
        lines.append("")

    # Emerging / declining
    if report.emerging:
        lines.append(f"  Emerging:  {', '.join(report.emerging[:8])}")
    if report.declining:
        lines.append(f"  Declining: {', '.join(report.declining[:8])}")
    if report.emerging or report.declining:
        lines.append("")

    return "\n".join(lines)


def _extract_keywords(text: str, top_n: int = 15) -> list[tuple[str, int]]:
    """Extract significant keywords from text."""
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    filtered = [w for w in words if w not in _STOP_WORDS and len(w) >= 4]
    counts = Counter(filtered)
    return counts.most_common(top_n)


def _score_topics(text: str) -> dict[str, float]:
    """Score topic categories based on keyword presence."""
    text_lower = text.lower()
    scores: dict[str, float] = {}
    for topic, keywords in _TOPIC_PATTERNS.items():
        score = 0.0
        for kw in keywords:
            count = text_lower.count(kw)
            if count > 0:
                score += count * (1 + len(kw) / 10)
        if score >= 3.0:
            scores[topic] = round(score, 1)
    return scores


def _classify_trend(week_data: list[tuple[str, int]]) -> str:
    """Classify a keyword's trend direction."""
    values = [c for _, c in week_data]
    if not values:
        return "stable"

    # Recent = first 2 weeks (most recent), older = rest
    recent = values[:2]
    older = values[2:]

    recent_avg = sum(recent) / max(len(recent), 1)
    older_avg = sum(older) / max(len(older), 1)

    if older_avg == 0 and recent_avg > 0:
        return "new"
    if recent_avg == 0 and older_avg > 0:
        return "gone"
    if older_avg > 0:
        ratio = recent_avg / older_avg
        if ratio >= 1.5:
            return "rising"
        if ratio <= 0.5:
            return "falling"
    return "stable"


def _sparkline(values: list[int]) -> str:
    """Create a simple text sparkline from values."""
    if not values:
        return ""
    bars = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
    max_val = max(values) if max(values) > 0 else 1
    return "".join(bars[min(int(v / max_val * 7) + (1 if v > 0 else 0), 8)] for v in values)
