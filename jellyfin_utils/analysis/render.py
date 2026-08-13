"""Output renderers for the reclaim cleanup queue."""

from __future__ import annotations

import csv
import io

import orjson

PRIORITY_LEGEND = "* = requested in Jellyseerr and already watched by whoever requested it"
ROLLUP_LEGEND = "Series sizes total their episodes, which are listed separately and counted once."
REASON_LABELS = {
    "widely_watched_and_stale": "Widely watched, and stale",
    "widely_watched": "Widely watched",
    "stale": "Stale",
}
REASON_ORDER = ("widely_watched_and_stale", "widely_watched", "stale")
MIN_TITLE_WIDTH = 32
MAX_TITLE_WIDTH = 52
RULE_WIDTH = 80
DETAIL_INDENT = " " * 10


def _total_gib(entries: list[dict]) -> float:
    """Sum sizes, skipping series whose size is the total of episodes listed separately."""
    return sum(float(entry["size_gib"]) for entry in entries if not entry["size_is_rollup"])


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else f"{text[: width - 1]}…"


def _title_width(entries: list[dict]) -> int:
    longest = max((len(entry["item"]) for entry in entries), default=MIN_TITLE_WIDTH)
    return min(max(longest, MIN_TITLE_WIDTH), MAX_TITLE_WIDTH)


def _format_entry_line(entry: dict, title_width: int) -> str:
    marker = "*" if entry["requester_watched"] else " "
    title = _truncate(entry["item"], title_width)
    watchers = _plural(entry["watchers"], "watcher")
    size = f"{float(entry['size_gib']):.2f} GiB"
    return f"{marker} {entry['type']:<7} {title:<{title_width}}  {watchers:>11}  {size:>10}"


def _format_entry_detail(entry: dict) -> list[str]:
    lines = []
    if entry["watched_by_requester"]:
        requesters = ", ".join(entry["watched_by_requester"])
        lines.append(f"{DETAIL_INDENT}requested by {requesters}, who has since watched it")
    elif entry["requested_by"]:
        lines.append(f"{DETAIL_INDENT}requested by {', '.join(entry['requested_by'])}")
    lines.append(f"{DETAIL_INDENT}{entry['path'] or '(no path on disk)'}  [{entry['id']}]")
    return lines


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _group_rule(reason: str, entries: list[dict]) -> list[str]:
    label = REASON_LABELS.get(reason, reason)
    heading = f"── {label} · {_plural(len(entries), 'item')} · {_total_gib(entries):.2f} GiB "
    return ["", heading + "─" * max(RULE_WIDTH - len(heading), 0)]


def _header_lines(entries: list[dict], *, jellyseerr_enabled: bool) -> list[str]:
    total = _total_gib(entries)
    lines = [f"Reclaim review · {_plural(len(entries), 'candidate')} · {total:.2f} GiB to reclaim"]
    if any(entry["size_is_rollup"] for entry in entries):
        lines.append(ROLLUP_LEGEND)
    if jellyseerr_enabled:
        lines.append(PRIORITY_LEGEND)
    return lines


def render_text(entries: list[dict], *, jellyseerr_enabled: bool, quiet: bool) -> str:
    """Render the cleanup queue as a grouped, human-readable report."""
    title_width = _title_width(entries)

    if quiet:
        return "\n".join(_format_entry_line(entry, title_width) for entry in entries)

    lines = _header_lines(entries, jellyseerr_enabled=jellyseerr_enabled)
    if not entries:
        lines.append("\nNothing to review.")
        return "\n".join(lines)

    for reason in REASON_ORDER:
        group = [entry for entry in entries if entry["reason"] == reason]
        if not group:
            continue
        lines.extend(_group_rule(reason, group))
        for entry in group:
            lines.append(_format_entry_line(entry, title_width))
            lines.extend(_format_entry_detail(entry))

    return "\n".join(lines)


def render_json(entries: list[dict], *, jellyseerr_enabled: bool) -> str:
    """Render the cleanup queue as indented JSON."""
    payload = {
        "candidates": entries,
        "count": len(entries),
        "estimated_reclaimable_gib": round(_total_gib(entries), 2),
        "jellyseerr_enabled": jellyseerr_enabled,
    }
    return orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode()


def render_markdown(entries: list[dict]) -> str:
    """Render the cleanup queue as a compact Markdown table."""
    lines = [
        "| Priority | Reason | Type | Series | Title | Watchers | Requested by | Requester watched | Size |",
        "| --- | --- | --- | --- | --- | ---: | --- | --- | ---: |",
    ]
    lines.extend(
        "| "
        + " | ".join(
            (
                "Priority" if entry["requester_watched"] else "Standard",
                entry["reason"],
                entry["type"],
                entry["series"] or "—",
                entry["item"],
                str(entry["watchers"]),
                ", ".join(entry["requested_by"]) or "—",
                ", ".join(entry["watched_by_requester"]) or "—",
                f"{float(entry['size_gib']):.2f} GiB",
            )
        )
        + " |"
        for entry in entries
    )
    return "\n".join(lines)


def render_csv(entries: list[dict]) -> str:
    """Render the cleanup queue as CSV with headers."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Reason",
            "Type",
            "Series",
            "Name",
            "Watchers",
            "Requester Watched",
            "Requested By",
            "File Size (GiB)",
            "ID",
            "Path",
        ]
    )
    for entry in entries:
        writer.writerow(
            [
                entry["reason"],
                entry["type"],
                entry["series"] or "",
                entry["item"],
                entry["watchers"],
                ", ".join(entry["watched_by_requester"]),
                ", ".join(entry["requested_by"]),
                f"{float(entry['size_gib']):.2f}",
                entry["id"],
                entry["path"],
            ]
        )
    return buf.getvalue()
