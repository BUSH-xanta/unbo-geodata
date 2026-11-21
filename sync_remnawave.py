"""Synchronize the published Happ routing header without replacing unrelated settings."""
import base64
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

PREFIX = "happ://routing/onadd/"
BASE = "https://github.com/BUSH-xanta/unbo-geodata/releases/latest/download/"
NAME = "UnboVPN Smart Routing"

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def parse_link(link):
    if not isinstance(link, str) or not link.startswith(PREFIX):
        raise ValueError("Unexpected routing header format")
    data = json.loads(base64.b64decode(link[len(PREFIX):], validate=True))
    if data.get("Name") != NAME or data.get("Geoipurl") != BASE + "geoip.dat" or data.get("Geositeurl") != BASE + "geosite.dat":
        raise ValueError("Refusing to overwrite a different routing profile")
    return data

def api(method, endpoint, token, payload=None):
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json",
               "Content-Type": "application/json"}
    body = json.dumps(payload).encode() if payload is not None else None
    req = Request(endpoint, data=body, headers=headers, method=method)
    try:
        with build_opener(NoRedirect()).open(req, timeout=30) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"Remnawave {method} returned HTTP {exc.code}") from None
    except URLError:
        raise RuntimeError(f"Remnawave {method} failed: network or TLS error") from None
    settings = result.get("response")
    if not isinstance(settings, dict):
        raise ValueError("Unexpected API response")
    return settings

def header_map(settings):
    headers = settings.get("customResponseHeaders")
    if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise ValueError("Unexpected customResponseHeaders format")
    return headers

def main():
    token = os.environ.get("REMNAWAVE_API_TOKEN", "").strip()
    url = os.environ.get("REMNAWAVE_URL", "").strip().rstrip("/")
    if not token and not url:
        print("Remnawave synchronization not configured; publication unaffected.")
        return
    if not token or not url:
        raise ValueError("Both REMNAWAVE_URL and REMNAWAVE_API_TOKEN are required")
    u = urlsplit(url)
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError("REMNAWAVE_URL must be a trusted HTTPS panel URL")
    endpoint = url + ("/subscription-settings" if url.endswith("/api") else "/api/subscription-settings")
    link = Path("dist/routing_header.txt").read_text(encoding="utf-8").strip()
    new = parse_link(link)
    settings = api("GET", endpoint, token)
    headers = header_map(settings)
    keys = [key for key in headers if key.lower() == "routing"]
    if len(keys) != 1 or not settings.get("uuid"):
        raise ValueError("Expected one existing routing header and a settings UUID")
    key = keys[0]
    old = parse_link(headers[key])
    if int(new["LastUpdated"]) < int(old["LastUpdated"]):
        raise ValueError("Refusing to downgrade routing timestamp")
    if headers[key] == link:
        print("Routing header already current.")
        return
    updated = dict(headers)
    updated[key] = link
    # Check for concurrent edits before sending the complete header map.
    fresh = api("GET", endpoint, token)
    if fresh.get("uuid") != settings["uuid"] or header_map(fresh) != headers:
        raise ValueError("Headers changed concurrently; rerun synchronization")
    api("PATCH", endpoint, token, {"uuid": settings["uuid"], "customResponseHeaders": updated})
    checked = api("GET", endpoint, token)
    if header_map(checked) != updated:
        raise ValueError("Post-update verification failed; inspect panel headers")
    print("Remnawave routing header updated and verified. LastUpdated=" + str(new["LastUpdated"]))

if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, KeyError, TypeError, OSError) as exc:
        # Do not log headers, credentials, response bodies or tracebacks.
        message = str(exc) if isinstance(exc, RuntimeError) else "Invalid configuration or verification failed; inspect panel settings and workflow secrets."
        print("ERROR: " + message, file=sys.stderr)
        sys.exit(1)
