#!/usr/bin/env python3
"""Fork-only: render victron_mqtt's topics as an HTML page structured like the Venus OS D-Bus wiki page.

Usage: build_dbus_page.py victron_mqtt.json docs/dbus.html [--ref REF] [--sha SHA]

The wiki (https://github.com/victronenergy/venus/wiki/dbus) lists D-Bus paths per service. This page uses the
same sections, in the same order, and lists the paths victron_mqtt supports for each service. The MQTT topic
for a path is N/{installation_id}/{service}/{device_id}{path}.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import textwrap
from collections import defaultdict
from pathlib import Path

WIKI_URL = "https://github.com/victronenergy/venus/wiki/dbus"

# Sections of the wiki page, in wiki order: (title, index label, services).
SECTIONS: list[tuple[str, str, list[str]]] = [
    ("Generic paths", "generic", ["{device_type}"]),
    ("System", "system", ["system"]),
    ("ESS (formerly called Hub-4)", "ess", ["hub4"]),
    ("Settings", "settings", ["settings"]),
    ("VE.Bus systems (Multis, Quattros, Inverters)", "vebus", ["vebus"]),
    ("Multi RS and other future new inverter/chargers", "multi", ["multi"]),
    ("Acsystem", "acsystem", ["acsystem"]),
    ("Inverter", "inverter", ["inverter"]),
    ("Battery", "battery", ["battery"]),
    ("Solar chargers", "solar chargers", ["solarcharger"]),
    ("DCDC converters", "DCDC converters", ["dcdc"]),
    ("PV Inverters", "pvinverters", ["pvinverter"]),
    ("AC chargers", "ac chargers", ["charger"]),
    (
        "Grid (and genset and acload and heatpump) meter",
        "grid (and genset and acload and heatpump) meter",
        ["grid", "acload", "heatpump"],
    ),
    ("Temperatures", "temperatures", ["temperature"]),
    ("Meteo", "meteo", ["meteo"]),
    ("Tank levels", "tank levels", ["tank"]),
    ("Pulse meters and digital inputs", "pulse meters and digital inputs", ["pulsemeter", "digitalinput"]),
    ("Generator data", "(diesel) generator", ["genset", "dcgenset"]),
    ("Generator start/stop", "generator start/stop", ["generator"]),
    ("vecan-xxx", "vecan-xxx", ["vecan"]),
    ("alternator-and-dcsource", "alternator, dcsource", ["alternator", "dcsource"]),
    ("fuelcell", "fuelcell", ["fuelcell"]),
    ("motordrive", "motordrive", ["motordrive"]),
    ("dcsystem", "dcsystem", ["dcsystem"]),
    ("dcload", "dcload", ["dcload"]),
    ("evcharger", "evcharger", ["evcharger"]),
    ("switch", "switch", ["switch"]),
    ("rvc-xxx", "rvc", ["rvc"]),
    ("platform", "platform", ["platform"]),
    ("gps", "gps", ["gps"]),
    ("ble", "ble", ["ble"]),
    ("EV", "ev", ["ev"]),
]

WRITABLE_KINDS = {"SWITCH", "SELECT", "NUMBER", "TIME", "BUTTON", "DYNAMIC"}
MAX_INLINE_ENUM_VALUES = 12
TEXT_WIDTH = 72


def slug(title: str) -> str:
    """GitHub-style heading anchor, so #fragments match the wiki's."""
    return re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")


def split_topic(topic: str) -> tuple[str, str, str] | None:
    """N/{installation_id}/battery/{device_id}/Dc/0/Voltage -> ("N", "battery", "/Dc/0/Voltage")."""
    parts = topic.split("/")
    if parts[0] not in ("N", "W") or len(parts) < 3:
        return None
    if len(parts) < 5:  # root topics such as N/{installation_id}/heartbeat
        return parts[0], parts[2], ""
    return parts[0], parts[2], "/" + "/".join(parts[4:])


def enum_text(enum: dict | None) -> str:
    if not enum:
        return ""
    values = enum["EnumValues"]
    if len(values) > MAX_INLINE_ENUM_VALUES:
        return f"enum {enum['name']} ({len(values)} values)"
    return "; ".join(f"{v['value']}={v['name']}" for v in values)


def describe(t: dict, prefix: str, enums: dict[str, dict]) -> str:
    kind = t["message_type"].split(".")[-1]
    parts = []
    if t.get("unit_of_measurement"):
        parts.append(f"{t['unit_of_measurement']} -")
    parts.append(t.get("description") or t.get("name") or "")
    if values := enum_text(enums.get(t.get("enum") or "")):
        parts.append(f"({values})")
    flags = ["write-only"] if prefix == "W" else (["writable"] if kind in WRITABLE_KINDS else [])
    if kind == "ATTRIBUTE":
        flags.append("device attribute")
    if t.get("experimental"):
        flags.append("experimental")
    if flags:
        parts.append(f"[{', '.join(flags)}]")
    return " ".join(p for p in parts if p)


def code_block(service: str, rows: list[tuple[str, str]]) -> str:
    """One wiki-style block: service name, then `/Path   <- text` lines with wrapped continuations."""
    width = min(max((len(p) for p, _ in rows), default=0), 44) + 2
    lines = [f"com.victronenergy.{service}" if not service.startswith("{") else "(all services)", ""]
    for path, text in rows:
        wrapped = textwrap.wrap(text, TEXT_WIDTH) or [""]
        pad = " " * max(width - len(path), 1)
        lines.append(f"{html.escape(path)}{pad}&lt;- {html.escape(wrapped[0])}")
        lines += [" " * (max(width, len(path) + 1) + 3) + html.escape(w) for w in wrapped[1:]]
    return "<pre>" + "\n".join(lines) + "</pre>"


def attribute_topics() -> list[dict]:
    """Device attributes (CustomName, Serial, ...), which victron_mqtt.json leaves out; read from the library."""
    try:
        from victron_mqtt._victron_topics import topics  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        from victron_mqtt.constants import MetricKind  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
    except ImportError:
        return []
    return [
        {"topic": d.topic, "message_type": "MetricKind.ATTRIBUTE", "name": d.name, "description": d.description}
        for d in topics
        if d.message_type == MetricKind.ATTRIBUTE
    ]


def build(data: dict, ref: str | None, sha: str | None) -> str:
    enums = {e["name"]: e for e in data.get("enums", [])}
    by_service: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for t in sorted(data["topics"] + attribute_topics(), key=lambda t: t["topic"]):
        if t.get("is_formula") or not (split := split_topic(t["topic"])):
            continue
        prefix, service, path = split
        by_service[service].append((path or "(root topic)", describe(t, prefix, enums)))

    known = {svc for _, _, services in SECTIONS for svc in services}
    sections = list(SECTIONS)
    if other := sorted(set(by_service) - known):
        sections.append(("Other services", "other services (not on the wiki)", other))

    total = sum(len(v) for v in by_service.values())
    built_from = f"<code>{html.escape(ref)}</code>" if ref else "this repository"
    if sha:
        built_from += f" at <code>{html.escape(sha[:10])}</code>"
    body = [
        '<h1 id="list-of-available-services-and-their-paths">List of available services and their paths</h1>',
        f"<p>This page lists the {total} D-Bus paths that victron_mqtt supports, grouped per service in the same "
        f'structure as the <a href="{WIKI_URL}">Venus OS D-Bus wiki page</a>. The MQTT topic for a path is '
        "<code>N/{installation_id}/{service}/{device_id}{path}</code>; placeholders such as <code>{phase}</code> "
        "and <code>{output}</code> stand for instance or phase segments. Sections the wiki has but victron_mqtt "
        "does not support yet are kept so gaps are visible.</p>",
        f'<p class="meta">Generated from {built_from} on {dt.datetime.now(dt.UTC).date().isoformat()}.</p>',
        "<p>Index:</p>",
        "<ul>",
    ]
    for title, label, services in sections:
        count = sum(len(by_service.get(s, [])) for s in services)
        body.append(f'<li><a href="#{slug(title)}">{html.escape(label)}</a> <span class="count">({count})</span></li>')
    body.append("</ul>")

    for title, _, services in sections:
        body.append(f'<h2 id="{slug(title)}">{html.escape(title)}</h2>')
        present = [s for s in services if by_service.get(s)]
        if not present:
            names = ", ".join(
                f"<code>com.victronenergy.{html.escape(s)}</code>" for s in services if not s.startswith("{")
            )
            body.append(f'<p class="empty">Not supported by victron_mqtt yet ({names or "no paths"}).</p>')
            continue
        body.extend(code_block(service, by_service[service]) for service in present)
        if missing := [s for s in services if s not in present]:
            names = ", ".join(f"<code>com.victronenergy.{html.escape(s)}</code>" for s in missing)
            body.append(f'<p class="empty">Not supported by victron_mqtt yet: {names}.</p>')

    return PAGE.replace("{{BODY}}", "\n".join(body))


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>victron_mqtt D-Bus paths</title>
<style>
  :root { --fg: #1f2328; --muted: #59636e; --bg: #ffffff; --code-bg: #f6f8fa; --border: #d1d9e0; --link: #0969da; }
  @media (prefers-color-scheme: dark) {
    :root { --fg: #f0f6fc; --muted: #9198a1; --bg: #0d1117; --code-bg: #151b23; --border: #3d444d; --link: #4493f8; }
  }
  body { margin: 0; background: var(--bg); color: var(--fg);
         font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif; }
  main { max-width: 1012px; margin: 0 auto; padding: 32px 16px 64px; }
  a { color: var(--link); text-decoration: none; } a:hover { text-decoration: underline; }
  h1, h2 { font-weight: 600; line-height: 1.25; padding-bottom: .3em; border-bottom: 1px solid var(--border); }
  h1 { font-size: 2em; margin: 0 0 16px; } h2 { font-size: 1.5em; margin: 24px 0 16px; }
  code, pre { font: 13.6px/1.45 ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; }
  code { background: var(--code-bg); padding: .2em .4em; border-radius: 6px; }
  pre { background: var(--code-bg); padding: 16px; border-radius: 6px; overflow-x: auto; margin: 0 0 16px; }
  .meta, .count, .empty { color: var(--muted); }
  ul { padding-left: 2em; }
</style>
</head>
<body>
<main>
{{BODY}}
</main>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("json_file", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--ref", help="ref the docs were built from, shown on the page")
    parser.add_argument("--sha", help="commit the docs were built from, shown on the page")
    args = parser.parse_args()
    args.output.write_text(build(json.loads(args.json_file.read_text()), args.ref, args.sha))


if __name__ == "__main__":
    main()
