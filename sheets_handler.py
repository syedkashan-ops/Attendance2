from datetime import datetime
from zoneinfo import ZoneInfo
import uuid
import math
import time
import threading
import pandas as pd
import gspread
import streamlit as st
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError

SHEET_NAME = "Visits"
HEADERS = [
    "Visit ID", "Date", "Employee Code", "Employee Name", "Outlet Code", "Outlet Name",
    "IN Date/Time", "IN Latitude", "IN Longitude", "IN GPS Accuracy",
    "OUT Date/Time", "OUT Latitude", "OUT Longitude", "OUT GPS Accuracy",
    "IN-OUT GPS Distance (m)", "GPS Status", "Time Spent", "Time Spent Hours", "Status",
    "IN-Outlet GPS Distance (m)", "OUT-Outlet GPS Distance (m)", "Outlet GPS Status",
]
INTERNAL_ROW = "__sheet_row"
REFRESH_SECONDS = 900  # production: shared mirror is updated immediately after every local write
# Google Sheets user/project write quota is limited. Serialize writes and keep
# a small safety gap so a burst of employees does not turn into a 429 storm.
SHEET_WRITE_MIN_INTERVAL_SECONDS = 1.10
SHEET_WRITE_RETRIES = 6


class SheetWriteGate:
    """Process-wide write throttle shared by all Streamlit sessions."""
    def __init__(self):
        self.lock = threading.RLock()
        self.last_write = 0.0

    def wait_turn(self):
        with self.lock:
            now = time.monotonic()
            wait = SHEET_WRITE_MIN_INTERVAL_SECONDS - (now - self.last_write)
            if wait > 0:
                time.sleep(wait)
            self.last_write = time.monotonic()


@st.cache_resource(show_spinner=False)
def get_sheet_write_gate():
    return SheetWriteGate()


def _sheet_write(operation):
    """Run a Sheets write with pacing + exponential backoff for transient errors."""
    last_error = None
    for attempt in range(SHEET_WRITE_RETRIES):
        get_sheet_write_gate().wait_turn()
        try:
            return operation()
        except APIError as exc:
            last_error = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            text = str(exc)
            retryable = status in (429, 500, 502, 503, 504) or "429" in text or "503" in text
            if not retryable or attempt == SHEET_WRITE_RETRIES - 1:
                raise
            time.sleep(min(2 ** attempt, 20))
    raise RuntimeError(f"Google Sheets write failed after retries: {last_error}")


def get_client():
    info = dict(st.secrets["gcp_service_account"])
    private_key = str(info.get("private_key", ""))
    if "\\n" in private_key:
        private_key = private_key.replace("\\n", "\n")
    info["private_key"] = private_key.strip() + "\n"
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(credentials)


@st.cache_resource(show_spinner=False)
def get_client_cached():
    return get_client()


@st.cache_resource(show_spinner=False)
def get_worksheet_cached():
    """Open the worksheet once per Streamlit process/session lifetime."""
    gc = get_client_cached()
    sh = gc.open_by_key(st.secrets["GOOGLE_SHEET_ID"])
    try:
        ws = sh.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=SHEET_NAME, rows=2000, cols=len(HEADERS))

    # Ensure the existing worksheet grid is large enough for the current schema.
    # Older V9/V10 sheets may have only 19 columns (A:S); the V10 GPS fields
    # extend the schema to 22 columns (A:V). Without this resize, Google Sheets
    # rejects the schema migration with a grid-limit 400 error.
    required_cols = len(HEADERS)
    required_rows = max(getattr(ws, "row_count", 0), 2000)
    if getattr(ws, "col_count", 0) < required_cols or getattr(ws, "row_count", 0) < required_rows:
        _sheet_write(lambda: ws.resize(rows=required_rows, cols=max(getattr(ws, "col_count", 0), required_cols)))

    # IMPORTANT: only one small read on first initialization; never get_all_values().
    header = ws.row_values(1)
    if not header:
        _sheet_write(lambda: ws.update("A1:V1", [HEADERS], value_input_option="USER_ENTERED"))
    else:
        # Backward-compatible schema migration: preserve all existing data and only
        # append the new outlet-distance columns when upgrading an older Visits sheet.
        if len(header) < len(HEADERS):
            missing = HEADERS[len(header):]
            start_col = len(header) + 1
            def col_letter(n):
                out = ""
                while n:
                    n, rem = divmod(n - 1, 26)
                    out = chr(65 + rem) + out
                return out
            end_col = col_letter(len(HEADERS))
            start_col_letter = col_letter(start_col)
            _sheet_write(lambda: ws.update(f"{start_col_letter}1:{end_col}1", [missing], value_input_option="USER_ENTERED"))
    return ws


class VisitStore:
    """Thread-safe in-memory mirror of the Visits sheet.

    One Sheets read refreshes the whole mirror. Normal app reruns use the mirror.
    Successful IN/OUT writes update the mirror directly, avoiding a read-after-write.
    """
    def __init__(self):
        self.lock = threading.RLock()
        self.df = None
        self.last_refresh = 0.0
        self.version = 0
        self.open_by_employee = {}
        self.by_visit_id = {}

    def _rebuild_indexes(self):
        self.open_by_employee = {}
        self.by_visit_id = {}
        if self.df is None or self.df.empty:
            return
        today = _today()
        for _, row in self.df.iterrows():
            rec = row.to_dict()
            visit_id = str(rec.get("Visit ID", "")).strip()
            if visit_id:
                self.by_visit_id[visit_id] = rec
            if (str(rec.get("Date", "")) == today and
                    str(rec.get("Status", "")).strip() == "IN"):
                self.open_by_employee[str(rec.get("Employee Code", "")).strip()] = rec

    def _read_sheet(self):
        ws = get_worksheet_cached()
        last_error = None
        for attempt in range(4):
            try:
                records = ws.get_all_records(default_blank="")
                rows = []
                for i, record in enumerate(records, start=2):
                    row = {col: record.get(col, "") for col in HEADERS}
                    row[INTERNAL_ROW] = i
                    rows.append(row)
                df = pd.DataFrame(rows, columns=HEADERS + [INTERNAL_ROW])
                self.version += 1
                return df
            except APIError as exc:
                last_error = exc
                if "429" not in str(exc):
                    raise
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"Google Sheets is temporarily rate-limited. Please try again shortly. {last_error}")

    def refresh_if_needed(self, force=False):
        with self.lock:
            age = time.monotonic() - self.last_refresh
            if force or self.df is None or age >= REFRESH_SECONDS:
                self.df = self._read_sheet()
                self._rebuild_indexes()
                self.last_refresh = time.monotonic()
            # IMPORTANT: return the shared dataframe reference internally.
            # Creating a deep copy here on every employee Continue/IN/OUT caused
            # CPU/memory spikes under concurrent production traffic. Public
            # reporting functions make their own copy when needed.
            return self.df

    def force_refresh(self):
        return self.refresh_if_needed(force=True)

    def add_row(self, row, sheet_row):
        with self.lock:
            new_row = {col: row.get(col, "") for col in HEADERS}
            new_row[INTERNAL_ROW] = sheet_row
            if self.df is None:
                self.df = pd.DataFrame([new_row], columns=HEADERS + [INTERNAL_ROW])
            else:
                self.df = pd.concat([self.df, pd.DataFrame([new_row])], ignore_index=True)
            self.last_refresh = time.monotonic()
            self.version += 1
            self.by_visit_id[str(row.get("Visit ID", "")).strip()] = new_row.copy()
            if str(row.get("Date", "")) == _today() and str(row.get("Status", "")).strip() == "IN":
                self.open_by_employee[str(row.get("Employee Code", "")).strip()] = new_row.copy()

    def update_row(self, visit_id, updates):
        with self.lock:
            if self.df is None:
                return
            mask = self.df["Visit ID"].astype(str).str.strip() == str(visit_id).strip()
            for col, value in updates.items():
                if col in self.df.columns:
                    self.df.loc[mask, col] = value
            self.last_refresh = time.monotonic()
            self.version += 1
            key = str(visit_id).strip()
            rec = self.by_visit_id.get(key, {}).copy()
            rec.update(updates)
            if rec:
                self.by_visit_id[key] = rec
                emp = str(rec.get("Employee Code", "")).strip()
                if str(rec.get("Status", "")).strip() == "IN" and str(rec.get("Date", "")) == _today():
                    self.open_by_employee[emp] = rec.copy()
                else:
                    current = self.open_by_employee.get(emp)
                    if current and str(current.get("Visit ID", "")).strip() == key:
                        self.open_by_employee.pop(emp, None)


@st.cache_resource(show_spinner=False)
def get_visit_store_v101b():
    # Versioned cache key: guarantees a fresh VisitStore after deploying this
    # hotfix instead of reusing a class instance cached by an older V10.1 build.
    return VisitStore()


def get_visit_store():
    # Backward-compatible public name used elsewhere in the app.
    return get_visit_store_v101b()


def _ensure_visit_store_indexes(store):
    """Upgrade an older cached VisitStore object after an app redeploy.

    Streamlit cache_resource can keep the previous class instance alive across
    reruns/redeploys. V10.1 added open_by_employee/by_visit_id, so initialize
    and rebuild them when an older cached instance is encountered.
    """
    with store.lock:
        needs_rebuild = False
        if not hasattr(store, "open_by_employee"):
            store.open_by_employee = {}
            needs_rebuild = True
        if not hasattr(store, "by_visit_id"):
            store.by_visit_id = {}
            needs_rebuild = True
        if not hasattr(store, "version"):
            store.version = 0
        if needs_rebuild:
            # The method belongs to the newly deployed class. If the cached
            # object predates it, rebuild indexes directly from its dataframe.
            store.open_by_employee = {}
            store.by_visit_id = {}
            df = getattr(store, "df", None)
            if df is not None and not df.empty:
                today = _today()
                for _, row in df.iterrows():
                    rec = row.to_dict()
                    visit_id = str(rec.get("Visit ID", "")).strip()
                    if visit_id:
                        store.by_visit_id[visit_id] = rec
                    if (str(rec.get("Date", "")) == today and
                            str(rec.get("Status", "")).strip() == "IN"):
                        store.open_by_employee[str(rec.get("Employee Code", "")).strip()] = rec
    return store


def _visit_store():
    return _ensure_visit_store_indexes(get_visit_store())


def get_all_visits(force_refresh=False):
    df = _visit_store().refresh_if_needed(force=force_refresh)
    return df[HEADERS].copy()


def refresh_visits():
    return _visit_store().force_refresh()[HEADERS].copy()


def get_visit_store_version():
    """Return the in-process data version used to invalidate analytics caches."""
    return _visit_store().version


def _today():
    return datetime.now(ZoneInfo("Asia/Karachi")).strftime("%Y-%m-%d")


def _visit_id():
    stamp = datetime.now(ZoneInfo("Asia/Karachi")).strftime("%Y%m%d%H%M%S")
    return f"VIS-{stamp}-{uuid.uuid4().hex[:6].upper()}"


def find_open_visit(employee_code):
    store = _visit_store()
    # One shared refresh at most every 15 minutes. All successful IN/OUT writes
    # update the process-wide index immediately, so normal employee lookups are O(1).
    store.refresh_if_needed()
    with store.lock:
        rec = store.open_by_employee.get(str(employee_code).strip())
        return None if rec is None else rec.copy()


def create_in_visit(employee_code, employee_name, outlet_code, outlet_name, location, outlet_latitude=None, outlet_longitude=None):
    store = _visit_store()
    with store.lock:
        df = store.refresh_if_needed()
        if str(employee_code).strip() in store.open_by_employee:
            raise ValueError("An open visit already exists for this employee.")

        ws = get_worksheet_cached()
        now = datetime.now(ZoneInfo("Asia/Karachi"))
        visit_id = _visit_id()
        outlet_distance = ""
        if outlet_latitude is not None and outlet_longitude is not None:
            outlet_distance = round(_haversine_m(location["latitude"], location["longitude"], outlet_latitude, outlet_longitude), 1)
        outlet_status = "Mismatch > 1.5 km" if outlet_distance != "" and outlet_distance > 1500 else ("Within 1.5 km" if outlet_distance != "" else "")
        row = {
            "Visit ID": visit_id,
            "Date": now.strftime("%Y-%m-%d"),
            "Employee Code": employee_code,
            "Employee Name": employee_name,
            "Outlet Code": outlet_code,
            "Outlet Name": outlet_name,
            "IN Date/Time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "IN Latitude": location["latitude"],
            "IN Longitude": location["longitude"],
            "IN GPS Accuracy": location.get("accuracy", ""),
            "OUT Date/Time": "", "OUT Latitude": "", "OUT Longitude": "", "OUT GPS Accuracy": "",
            "IN-OUT GPS Distance (m)": "", "GPS Status": "", "Time Spent": "", "Time Spent Hours": "", "Status": "IN",
            "IN-Outlet GPS Distance (m)": outlet_distance, "OUT-Outlet GPS Distance (m)": "", "Outlet GPS Status": outlet_status,
        }
        values = [row[h] for h in HEADERS]
        response = _sheet_write(lambda: ws.append_row(
            values,
            value_input_option="USER_ENTERED",
            insert_data_option="INSERT_ROWS",
            include_values_in_response=True,
        ))
        sheet_row = None
        try:
            updated_range = response.get("updates", {}).get("updatedRange", "")
            import re
            match = re.search(r"!(?:[A-Z]+)(\d+):", str(updated_range))
            if match:
                sheet_row = int(match.group(1))
        except Exception:
            sheet_row = None
        if sheet_row is None:
            sheet_row = max(2, len(df) + 2)
        store.add_row(row, sheet_row)
        return visit_id


def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1 = math.radians(float(lat1)); p2 = math.radians(float(lat2))
    dp = math.radians(float(lat2) - float(lat1)); dl = math.radians(float(lon2) - float(lon1))
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def complete_out_visit(visit_id, location, outlet_latitude=None, outlet_longitude=None, tolerance_meters=100, warning_meters=200):
    store = _visit_store()
    store.refresh_if_needed()
    key = str(visit_id).strip()
    with store.lock:
        row = store.by_visit_id.get(key)
    if row is None:
        # One forced refresh only when the shared index does not know this visit.
        store.force_refresh()
        with store.lock:
            row = store.by_visit_id.get(key)
    if row is None:
        raise ValueError("Visit ID was not found.")
    row = row.copy()
    if str(row.get("Status", "")).strip() != "IN":
        raise ValueError("This visit is already completed or invalid.")

    row_num = int(row[INTERNAL_ROW])
    distance = _haversine_m(row["IN Latitude"], row["IN Longitude"], location["latitude"], location["longitude"])
    out_outlet_distance = ""
    if outlet_latitude is not None and outlet_longitude is not None:
        out_outlet_distance = round(_haversine_m(location["latitude"], location["longitude"], outlet_latitude, outlet_longitude), 1)
    in_outlet_distance = row.get("IN-Outlet GPS Distance (m)", "")
    outlet_status = "Mismatch > 1.5 km" if (out_outlet_distance != "" and out_outlet_distance > 1500) or (str(in_outlet_distance).strip() not in {"", "nan"} and float(in_outlet_distance) > 1500) else ("Within 1.5 km" if out_outlet_distance != "" else str(row.get("Outlet GPS Status", "")))
    if distance <= tolerance_meters:
        gps_status = "Almost Same"
    elif distance <= warning_meters:
        gps_status = "Some Difference"
    else:
        gps_status = "Big Difference"

    in_time = datetime.strptime(str(row["IN Date/Time"]), "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Karachi"))
    out_time = datetime.now(ZoneInfo("Asia/Karachi"))
    seconds = max(0, int((out_time - in_time).total_seconds()))
    hours = seconds / 3600
    time_spent = f"{seconds//3600:02d}:{(seconds%3600)//60:02d}:{seconds%60:02d}"

    updates = {
        "OUT Date/Time": out_time.strftime("%Y-%m-%d %H:%M:%S"),
        "OUT Latitude": location["latitude"],
        "OUT Longitude": location["longitude"],
        "OUT GPS Accuracy": location.get("accuracy", ""),
        "IN-OUT GPS Distance (m)": round(distance, 1),
        "GPS Status": gps_status,
        "Time Spent": time_spent,
        "Time Spent Hours": round(hours, 4),
        "Status": "Completed",
        "OUT-Outlet GPS Distance (m)": out_outlet_distance,
        "Outlet GPS Status": outlet_status,
    }
    values = [updates.get(h, row.get(h, "")) for h in HEADERS[10:]]
    ws = get_worksheet_cached()
    _sheet_write(lambda: ws.update(f"K{row_num}:V{row_num}", [values], value_input_option="USER_ENTERED"))
    store.update_row(visit_id, updates)

    row.update(updates)
    return {h: row.get(h, "") for h in HEADERS}
