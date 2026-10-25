"""HTTP GET of JSON with a small on-disk cache, so the free endpoints are
asked politely and the page stays fast on a reload."""

import gzip
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class FetchError(Exception):
    pass


class Cache(object):
    def __init__(self, folder):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, key):
        return os.path.join(self.folder, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")

    def get(self, key, ttl):
        path = self._path(key)
        try:
            if time.time() - os.path.getmtime(path) > ttl:
                return None
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def put(self, key, value):
        path = self._path(key)
        tmp = path + ".%d.tmp" % threading.get_ident()
        with self._lock:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(value, fh)
            os.replace(tmp, path)


_pace_lock = threading.Lock()
_last_call = {}


def _pace(host, gap):
    """At most one request per `gap` seconds to each host."""
    with _pace_lock:
        wait = _last_call.get(host, 0) + gap - time.time()
        _last_call[host] = time.time() + max(wait, 0)
    if wait > 0:
        time.sleep(wait)


def get_json(url, cache, ttl, headers=None, gap=0.2):
    hit = cache.get(url, ttl) if cache else None
    if hit is not None:
        return hit
    _pace(urllib.parse.urlsplit(url).netloc, gap)
    req = urllib.request.Request(url, headers=dict({"Accept-Encoding": "gzip",
                                                    "Accept": "application/json"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
    except urllib.error.HTTPError as exc:
        raise FetchError("HTTP %s from %s" % (exc.code, urllib.parse.urlsplit(url).netloc))
    except (urllib.error.URLError, OSError) as exc:
        raise FetchError("could not reach %s: %s" % (urllib.parse.urlsplit(url).netloc,
                                                     getattr(exc, "reason", exc)))
    try:
        doc = json.loads(data.decode("utf-8"))
    except ValueError:
        raise FetchError("not JSON from %s" % urllib.parse.urlsplit(url).netloc)
    if cache:
        cache.put(url, doc)
    return doc

