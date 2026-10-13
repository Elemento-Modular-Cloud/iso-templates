"""Keeps iso.json pointed at the latest ISO of every major release line.

Every entry in iso.json is one major release line of a distro variant
(e.g. AlmaLinux 9 minimal). On each run, for every variant:

  * existing lines are moved to the newest minor release on the mirror
    (AlmaLinux 9.7 -> 9.8), with os_version and libosinfo_id kept in sync;
  * a major release newer than the newest one in iso.json gets a new entry,
    cloned from the newest existing one (AlmaLinux 10 appears -> new entry);
  * a line whose ISO is gone from the mirror is dropped, but only when a
    newer line exists, so a variant is never removed entirely;
  * several entries for the same line are collapsed into one.

Rolling distros (Arch, Gentoo, Tumbleweed, ...) have a single line whose URL
is refreshed. Windows entries and entries without an iso_url are untouched.
"""

import copy
import json
import os
import re
from datetime import date
from urllib.parse import unquote, urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TIMEOUT = 15
OSINFO_RAW_URL = "https://gitlab.com/libosinfo/osinfo-db/-/raw/main/data/os/{vendor}/{name}.xml.in"
POP_API_URL = "https://api.pop-os.org/builds/{version}/{channel}"

# The leading version in os_version, e.g. "9.8" in "9.8.latest-boot-x86_64"
VERSION_TOKEN = re.compile(r"\d+(?:\.\d+)*")

session = requests.Session()
session.headers["User-Agent"] = "elemento-iso-templates-scraper (+https://github.com/Elemento-Modular-Cloud)"
_retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
session.mount("http://", HTTPAdapter(max_retries=_retry))
session.mount("https://", HTTPAdapter(max_retries=_retry))


def version_key(version):
    """Numeric sort key: "22.10" > "22.9", "24.04.5.1" > "24.04.5"."""
    return tuple(int(x) for x in re.findall(r"\d+", version or ""))


def leading(n):
    """Major-line extractor keeping the first n numeric components ("9.8" -> "9" for n=1)."""
    return lambda version: ".".join(re.findall(r"\d+", version)[:n])


# ---------------------------------------------------------------------------
# Sources
#
# Every (os_flavour, kind) pair maps to how its ISOs are laid out on the mirror:
#   file    regex matched against file names; the optional "version" group is the
#           full release version (otherwise the release directory name is used)
#   major   function mapping a version to its major line; None for rolling distros
#   layout  regex splitting the entry URL into the mirror root and a per-release
#           directory ("dir" group); None when all releases live in one directory
#   dirs    regex selecting release directories in the root listing
#   subdir  path of the ISO directory below the root, formatted with {dir}
#   codename  the release directory is the codename (Ubuntu) and appears in os_version
#   api     "pop": releases come from the Pop!_OS build API instead of a listing
# ---------------------------------------------------------------------------


def _per_release_dirs(layout, dirs, subdir, file, major, **extra):
    return dict(layout=layout, dirs=dirs, subdir=subdir, file=file, major=major, **extra)


def _flat(file, major=None):
    return dict(layout=None, file=file, major=major)


def _rhel_like(prefix, kind):
    return _per_release_dirs(
        layout=r"^(?P<root>.*/)(?P<dir>\d+)/isos/x86_64/[^/]+$",
        dirs=r"^\d+$",
        subdir="{dir}/isos/x86_64/",
        file=rf"^{prefix}-(?P<version>\d+\.\d+)-x86_64-{kind}\.iso$",
        major=leading(1),
    )


def _fedora(edition, file):
    return _per_release_dirs(
        layout=rf"^(?P<root>.*/releases/)(?P<dir>\d+)/{edition}/x86_64/iso/[^/]+$",
        dirs=r"^\d+$",
        subdir=f"{{dir}}/{edition}/x86_64/iso/",
        file=file,
        major=leading(1),
    )


def _ubuntu(kind):
    return _per_release_dirs(
        layout=r"^(?P<root>.*/)(?P<dir>[a-z]+)/[^/]+$",
        dirs=r"^[a-z]+$",
        subdir="{dir}/",
        file=rf"^ubuntu-(?P<version>\d+\.\d+(?:\.\d+)*)-{kind}-amd64\.iso$",
        major=leading(2),
        codename=True,
    )


def _mint(edition):
    return _per_release_dirs(
        layout=r"^(?P<root>.*/stable/)(?P<dir>\d+(?:\.\d+)?)/[^/]+$",
        dirs=r"^\d+(?:\.\d+)?$",
        subdir="{dir}/",
        file=rf"^linuxmint-(?P<version>\d+(?:\.\d+)?)-{edition}-64bit\.iso$",
        major=leading(1),
    )


SOURCES = {
    ("ubuntu", "desktop"): _ubuntu("desktop"),
    ("ubuntu", "server"): _ubuntu("live-server"),
    ("almalinux", "boot"): _rhel_like("AlmaLinux", "boot"),
    ("almalinux", "minimal"): _rhel_like("AlmaLinux", "minimal"),
    ("rocky", "boot"): _rhel_like("Rocky", "boot"),
    ("rocky", "minimal"): _rhel_like("Rocky", "minimal"),
    ("fedora", "workstation"): _fedora(
        "Workstation", r"^Fedora-Workstation-Live-(?P<version>\d+)-[\d.]+\.x86_64\.iso$"
    ),
    ("fedora", "silverblue"): _fedora(
        "Silverblue", r"^Fedora-Silverblue-ostree-x86_64-(?P<version>\d+)-[\d.]+\.iso$"
    ),
    ("fedora", "server"): _fedora(
        "Server", r"^Fedora-Server-netinst-x86_64-(?P<version>\d+)-[\d.]+\.iso$"
    ),
    ("mint", "cinnamon"): _mint("cinnamon"),
    ("mint", "mate"): _mint("mate"),
    ("mint", "xfce"): _mint("xfce"),
    ("mint", "lmde"): _flat(r"^lmde-(?P<version>\d+)-cinnamon-64bit\.iso$", leading(1)),
    # OpenBSD has no point releases: every release (7.8, 7.9, ...) is its own line.
    # The mirror only keeps supported releases, so old lines retire on their own.
    ("openbsd", ""): _per_release_dirs(
        layout=r"^(?P<root>.*/OpenBSD/)(?P<dir>\d+\.\d+)/amd64/[^/]+$",
        dirs=r"^\d+\.\d+$",
        subdir="{dir}/amd64/",
        file=r"^install\d+\.iso$",
        major=leading(2),
    ),
    # Alpine release branches (v3.23, v3.24) are the lines, x.y.Z the minor updates.
    ("alpine", ""): _per_release_dirs(
        layout=r"^(?P<root>.*/)(?P<dir>v\d+\.\d+)/releases/x86_64/[^/]+$",
        dirs=r"^v\d+\.\d+$",
        subdir="{dir}/releases/x86_64/",
        file=r"^alpine-standard-(?P<version>\d+\.\d+\.\d+)-x86_64\.iso$",
        major=leading(2),
    ),
    # debian-cd/current/ and openSUSE-current/ only hold the current release: a new
    # major replaces the old one there, which adds the new line and retires the old.
    ("debian", ""): _flat(r"^debian-(?P<version>\d+\.\d+\.\d+)-amd64-netinst\.iso$", leading(1)),
    ("opensuse", "leap"): _flat(
        r"^Leap-(?P<version>\d+\.\d+)-offline-installer-x86_64\.install\.iso$", leading(1)
    ),
    ("opensuse", "tumbleweed-gnome"): _flat(r"^openSUSE-Tumbleweed-GNOME-Live-x86_64-Current\.iso$"),
    ("opensuse", "tumbleweed"): _flat(r"^openSUSE-Tumbleweed-DVD-x86_64-Current\.iso$"),
    ("pop", "intel"): dict(api="pop", file=r"^pop-os_(?P<version>\d+\.\d+)_amd64_intel_\d+\.iso$", major=leading(2)),
    ("pop", "nvidia"): dict(api="pop", file=r"^pop-os_(?P<version>\d+\.\d+)_amd64_nvidia_\d+\.iso$", major=leading(2)),
    ("gentoo", ""): _flat(r"^install-amd64-minimal-(?P<version>\d+T\d+Z)\.iso$"),
    ("arch", ""): _flat(r"^archlinux-x86_64\.iso$"),
    ("temple", ""): _flat(r"^TempleOS\.ISO$"),
    ("void", "base"): _flat(r"^void-live-x86_64-(?P<version>\d+)-base\.iso$"),
    ("void", "xfce"): _flat(r"^void-live-x86_64-(?P<version>\d+)-xfce\.iso$"),
    ("void", "gnome"): _flat(r"^void-live-x86_64-(?P<version>\d+)-gnome\.iso$"),
    ("void", "cinnamon"): _flat(r"^void-live-x86_64-(?P<version>\d+)-cinnamon\.iso$"),
}


def get_kind(entry):
    """Returns the variant of an entry, i.e. the second half of its SOURCES key."""
    flavour = entry.get("os_flavour", "").lower()
    version = entry.get("os_version", "").lower()

    def first_of(*kinds, default=""):
        return next((k for k in kinds if k in version), default)

    if flavour == "ubuntu":
        return first_of("desktop", default="server")
    if flavour in ("almalinux", "rocky"):
        return first_of("boot", default="minimal")
    if flavour == "fedora":
        return first_of("workstation", "silverblue", "server")
    if flavour == "mint":
        return first_of("lmde", "cinnamon", "mate", "xfce")
    if flavour == "opensuse":
        if "tumbleweed" in version:
            return "tumbleweed-gnome" if "gnome" in version else "tumbleweed"
        return "leap"
    if flavour == "pop":
        return first_of("nvidia", default="intel")
    if flavour == "void":
        return first_of("xfce", "gnome", "cinnamon", default="base")
    return ""


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------

_listing_cache = {}
_osinfo_cache = {}


def list_dir(url):
    """Returns the entry names of a mirror directory listing (directories end with "/"),
    or None if it can't be read. Results are cached for the whole run."""
    if url not in _listing_cache:
        try:
            resp = session.get(url, timeout=TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            names = []
            for a in soup.find_all("a", href=True):
                href = a["href"].split("?")[0].split("#")[0]
                if href.startswith("./"):
                    href = href[2:]
                if not href or href.startswith(("/", "..")) or "://" in href:
                    continue
                names.append(unquote(href))
            _listing_cache[url] = names
        except requests.RequestException as e:
            print(f"    ! cannot list {url}: {e}")
            _listing_cache[url] = None
    return _listing_cache[url]


def url_ok(url):
    """True if the URL answers 200 (following redirects). Falls back to a one-byte
    GET for servers that reject HEAD."""
    try:
        resp = session.head(url, allow_redirects=True, timeout=TIMEOUT)
        if resp.status_code in (403, 405, 501):
            resp = session.get(url, headers={"Range": "bytes=0-0"}, stream=True, allow_redirects=True, timeout=TIMEOUT)
            resp.close()
            return resp.status_code in (200, 206)
        return resp.status_code == 200
    except requests.RequestException as e:
        print(f"    ! cannot reach {url}: {e}")
        return False


def libosinfo_exists(os_id):
    """True/False if the libosinfo id is/isn't in osinfo-db, None if osinfo-db can't be reached."""
    if os_id not in _osinfo_cache:
        clean_id = re.sub(r"^https?://", "", os_id)
        vendor, _, path = clean_id.partition("/")
        url = OSINFO_RAW_URL.format(vendor=vendor, name=path.replace("/", "-"))
        try:
            resp = session.head(url, allow_redirects=True, timeout=TIMEOUT)
            _osinfo_cache[os_id] = {200: True, 404: False}.get(resp.status_code)
        except requests.RequestException:
            _osinfo_cache[os_id] = None
    return _osinfo_cache[os_id]


# ---------------------------------------------------------------------------
# Release discovery
# ---------------------------------------------------------------------------


def major_of(source, version):
    return source["major"](version) if source["major"] else ""


def parse_entry_url(source, url):
    """Returns (version, release_dir) of the ISO an entry points at, or None when the
    URL doesn't follow the source layout."""
    name = url.rsplit("/", 1)[-1]
    file_match = re.match(source["file"], name)
    if not file_match:
        return None
    release_dir = None
    if source.get("layout"):
        layout_match = re.match(source["layout"], url)
        if not layout_match:
            return None
        release_dir = layout_match["dir"]
    version = file_match.groupdict().get("version") or release_dir or ""
    return version, release_dir


def find_pop_releases(source, sample_url, min_major):
    """Asks the Pop!_OS build API for every YY.04/YY.10 release from min_major to today."""
    channel = re.search(r"_amd64_(\w+?)_\d+\.iso$", sample_url)[1]
    releases = {}
    first_year = int(min_major.split(".")[0])
    for year in range(first_year, date.today().year % 100 + 1):
        for month in ("04", "10"):
            version = f"{year}.{month}"
            if version_key(version) < version_key(min_major):
                continue
            try:
                resp = session.get(POP_API_URL.format(version=version, channel=channel), timeout=TIMEOUT)
            except requests.RequestException as e:
                print(f"    ! cannot reach the Pop!_OS API: {e}")
                return None
            if resp.status_code == 404:
                continue
            if not resp.ok:
                print(f"    ! Pop!_OS API answered {resp.status_code} for {version}")
                return None
            build = resp.json()
            releases[version] = {"version": version, "url": build["url"], "codename": None}
    return releases


def find_releases(source, sample_url, min_major):
    """Returns {major: newest release} for every major line >= min_major available on
    the mirror, or None if the mirror couldn't be read."""
    if source.get("api") == "pop":
        return find_pop_releases(source, sample_url, min_major)

    if source.get("layout"):
        root = re.match(source["layout"], sample_url)["root"]
        names = list_dir(root)
        if names is None:
            return None
        candidates = []
        for name in names:
            release_dir = name.rstrip("/")
            if not name.endswith("/") or not re.match(source["dirs"], release_dir):
                continue
            dir_major = major_of(source, release_dir)
            # Release directories without a version in their name (Ubuntu codenames) are always checked
            if dir_major and version_key(dir_major) < version_key(min_major):
                continue
            candidates.append((release_dir, urljoin(root, source["subdir"].format(dir=release_dir))))
    else:
        candidates = [(None, sample_url.rsplit("/", 1)[0] + "/")]

    releases = {}
    for release_dir, dir_url in candidates:
        names = list_dir(dir_url)
        if names is None:
            if not source.get("layout"):
                return None
            continue
        for name in names:
            file_match = re.match(source["file"], name)
            if not file_match:
                continue
            version = file_match.groupdict().get("version") or release_dir or ""
            major = major_of(source, version)
            if major and version_key(major) < version_key(min_major):
                continue
            best = releases.get(major)
            if best is None or version_key(version) > version_key(best["version"]):
                releases[major] = {
                    "version": version,
                    "url": urljoin(dir_url, name),
                    "codename": release_dir if source.get("codename") else None,
                }
    return releases


# ---------------------------------------------------------------------------
# Entry updates
# ---------------------------------------------------------------------------


def sync_libosinfo(entry, major):
    """Points a versioned libosinfo id (".../almalinux/9") at the entry's major line,
    keeping the current id while osinfo-db doesn't know the new one yet."""
    current = entry.get("libosinfo_id")
    if not current or not major or "/" not in current:
        return
    base, last = current.rsplit("/", 1)
    if not re.match(r"^\d", last) or last == major:
        return
    candidate = f"{base}/{major}"
    exists = libosinfo_exists(candidate)
    if exists:
        entry["libosinfo_id"] = candidate
        print(f"    libosinfo_id -> {candidate}")
    elif exists is False:
        print(f"    libosinfo_id {candidate} not in osinfo-db yet, keeping {current}")


def is_ubuntu_lts(major):
    year, month = (int(x) for x in major.split("."))
    return year % 2 == 0 and month == 4


def apply_release(entry, source, release, major, old_codename, new_line=False):
    """Points an entry at a release. Returns False (entry untouched) if the new URL doesn't answer."""
    # Same file reachable through another path of the mirror: keep the entry's URL
    same_file = release["url"].rsplit("/", 1)[-1] == entry["iso_url"].rsplit("/", 1)[-1]
    if not same_file:
        if not url_ok(release["url"]):
            print(f"    ! {release['url']} does not answer, not updating")
            return False
        entry["iso_url"] = release["url"]
        print(f"    iso_url -> {release['url']}")

    if source["major"]:
        os_version = VERSION_TOKEN.sub(release["version"], entry["os_version"], count=1)
        new_codename = release["codename"]
        if old_codename and new_codename and old_codename != new_codename:
            os_version = os_version.replace(old_codename, new_codename)
        if new_line and entry.get("os_flavour", "").lower() == "ubuntu":
            os_version = re.sub(r"-LTS$", "", os_version)
            if is_ubuntu_lts(major):
                os_version += "-LTS"
        if os_version != entry["os_version"]:
            print(f"    os_version -> {os_version}")
            entry["os_version"] = os_version
        sync_libosinfo(entry, major)
    return True


def update_group(key, entries):
    """Updates the entries of one variant in place.
    Returns (entries to remove, new entries to append after the group)."""
    source = SOURCES[key]
    managed = []
    for entry in entries:
        parsed = parse_entry_url(source, entry["iso_url"])
        if parsed is None:
            print(f"  ! {entry['name']} ({entry['os_version']}): URL doesn't match the {key} layout, left untouched")
            continue
        version, release_dir = parsed
        managed.append((entry, major_of(source, version), release_dir))
    if not managed:
        return [], []

    min_major = min((major for _, major, _ in managed), key=version_key)
    releases = find_releases(source, managed[0][0]["iso_url"], min_major)
    if releases is None:
        print("  ! mirror unreachable, keeping entries as they are")
        return [], []
    if not releases:
        print("  ! no matching ISO on the mirror, keeping entries as they are")
        return [], []
    newest_major = max(releases, key=version_key)

    removed, seen_majors = [], set()
    for entry, major, release_dir in managed:
        label = f"{entry['name']} ({entry['os_version']})"
        if major in seen_majors:
            print(f"  - {label}: duplicate of major line {major}, removed")
            removed.append(entry)
            continue
        seen_majors.add(major)

        release = releases.get(major)
        if release is None:
            if version_key(major) < version_key(newest_major) and not url_ok(entry["iso_url"]):
                print(f"  - {label}: gone from the mirror and superseded by {newest_major}, removed")
                removed.append(entry)
            else:
                print(f"  ! {label}: not found on the mirror, kept")
            continue

        print(f"  * {label}")
        apply_release(entry, source, release, major, release_dir)

    # New major lines are cloned from the newest surviving one
    removed_ids = {id(e) for e in removed}
    survivors = [m for m in managed if id(m[0]) not in removed_ids]
    if not survivors:
        return removed, []
    template, template_major, _ = max(survivors, key=lambda m: version_key(m[1]))
    template_codename = parse_entry_url(source, template["iso_url"])[1]
    added = []
    for major in sorted(releases, key=version_key):
        if version_key(major) <= version_key(template_major):
            continue
        new_entry = copy.deepcopy(template)
        print(f"  + new major line {major} (from {template['os_version']})")
        if apply_release(new_entry, source, releases[major], major, template_codename, new_line=True):
            added.append(new_entry)
    return removed, added


def update_os_json(input_file, output_file):
    if not os.path.exists(input_file):
        print(f"File not found: {input_file}")
        return

    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    groups = {}
    for entry in data:
        if entry.get("os_family") == "windows" or not entry.get("iso_url"):
            continue
        key = (entry.get("os_flavour", "").lower(), get_kind(entry))
        if key not in SOURCES:
            print(f"! {entry.get('name')} ({entry.get('os_version')}): no source for {key}, left untouched")
            continue
        groups.setdefault(key, []).append(entry)

    removed_ids = set()
    added_after = {}
    for key, entries in groups.items():
        print(f"Checking {key[0]} {key[1]}".rstrip() + "...")
        removed, added = update_group(key, entries)
        removed_ids.update(id(e) for e in removed)
        survivors = [e for e in entries if id(e) not in removed_ids]
        if added:
            anchor = survivors[-1] if survivors else entries[-1]
            added_after.setdefault(id(anchor), []).extend(added)

    result = []
    for entry in data:
        if id(entry) not in removed_ids:
            result.append(entry)
        result.extend(added_after.get(id(entry), []))

    # Safety net: never ship two entries with the same ISO or the same version label
    final_data, seen_urls, seen_sigs = [], set(), set()
    for entry in result:
        url = entry.get("iso_url")
        sig = (entry.get("os_family"), entry.get("os_flavour"), entry.get("os_version"))
        if (url and url in seen_urls) or sig in seen_sigs:
            print(f"! dropping duplicate {entry.get('name')} ({entry.get('os_version')})")
            continue
        if url:
            seen_urls.add(url)
        seen_sigs.add(sig)
        final_data.append(entry)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(final_data, f, indent=2)
        f.write("\n")
    print(f"\nUpdate complete: {len(data)} -> {len(final_data)} entries. Data saved to {output_file}")


if __name__ == "__main__":
    update_os_json("iso.json", "iso_updated.json")
