from io import BytesIO
import threading
import time
import pandas as pd
import streamlit as st

from sheets_handler import get_client_cached, _sheet_write

OUTLET_SHEET = "Outlets"
OUTLET_HEADERS = ["Outlet Code", "Outlet Name", "Latitude", "Longitude", "Active"]
MASTER_REFRESH_SECONDS = 1800  # production: master changes rarely; admin upload updates cache immediately

def _clean_code(value):
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text

def _clean_active(value):
    if pd.isna(value) or str(value).strip() == "":
        return True
    return str(value).strip().lower() not in {"false", "0", "no", "inactive", "n"}

def _get_spreadsheet():
    gc = get_client_cached()
    return gc.open_by_key(st.secrets["GOOGLE_SHEET_ID"])

@st.cache_resource(show_spinner=False)
def get_master_store():
    return MasterStore()

class MasterStore:
    def __init__(self):
        self.lock = threading.RLock()
        self.outlets = None
        self.last_refresh = 0.0
        self.version = 0
        self.outlet_index = {}

    def _worksheet(self, title=OUTLET_SHEET, headers=OUTLET_HEADERS, rows=2000):
        sh = _get_spreadsheet()
        try:
            ws = sh.worksheet(title)
        except Exception:
            ws = _sheet_write(lambda: sh.add_worksheet(title=title, rows=max(rows, 100), cols=len(headers)))
        header = ws.row_values(1)
        if not header:
            _sheet_write(lambda: ws.update("A1:E1", [headers], value_input_option="USER_ENTERED"))
        return ws

    def refresh_if_needed(self, force=False):
        with self.lock:
            if force or self.outlets is None or time.monotonic() - self.last_refresh >= MASTER_REFRESH_SECONDS:
                ow = self._worksheet()
                records = ow.get_all_records(default_blank="")
                self.outlets = self._normalize_outlets(pd.DataFrame(records, columns=OUTLET_HEADERS))
                self.outlet_index = {
                    str(row["Outlet Code"]): row.to_dict()
                    for _, row in self.outlets[self.outlets["Active"] == True].iterrows()
                }
                self.last_refresh = time.monotonic()
                self.version += 1
            return self.outlets.copy(deep=True)

    @staticmethod
    def _normalize_outlets(df):
        if df.empty:
            return pd.DataFrame(columns=OUTLET_HEADERS)
        x = df.copy()
        x["Outlet Code"] = x["Outlet Code"].map(_clean_code)
        x["Outlet Name"] = x["Outlet Name"].fillna("").astype(str).str.strip()
        x["Latitude"] = pd.to_numeric(x["Latitude"], errors="coerce")
        x["Longitude"] = pd.to_numeric(x["Longitude"], errors="coerce")
        x["Active"] = x["Active"].map(_clean_active)
        return x[OUTLET_HEADERS].drop_duplicates(subset=["Outlet Code"], keep="last")

    def replace_outlets(self, df):
        clean = self._normalize_outlets(df)
        self._validate_outlets(clean)
        ws = self._worksheet(rows=max(2000, len(clean) + 10))
        rows = [OUTLET_HEADERS] + clean.astype(object).where(pd.notna(clean), "").values.tolist()
        _sheet_write(lambda: ws.clear())
        _sheet_write(lambda: ws.update(f"A1:E{len(rows)}", rows, value_input_option="USER_ENTERED"))
        with self.lock:
            self.outlets = clean
            self.outlet_index = {
                str(row["Outlet Code"]): row.to_dict()
                for _, row in clean[clean["Active"] == True].iterrows()
            }
            self.last_refresh = time.monotonic()
            self.version += 1
        return clean

    @staticmethod
    def _validate_outlets(df):
        errors = []
        if df["Outlet Code"].eq("").any(): errors.append("Outlet Code contains blank values.")
        if (~df["Outlet Code"].str.fullmatch(r"\d+").fillna(False)).any(): errors.append("Outlet Code must contain numbers only.")
        if df["Outlet Name"].astype(str).str.strip().eq("").any(): errors.append("Outlet Name contains blank values.")
        if df["Latitude"].isna().any() or df["Longitude"].isna().any(): errors.append("Every outlet must have valid Latitude and Longitude.")
        if ((df["Latitude"] < -90) | (df["Latitude"] > 90)).any(): errors.append("Latitude must be between -90 and 90.")
        if ((df["Longitude"] < -180) | (df["Longitude"] > 180)).any(): errors.append("Longitude must be between -180 and 180.")
        if df["Outlet Code"].duplicated().any(): errors.append("Duplicate Outlet Code found.")
        if errors: raise ValueError(" ".join(errors))

def _ensure_store_compat(store):
    """Make cached MasterStore objects from an older hot-reloaded app version compatible.

    Streamlit cache_resource can keep the previous class instance alive across a code
    redeploy. Older instances did not have outlet_index, so initialize/rebuild it here
    without requiring a manual cache clear or reboot.
    """
    if not hasattr(store, "outlet_index"):
        store.outlet_index = {}
    if not hasattr(store, "version"):
        store.version = 0
    if not hasattr(store, "last_refresh"):
        store.last_refresh = 0.0
    if not hasattr(store, "lock"):
        store.lock = threading.RLock()
    if not hasattr(store, "outlets"):
        store.outlets = None

    if store.outlets is not None and not store.outlet_index:
        try:
            clean = MasterStore._normalize_outlets(store.outlets)
            store.outlets = clean
            store.outlet_index = {
                str(row["Outlet Code"]): row.to_dict()
                for _, row in clean[clean["Active"] == True].iterrows()
            }
        except Exception:
            # Force one clean refresh from the Outlet Master if an old cached shape
            # cannot be migrated safely.
            store.outlets = None
            store.last_refresh = 0.0
    return store

def get_master_data(force=False):
    store = _ensure_store_compat(get_master_store())
    return store.refresh_if_needed(force=force)

def lookup_outlet(code):
    # Refresh only when the shared master cache is stale, then perform O(1) lookup.
    store = _ensure_store_compat(get_master_store())
    store.refresh_if_needed()
    return store.outlet_index.get(_clean_code(code))

def master_status():
    store = _ensure_store_compat(get_master_store())
    store.refresh_if_needed()
    outlets = store.outlets
    return {
        "outlets": len(outlets),
        "active_outlets": len(store.outlet_index),
        "version": store.version,
    }

def template_excel(kind="outlets"):
    df = pd.DataFrame([{
        "Outlet Code": "1001",
        "Outlet Name": "PSO - Example II & Co.",
        "Latitude": 24.8607,
        "Longitude": 67.0011,
        "Active": True,
    }])
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Outlets")
    return bio.getvalue()
