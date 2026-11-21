import base64
import hashlib
import ipaddress
import json
import os
import time
from pathlib import Path
from urllib.request import urlopen, Request

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "dist"
TAG = b"UNBO-DIRECT"
UPSTREAM = "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/"
BASE = "https://github.com/BUSH-xanta/unbo-geodata/releases/latest/download/"

def varint(n):
    out = bytearray()
    while n > 127:
        out.append((n & 127) | 128)
        n >>= 7
    out.append(n)
    return bytes(out)

def bfield(n, b):
    return varint(n * 8 + 2) + varint(len(b)) + b

def ifield(n, v):
    return varint(n * 8) + varint(v)

def readvar(data, p):
    v = 0
    for shift in range(0, 70, 7):
        x = data[p]
        p += 1
        v |= (x & 127) << shift
        if x < 128:
            return v, p
    raise ValueError("Invalid varint")

def fields(data):
    p = 0
    while p < len(data):
        key, p = readvar(data, p)
        n, wire = key >> 3, key & 7
        if n == 0:
            raise ValueError("Invalid field")
        if wire == 0:
            v, p = readvar(data, p)
        elif wire == 2:
            size, p = readvar(data, p)
            if p + size > len(data):
                raise ValueError("Truncated protobuf")
            v = data[p:p+size]
            p += size
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            if p + size > len(data):
                raise ValueError("Truncated protobuf")
            v = data[p:p+size]
            p += size
        else:
            raise ValueError("Unsupported wire type")
        yield n, wire, v

def entries(data):
    out = {}
    for n, wire, v in fields(data):
        if n == 1 and wire == 2:
            name = next((x for k, w, x in fields(v) if k == 1 and w == 2), None)
            if not name or name.upper() in out:
                raise ValueError("Missing or duplicate category")
            out[name.upper()] = v
    if not out:
        raise ValueError("Empty database")
    return out

def download(name):
    def get(suffix):
        with urlopen(Request(UPSTREAM + suffix, headers={"User-Agent": "UnboVPN-GeoData"}), timeout=120) as response:
            return response.read()
    data = get(name)
    checksum = get(name + ".sha256sum").decode("ascii").split()[0].lower()
    if hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError("Upstream checksum mismatch: " + name)
    return data

def merge(data, items, required=()):
    original = entries(data)
    if TAG in original or not all(c in original for c in required):
        raise ValueError("Unexpected upstream categories")
    entry = bfield(1, TAG) + b"".join(bfield(2, x) for x in items)
    result = data + bfield(1, entry)
    verified = entries(result)
    if set(verified) != set(original) | {TAG}:
        raise ValueError("Category mismatch")
    if any(verified[k] != v for k, v in original.items()):
        raise ValueError("Upstream category changed")
    if [v for n, w, v in fields(verified[TAG]) if n == 2 and w == 2] != items:
        raise ValueError("Custom rules mismatch")
    return result

def main():
    cfg = json.loads((ROOT / "routing-source.json").read_text(encoding="utf-8"))
    domains = sorted(set(cfg["DirectSites"]))
    networks = sorted(set(cfg["DirectIp"]))
    assert len(domains) == len(cfg["DirectSites"]) and len(networks) == len(cfg["DirectIp"])
    assert domains and networks
    assert cfg["BlockSites"] == ["geosite:category-ads-all"]
    ds = []
    for rule in domains:
        if not rule.startswith("domain:"):
            raise ValueError(rule)
        name = rule[7:].encode("idna")
        if not name or b" " in name:
            raise ValueError(rule)
        ds.append(ifield(1, 2) + bfield(2, name))
    ips = []
    for rule in networks:
        net = ipaddress.ip_network(rule, strict=True)
        ips.append(bfield(1, net.network_address.packed) + ifield(2, net.prefixlen))
    OUT.mkdir(exist_ok=True)
    for name, items, required in [
        ("geosite.dat", ds, (b"CATEGORY-ADS-ALL",)),
        ("geoip.dat", ips, ()),
    ]:
        data = merge(download(name), items, required)
        (OUT / name).write_bytes(data)
        (OUT / (name + ".sha256sum")).write_text(hashlib.sha256(data).hexdigest() + "  " + name + "\n")
    cfg.update(Geositeurl=BASE+"geosite.dat", Geoipurl=BASE+"geoip.dat",
               DirectSites=["geosite:unbo-direct"], DirectIp=["geoip:unbo-direct"],
               LastUpdated=str(int(time.time())))
    raw = json.dumps(cfg, ensure_ascii=False, separators=(",", ":")).encode()
    link = "happ://routing/onadd/" + base64.b64encode(raw).decode("ascii")
    assert json.loads(base64.b64decode(link[len("happ://routing/onadd/"):], validate=True)) == cfg
    (OUT / "routing.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    (OUT / "routing_header.txt").write_text(link, encoding="utf-8")
    print(f"Verified {len(domains)} domain rules, {len(networks)} IP rules; header {len(link)} bytes")

if __name__ == "__main__":
    main()
