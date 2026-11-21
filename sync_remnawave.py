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

class SafeError(RuntimeError):
    """Only fixed, non-sensitive diagnostic messages may be logged."""

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def parse_link(link, label):
    if not isinstance(link, str) or not link.strip().startswith(PREFIX):
        raise SafeError(f"{label}: expected happ://routing/onadd/ prefix")
    encoded = link.strip()[len(PREFIX):]
    try:
        data = json.loads(base64.b64decode(encoded, validate=True))
    except (ValueError, TypeError):
        raise SafeError(f"{label}: invalid Base64 or JSON") from None
    if not isinstance(data, dict) or data.get("Name") != NAME:
        raise SafeError(f"{label}: unexpected profile name")
    if data.get("Geoipurl") != BASE + "geoip.dat" or data.get("Geositeurl") != BASE + "geosite.dat":
        raise SafeError(f"{label}: GeoData URLs do not match UnboVPN releases")
    return data

def timestamp(profile, label, allow_empty=False):
    value = profile.get("LastUpdated")
    if allow_empty and value in (None, ""):
        return 0
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise SafeError(f"{label}: LastUpdated is not an integer") from None
    if result < 0:
        raise SafeError(f"{label}: LastUpdated is negative")
    return result

def api(method, endpoint, token, payload=None):
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json",
               "Content-Type": "application/json"}
    body = json.dumps(payload).encode() if payload is not None else None
    req = Request(endpoint, data=body, headers=headers, method=method)
    try:
        with build_opener(NoRedirect()).open(req, timeout=30) as response:
            try:
                result = json.load(response)
            except (ValueError, TypeError):
                raise SafeError(f"Remnawave {method}: response is not JSON") from None
            print(f"Remnawave {method}: HTTP {response.status}, JSON received.")
    except HTTPError as exc:
        raise RuntimeError(f"Remnawave {method} returned HTTP {exc.code}") from None
    except URLError:
        raise RuntimeError(f"Remnawave {method} failed: network or TLS error") from None
    settings = result.get("response") if isinstance(result, dict) else None
    if not isinstance(settings, dict):
        raise SafeError(f"Remnawave {method}: expected response object")
    return settings

def header_map(settings):
    headers = settings.get("customResponseHeaders")
    if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise SafeError("API: customResponseHeaders must be an object with string values")
    return headers

def main():
    token = os.environ.get("REMNAWAVE_API_TOKEN", "").strip()
    url = os.environ.get("REMNAWAVE_URL", "").strip().rstrip("/")
    if not token and not url:
        print("Remnawave synchronization not configured; publication unaffected.")
        return
    if not token or not url:
        raise SafeError("Both REMNAWAVE_URL and REMNAWAVE_API_TOKEN are required")
    u = urlsplit(url)
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise SafeError("REMNAWAVE_URL must be a trusted HTTPS panel URL")
    endpoint = url + ("/subscription-settings" if url.endswith("/api") else "/api/subscription-settings")
    link = Path("dist/routing_header.txt").read_text(encoding="utf-8").strip()
    new = parse_link(link, "Generated header")
    new_ts = timestamp(new, "Generated header")
    print("Generated routing profile validated.")
    settings = api("GET", endpoint, token)
    headers = header_map(settings)
    keys = [key for key in headers if key.lower() == "routing"]
    if not settings.get("uuid"):
        raise SafeError("API: missing subscription settings UUID")
    if len(keys) != 1:
        raise SafeError(f"API: expected one routing header; found {len(keys)}")
    print(f"Found routing header; {len(headers)-1} other headers will be preserved.")
    key = keys[0]
    old = parse_link(headers[key], "Existing panel header")
    if new_ts < timestamp(old, "Existing panel header", allow_empty=True):
        raise SafeError("Refusing to downgrade routing timestamp")
    print("Existing routing profile validated.")
    if headers[key] == link:
        print("Routing header already current.")
        return
    updated = dict(headers)
    updated[key] = link
    # Check for concurrent edits before sending the complete header map.
    fresh = api("GET", endpoint, token)
    if fresh.get("uuid") != settings["uuid"] or header_map(fresh) != headers:
        raise SafeError("Headers changed concurrently; rerun synchronization")
    api("PATCH", endpoint, token, {"uuid": settings["uuid"], "customResponseHeaders": updated})
    checked = api("GET", endpoint, token)
    if header_map(checked) != updated:
        raise SafeError("Post-update verification failed; inspect panel headers")
    print("Remnawave routing header updated and verified. LastUpdated=" + str(new["LastUpdated"]))

if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, KeyError, TypeError, OSError) as exc:
        # Do not log headers, credentials, response bodies or tracebacks.
        message = str(exc) if isinstance(exc, RuntimeError) else f"Unhandled {type(exc).__name__}; see preceding diagnostic stage."
        print("ERROR: " + message, file=sys.stderr)
        sys.exit(1)
