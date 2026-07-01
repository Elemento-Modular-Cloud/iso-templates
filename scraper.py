import json
import os
import re
from urllib.parse import urljoin
import copy
import requests
from bs4 import BeautifulSoup


def natural_sort_key(s):
    """Sorts strings logically (e.g. 22.3 comes after 22.2, 10 comes after 9)"""
    return [
        int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", s)
    ]


def get_regex_pattern(flavour, version):
    """Returns a specialized regex pattern based on the OS flavour and version."""
    flavour = flavour.lower()
    version = version.lower()

    if flavour == "ubuntu":
        if "desktop" in version:
            return r"ubuntu-\d+\.\d+(?:\.\d+)?-desktop-amd64\.iso$"
        return r"ubuntu-\d+\.\d+(?:\.\d+)?-(?:live-)?server-amd64\.(iso|img)$"
    elif flavour == "opensuse":
        if "tumbleweed" in version:
            if "gnome" in version:
                return r"openSUSE-Tumbleweed-GNOME-Live-x86_64-Current\.iso$"
            return r"openSUSE-Tumbleweed-.*-Current\.iso$"
        return r"Leap-\d+\.\d+-offline-installer-.*\.iso$"
    elif flavour == "gentoo":
        return r"install-x86-minimal-\d+T\d+Z\.iso$"
    elif flavour == "pop":
        return r"pop-os_.*\.iso$"
    elif flavour == "mint":
        if "lmde" in version:
            return r"lmde-\d+-.*-64bit\.iso$"
        elif "cinnamon" in version:
            return r"linuxmint-\d+(?:\.\d+)?-cinnamon-64bit\.iso$"
        elif "mate" in version:
            return r"linuxmint-\d+(?:\.\d+)?-mate-64bit\.iso$"
        elif "xfce" in version:
            return r"linuxmint-\d+(?:\.\d+)?-xfce-64bit\.iso$"
    elif flavour == "void":
        if "xfce" in version:
            return r"void-live-x86_64-\d+-xfce\.iso$"
        elif "gnome" in version:
            return r"void-live-x86_64-\d+-gnome\.iso$"
        elif "cinnamon" in version:
            return r"void-live-x86_64-\d+-cinnamon\.iso$"
        else:
            return r"void-live-x86_64-\d+-base\.iso$"
    elif flavour == "almalinux":
        if "boot" in version:
            return r"AlmaLinux-\d+(?:\.\d+)+-x86_64-boot\.iso$"
        return r"AlmaLinux-\d+(?:\.\d+)+-x86_64-minimal\.iso$"
    elif flavour == "arch":
        return r"archlinux-x86_64\.iso$"
    elif flavour == "temple":
        return r"TempleOS\.ISO$"
    elif flavour == "debian":
        return r"debian-\d+\.\d+\.\d+-amd64-netinst\.iso$"
    elif flavour == "fedora":
        if "workstation" in version:
            return r"Fedora-Workstation-Live-\d+-.*\.iso$"
        elif "silverblue" in version:
            return r"Fedora-Silverblue-ostree-x86_64-\d+-.*\.iso$"
        elif "server" in version:
            return r"Fedora-Server-netinst-x86_64-\d+-.*\.iso$"
    elif flavour == "rocky":
        if "boot" in version:
            return r"Rocky-\d+(?:\.\d+)+-x86_64-boot\.iso$"
        return r"Rocky-\d+(?:\.\d+)+-x86_64-minimal\.iso$"
    elif flavour == "openbsd":
        return r"install\d+\.iso$"
    elif flavour == "alpine":
        return r"alpine-standard-\d+\.\d+\.\d+-x86_64\.iso$"

    return r".*\.(iso|img)$"


def get_newer_major_versions(url, flavour):
    """Climbs the directory tree to discover if newer major version folders have been added."""
    flavour = flavour.lower()
    newer_versions = []
    try:
        if flavour == "fedora":
            match = re.search(r"/releases/(\d+)/", url)
            if match:
                current_ver = match.group(1)
                parent_dir = url.split(f"/releases/{current_ver}/")[0] + "/releases/"
                resp = requests.get(parent_dir, timeout=10)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    links = [
                        a["href"].strip("/") for a in soup.find_all("a", href=True)
                    ]
                    for l in links:
                        if l.isdigit() and int(l) > int(current_ver):
                            new_url = url.replace(
                                f"/releases/{current_ver}/", f"/releases/{l}/"
                            )
                            newer_versions.append((current_ver, l, new_url))

        elif flavour in ["almalinux", "rocky"]:
            match = re.search(r"/(\d+)/isos/", url)
            if match:
                current_ver = match.group(1)
                parent_dir = url.split(f"/{current_ver}/isos/")[0] + "/"
                resp = requests.get(parent_dir, timeout=10)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    links = [
                        a["href"].strip("/") for a in soup.find_all("a", href=True)
                    ]
                    for l in links:
                        if l.isdigit() and int(l) > int(current_ver):
                            new_url = url.replace(
                                f"/{current_ver}/isos/", f"/{l}/isos/"
                            )
                            newer_versions.append((current_ver, l, new_url))

        elif flavour == "alpine":
            match = re.search(r"/(v\d+\.\d+)/", url)
            if match:
                current_ver = match.group(1)
                parent_dir = url.split(f"/{current_ver}/")[0] + "/"
                resp = requests.get(parent_dir, timeout=10)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    links = [
                        a["href"].strip("/") for a in soup.find_all("a", href=True)
                    ]
                    for l in links:
                        if re.match(r"^v\d+\.\d+$", l):
                            curr_parts = [int(x) for x in current_ver[1:].split(".")]
                            l_parts = [int(x) for x in l[1:].split(".")]
                            if l_parts > curr_parts:
                                new_url = url.replace(f"/{current_ver}/", f"/{l}/")
                                newer_versions.append((current_ver, l, new_url))

        elif flavour == "mint":
            match = re.search(r"/stable/(\d+(?:\.\d+)?)/", url)
            if match:
                current_ver = match.group(1)
                parent_dir = url.split(f"/stable/{current_ver}/")[0] + "/stable/"
                resp = requests.get(parent_dir, timeout=10)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    links = [
                        a["href"].strip("/") for a in soup.find_all("a", href=True)
                    ]
                    for l in links:
                        if re.match(r"^\d+(\.\d+)?$", l):
                            if float(l) > float(current_ver):
                                new_url = url.replace(
                                    f"/stable/{current_ver}/", f"/stable/{l}/"
                                )
                                newer_versions.append((current_ver, l, new_url))

        elif flavour == "openbsd":
            match = re.search(r"/OpenBSD/(\d+\.\d+)/", url)
            if match:
                current_ver = match.group(1)
                parent_dir = url.split(f"/{current_ver}/")[0] + "/"
                resp = requests.get(parent_dir, timeout=10)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    links = [
                        a["href"].strip("/") for a in soup.find_all("a", href=True)
                    ]
                    for l in links:
                        if re.match(r"^\d+\.\d+$", l):
                            if float(l) > float(current_ver):
                                new_url = url.replace(f"/{current_ver}/", f"/{l}/")
                                newer_versions.append((current_ver, l, new_url))

        elif flavour == "pop":
            match = re.search(r"/(\d+\.\d+)/amd64/", url)
            if match:
                current_ver = match.group(1)
                parent_dir = url.split(f"/{current_ver}/amd64/")[0] + "/"
                resp = requests.get(parent_dir, timeout=10)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    links = [
                        a["href"].strip("/") for a in soup.find_all("a", href=True)
                    ]
                    for l in links:
                        if re.match(r"^\d+\.\d+$", l):
                            if float(l) > float(current_ver):
                                new_url = url.replace(f"/{current_ver}/", f"/{l}/")
                                newer_versions.append((current_ver, l, new_url))

        elif flavour == "debian":
            # If the user uses a numbered release rather than /current/
            match = re.search(r"/debian-cd/(\d+\.\d+\.\d+)/", url)
            if match:
                current_ver = match.group(1)
                parent_dir = url.split(f"/{current_ver}/")[0] + "/"
                resp = requests.get(parent_dir, timeout=10)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    links = [
                        a["href"].strip("/") for a in soup.find_all("a", href=True)
                    ]
                    for l in links:
                        if re.match(r"^\d+\.\d+\.\d+$", l):
                            curr_parts = [int(x) for x in current_ver.split(".")]
                            l_parts = [int(x) for x in l.split(".")]
                            if l_parts > curr_parts:
                                new_url = url.replace(f"/{current_ver}/", f"/{l}/")
                                newer_versions.append((current_ver, l, new_url))

    except Exception:
        pass
    return newer_versions


def discover_new_entries(data):
    """Clones JSON entries for newly discovered major versions so they co-exist automatically."""
    new_data = []
    seen_signatures = set()

    def get_sig(e):
        return f"{e.get('os_family')}-{e.get('os_flavour')}-{e.get('os_version')}"

    for entry in data:
        sig = get_sig(entry)
        if sig not in seen_signatures:
            seen_signatures.add(sig)
            new_data.append(entry)

    discovered = []
    for entry in data:
        iso_url = entry.get("iso_url")
        flavour = entry.get("os_flavour", "")
        if not iso_url or entry.get("os_family") == "windows":
            continue

        newer_versions = get_newer_major_versions(iso_url, flavour)
        for old_v, new_v, new_url in newer_versions:
            new_entry = copy.deepcopy(entry)
            new_entry["iso_url"] = new_url

            old_v_clean = old_v.lstrip("v")
            new_v_clean = new_v.lstrip("v")

            old_os_v = new_entry.get("os_version", "")
            if old_v_clean in old_os_v:
                new_entry["os_version"] = old_os_v.replace(old_v_clean, new_v_clean)
            elif old_v in old_os_v:
                new_entry["os_version"] = old_os_v.replace(old_v, new_v)

            old_lib = new_entry.get("libosinfo_id", "")
            if old_lib:
                if old_v_clean in old_lib:
                    new_entry["libosinfo_id"] = old_lib.replace(
                        old_v_clean, new_v_clean
                    )
                elif old_v in old_lib:
                    new_entry["libosinfo_id"] = old_lib.replace(old_v, new_v)

            sig = get_sig(new_entry)
            if sig not in seen_signatures:
                seen_signatures.add(sig)
                discovered.append(new_entry)

    return new_data + discovered


def patch_dynamic_string(old_url, new_url, target_str):
    """Dynamically patches minor point-release version numbers in metadata strings."""
    if not target_str:
        return target_str

    old_matches = re.findall(r"\d+(?:\.\d+)+|\d+", old_url)
    new_matches = re.findall(r"\d+(?:\.\d+)+|\d+", new_url)

    for old_val, new_val in zip(old_matches, new_matches):
        if old_val != new_val and old_val in target_str:
            target_str = target_str.replace(old_val, new_val)

    return target_str


def verify_libosinfo_id(os_id):
    """Checks the libosinfo-db GitLab repository to see if the URN ID actually exists."""
    if not os_id:
        return False

    try:
        clean_id = os_id.replace("http://", "").replace("https://", "")
        parts = clean_id.split("/", 1)
        if len(parts) != 2:
            return False

        vendor = parts[0]
        filename_base = parts[1].replace("/", "-")
        gitlab_raw_url = f"https://gitlab.com/libosinfo/osinfo-db/-/raw/main/data/os/{vendor}/{filename_base}.xml.in"

        response = requests.head(gitlab_raw_url, timeout=5)
        return response.status_code == 200
    except Exception:
        return False


def get_latest_libosinfo_id(current_id):
    """Queries GitLab API to fallback to the highest existing libosinfo tag and aggressively filters non-production codenames."""
    if not current_id:
        return current_id

    try:
        clean_id = current_id.replace("http://", "").replace("https://", "")
        parts = clean_id.split("/")
        vendor = parts[0]

        product_path = "-".join(parts[1:-1])
        if not product_path:
            product_path = parts[1]

        current_version_str = parts[-1]

        api_url = f"https://gitlab.com/api/v4/projects/libosinfo%2Fosinfo-db/repository/tree?path=data/os/{vendor}&per_page=100"

        files = []
        page = 1
        while True:
            resp = requests.get(f"{api_url}&page={page}", timeout=10)
            if resp.status_code != 200:
                break
            data = resp.json()
            if not data:
                break
            files.extend(data)
            page += 1

        valid_files = []
        for f in files:
            name = f["name"]
            if name.startswith(product_path + "-") and name.endswith(".xml.in"):
                version_str = name[len(product_path) + 1 : -7]

                if re.match(r"^\d", current_version_str):
                    if not re.match(r"^\d", version_str):
                        continue
                    if "kitten" in version_str.lower():
                        continue
                    if "rawhide" in version_str.lower():
                        continue

                valid_files.append(name)

        if not valid_files:
            return current_id

        valid_files.sort(key=natural_sort_key)
        latest_file = valid_files[-1]

        prefix_len = len(product_path) + 1
        version_str = latest_file[prefix_len:-7]

        original_base = current_id.rsplit("/", 1)[0]
        return f"{original_base}/{version_str}"

    except Exception:
        return current_id


def scrape_latest_iso(base_url, pattern):
    """Scrapes the base_url directory and finds the newest file matching the regex."""
    try:
        response = requests.get(base_url, timeout=10)
        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, "html.parser")
        links = [a["href"] for a in soup.find_all("a", href=True)]

        regex = re.compile(pattern, re.IGNORECASE)
        matching_isos = [l for l in links if regex.search(l)]

        if not matching_isos:
            return None

        matching_isos.sort(key=natural_sort_key)
        latest_iso = matching_isos[-1]

        return urljoin(base_url, latest_iso)
    except Exception:
        return None


def update_os_json(input_file, output_file):
    if not os.path.exists(input_file):
        print(f"File not found: {input_file}")
        return

    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    print("Scanning mirrors for new major non-rolling versions...")
    extended_data = discover_new_entries(data)

    valid_data = []

    for entry in extended_data:
        if entry.get("os_family") == "windows":
            valid_data.append(entry)
            continue

        iso_url = entry.get("iso_url")
        if not iso_url:
            valid_data.append(entry)
            continue

        name = entry.get("name", "Unknown")
        flavour = entry.get("os_flavour", "")
        version = entry.get("os_version", "")

        print(f"Checking {name} ({version})...")

        base_url = iso_url.rsplit("/", 1)[0] + "/"
        pattern = get_regex_pattern(flavour, version)

        new_url = scrape_latest_iso(base_url, pattern)

        if new_url:
            if new_url != iso_url:
                patched_version = patch_dynamic_string(
                    iso_url, new_url, entry.get("os_version", "")
                )
                patched_libosinfo = patch_dynamic_string(
                    iso_url, new_url, entry.get("libosinfo_id", "")
                )

                entry["os_version"] = patched_version
                entry["iso_url"] = new_url

                print(f"  ✅ UPDATED: {new_url}")
                print(f"     -> Version set to: {patched_version}")

                old_libosinfo = entry.get("libosinfo_id")
                if patched_libosinfo and patched_libosinfo != old_libosinfo:
                    if verify_libosinfo_id(patched_libosinfo):
                        entry["libosinfo_id"] = patched_libosinfo
                        print(
                            f"     -> libosinfo synced & VERIFIED: {patched_libosinfo}"
                        )
                    else:
                        latest_db_id = get_latest_libosinfo_id(old_libosinfo)
                        entry["libosinfo_id"] = latest_db_id
                        print(
                            f"     -> libosinfo tag pending upstream. Using latest DB match: {latest_db_id}"
                        )
            else:
                print("  ✓ Already up to date.")
        else:
            print(
                "  ❌ Unresponsive or missing file. Keeping old entry without updating."
            )

        valid_data.append(entry)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(valid_data, f, indent=2)
    print(f"\nUpdate complete. Data saved to {output_file}")


if __name__ == "__main__":
    update_os_json("iso.json", "iso_updated.json")

