#!/usr/bin/env python3
"""Probe a live forum URL using the same detector/hopper as the crawler.

Usage:
    python forum_probe.py https://example.invalid/forum/page

The probe reports software, confidence, evidence, parsed items, and the
classified outbound hosts. It does not write to JDownloader.
"""
import sys
from crawler import fetch, links
from crawler.forum_parser import parse


def main(url: str) -> int:
    status, body, headers = fetch.get(url, conditional=False)
    print(f"HTTP: {status}")
    print(f"Bytes: {len(body)}")
    items, detection = parse(body, url)
    if detection:
        print(f"Software: {detection.software}")
        print(f"Confidence: {detection.confidence:.2f}")
        print("Evidence: " + ", ".join(detection.evidence))
    else:
        print("Software: UNKNOWN")
        return 2

    all_links = [l for item in items for l in item.links]
    unique = {}
    for link in all_links:
        unique[link["url"].rstrip("/").lower()] = link

    print(f"Items: {len(items)}")
    print(f"Links: {len(unique)}")
    for link in unique.values():
        print(f"[{link['label']}] {link['url']}")

    rg = [l for l in unique.values() if l["host"] == "rapidgator.net"]
    print(f"Rapidgator: {len(rg)}")
    return 0 if rg else 3


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} URL", file=sys.stderr)
        raise SystemExit(64)
    raise SystemExit(main(sys.argv[1]))
