import base64
import time
import requests
import streamlit as st
import threading


API_ROOT = "https://api.github.com"

REPORT_SYNC_INTERVAL_SECONDS = 300  # 5 minutes


class ReportSyncGate:
    """Process-wide gate so employee actions do not update GitHub on every IN/OUT."""
    def __init__(self):
        self.lock = threading.RLock()
        self.last_sync = 0.0

    def is_due(self, force=False):
        with self.lock:
            return force or (time.monotonic() - self.last_sync >= REPORT_SYNC_INTERVAL_SECONDS)

    def mark_synced(self):
        with self.lock:
            self.last_sync = time.monotonic()


@st.cache_resource(show_spinner=False)
def get_report_sync_gate():
    return ReportSyncGate()


def report_sync_due(force=False):
    return get_report_sync_gate().is_due(force=force)


def mark_report_synced():
    get_report_sync_gate().mark_synced()


def _headers():
    return {
        "Authorization": f"Bearer {st.secrets['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repo():
    return st.secrets["GITHUB_REPORT_REPO"]


def _url(path):
    return f"{API_ROOT}/repos/{_repo()}/contents/{path}"


def _get(path):
    response = requests.get(
        _url(path),
        headers=_headers(),
        timeout=30,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def report_exists(path):
    return _get(path) is not None


def upload_or_update_report(path, file_bytes, commit_message, retries=4):
    encoded = base64.b64encode(file_bytes).decode("utf-8")

    for attempt in range(retries):
        current = _get(path)
        payload = {
            "message": commit_message,
            "content": encoded,
        }

        if current and current.get("sha"):
            payload["sha"] = current["sha"]

        response = requests.put(
            _url(path),
            headers=_headers(),
            json=payload,
            timeout=60,
        )

        if response.ok:
            return response.json()

        # SHA conflict / concurrent update. Refresh and retry.
        if response.status_code in (409, 422):
            time.sleep(1.5 * (attempt + 1))
            continue

        response.raise_for_status()

    raise RuntimeError(
        "GitHub report update failed after multiple retries."
    )


def list_reports():
    folder = "data/visits"
    data = _get(folder)
    if not data:
        return []

    return sorted(
        [
            item["path"]
            for item in data
            if item.get("type") == "file"
            and item.get("name", "").lower().endswith(".xlsx")
        ],
        reverse=True,
    )


@st.cache_data(ttl=120, max_entries=4, show_spinner=False)
def cached_list_reports():
    """Cache the GitHub report directory listing during normal admin reruns."""
    return list_reports()


def download_report(path):
    data = _get(path)
    if not data:
        raise FileNotFoundError(path)

    return base64.b64decode(data["content"])
