from datetime import datetime
from zoneinfo import ZoneInfo
import os
import re
import json
import base64
import pandas as pd
import streamlit as st

from master_data import get_master_data, lookup_outlet, master_status, template_excel

from sheets_handler import (
    get_all_visits,
    find_open_visit,
    create_in_visit,
    complete_out_visit,
    get_visit_store_version,
)

APP_TITLE = "Customer Care Day"
GPS_TOLERANCE_METERS = 100
GPS_WARNING_METERS = 200
MAX_ACCEPTABLE_GPS_ACCURACY_METERS = 1000
OUTLET_GPS_MISMATCH_METERS = 1500

st.set_page_config(page_title=APP_TITLE, page_icon="💚", layout="centered")

# Mobile/PWA-style metadata. Streamlit is still the hosted web application,
# but supported mobile browsers can add it to the home screen as
# "Customer Care Day" with a standalone app-like appearance.
PWA_ICON = os.path.join(os.path.dirname(__file__), "assets", "customer_care_day_icon.png")

@st.cache_data(show_spinner=False, max_entries=1)
def _pwa_payload():
    if not os.path.exists(PWA_ICON):
        return None, None
    with open(PWA_ICON, "rb") as _icon_file:
        icon_b64 = base64.b64encode(_icon_file.read()).decode("ascii")
    icon_data = f"data:image/png;base64,{icon_b64}"
    manifest = {
            "name": "Customer Care Day",
            "short_name": "Customer Care Day",
            "start_url": "https://csdattendance.streamlit.app/",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#063b91",
            "theme_color": "#063b91",
            "icons": [
                {"src": icon_data, "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
            ],
        }
    manifest_b64 = base64.b64encode(json.dumps(manifest).encode()).decode("ascii")
    return icon_data, manifest_b64

try:
    _icon_data, _manifest_b64 = _pwa_payload()
    if _icon_data and _manifest_b64:
        st.html(
            f"""
            <script>
            (() => {{
                // App-style metadata. This is intentionally injected at runtime because
                // Streamlit serves the application shell itself. Supported browsers can
                // use these settings for Add to Home Screen / installed-app display.
                const manifest = document.createElement('link');
                manifest.rel = 'manifest';
                manifest.href = 'data:application/manifest+json;base64,{_manifest_b64}';
                document.head.appendChild(manifest);

                const metas = [
                    ['mobile-web-app-capable', 'yes'],
                    ['apple-mobile-web-app-capable', 'yes'],
                    ['apple-mobile-web-app-status-bar-style', 'black-translucent'],
                    ['apple-mobile-web-app-title', 'Customer Care Day'],
                    ['theme-color', '#063b91']
                ];
                metas.forEach(([name, content]) => {{
                    if (!document.head.querySelector(`meta[name=\"${{name}}\"]`)) {{
                        const m = document.createElement('meta');
                        m.name = name;
                        m.content = content;
                        document.head.appendChild(m);
                    }}
                }});

                // Hide the browser-style Streamlit chrome as much as possible when the
                // site is launched in standalone/installed mode.
                const style = document.createElement('style');
                style.textContent = `
                    @media (display-mode: standalone) {{
                        [data-testid=\"stHeader\"] {{ min-height: 0 !important; }}
                        [data-testid=\"stToolbar\"] {{ display: none !important; }}
                    }}
                `;
                document.head.appendChild(style);
            }})();
            </script>
            """,
            unsafe_allow_javascript=True,
        )
except Exception:
    pass

# PSO / We Care. You Drive campaign theme
st.markdown("""
<style>
    :root {
        --ccd-blue: #063b91;
        --ccd-blue-2: #0758bd;
        --ccd-green: #12a84a;
        --ccd-green-dark: #087b3b;
        --ccd-yellow: #ffd21c;
        --ccd-light: #f3f8ff;
        --ccd-border: #c9def8;
        --ccd-text: #083b83;
    }
    .stApp {
        background: linear-gradient(180deg, #eef6ff 0%, #ffffff 52%, #eef7ff 100%);
    }
    [data-testid="stHeader"] {
        background: transparent;
    }
    [data-testid="stMainBlockContainer"] {
        max-width: 760px;
        padding-top: 1rem;
        padding-bottom: 2rem;
    }
    .ccd-hero {
        border-radius: 0 0 22px 22px;
        overflow: hidden;
        box-shadow: 0 8px 24px rgba(6,59,145,.16);
        margin-bottom: 12px;
    }
    .ccd-title {
        background: linear-gradient(135deg, var(--ccd-blue), #064aa5);
        color: white;
        border-radius: 16px;
        padding: 16px 18px;
        margin: 8px 0 14px;
        box-shadow: 0 6px 18px rgba(6,59,145,.14);
    }
    .ccd-title h1 {
        margin: 0;
        font-size: 1.55rem;
        font-weight: 800;
        letter-spacing: .2px;
    }
    .ccd-title p {
        margin: 5px 0 0;
        opacity: .95;
        font-size: .94rem;
    }
    .ccd-section {
        color: var(--ccd-text);
        font-weight: 800;
        font-size: 1.2rem;
        margin: 12px 0 8px;
    }
    .ccd-info {
        background: linear-gradient(135deg, #eef8ff, #ffffff);
        border: 1px solid var(--ccd-border);
        border-left: 5px solid var(--ccd-green);
        border-radius: 12px;
        padding: 11px 13px;
        color: #174d91;
        margin: 8px 0 12px;
    }
    .ccd-step {
        background: white;
        border: 1px solid var(--ccd-border);
        border-radius: 18px;
        padding: 16px 15px 12px;
        margin: 12px 0;
        box-shadow: 0 5px 18px rgba(6,59,145,.09);
    }
    .ccd-step h3 {
        color: var(--ccd-text);
        margin: 0 0 6px;
        font-size: 1.18rem;
    }
    .ccd-step p {
        color: #315a91;
        margin: 3px 0;
    }
    .ccd-summary {
        background: white;
        border: 1px solid var(--ccd-border);
        border-radius: 16px;
        overflow: hidden;
        box-shadow: 0 6px 20px rgba(6,59,145,.10);
    }
    .ccd-summary-head {
        background: linear-gradient(90deg, var(--ccd-blue), var(--ccd-blue-2));
        color: white;
        padding: 11px 14px;
        font-weight: 800;
        font-size: 1.05rem;
    }
    .ccd-progress {
        display:flex;
        align-items:center;
        gap:0;
        margin: 8px 2px 15px;
    }
    .ccd-dot {
        width:30px; height:30px; border-radius:50%;
        display:flex; align-items:center; justify-content:center;
        font-weight:800; font-size:.82rem;
        background:#e8f1fc; color:var(--ccd-text);
        border:2px solid #c6ddf7;
        flex:0 0 30px;
    }
    .ccd-dot.active, .ccd-dot.done {
        background:var(--ccd-green); color:white; border-color:var(--ccd-green);
    }
    .ccd-line { height:4px; background:#dce9f8; flex:1; }
    .ccd-line.done { background:var(--ccd-green); }
    div[data-testid="stForm"] {
        background: white;
        border: 1px solid var(--ccd-border);
        border-radius: 18px;
        padding: 18px 16px 8px;
        box-shadow: 0 5px 18px rgba(6,59,145,.08);
    }
    div[data-testid="stTextInput"] label {
        color: var(--ccd-text);
        font-weight: 700;
    }
    div.stButton > button, div[data-testid="stFormSubmitButton"] > button {
        border-radius: 11px;
        min-height: 48px;
        font-weight: 800;
        border: 0;
    }
    div[data-testid="stFormSubmitButton"] > button {
        background: linear-gradient(135deg, var(--ccd-green), var(--ccd-green-dark));
        color: white;
    }
    div.stButton > button[kind="primary"] {
        background: linear-gradient(135deg, var(--ccd-green), var(--ccd-green-dark));
        color: white;
    }
    .ccd-footer {
        text-align:center;
        color:#42648f;
        font-size:.78rem;
        margin-top:18px;
        padding:10px;
    }
    @media (max-width: 640px) {
        [data-testid="stMainBlockContainer"] { padding-left: .75rem; padding-right: .75rem; }
        .ccd-title h1 { font-size: 1.35rem; }
        .ccd-hero { border-radius: 0 0 18px 18px; }
    }
</style>
""", unsafe_allow_html=True)

HERO_IMAGE = os.path.join(os.path.dirname(__file__), "assets", "customer_care_day_banner.jpg")


def show_brand_header(show_title=True):
    if os.path.exists(HERO_IMAGE):
        st.markdown('<div class="ccd-hero">', unsafe_allow_html=True)
        st.image(HERO_IMAGE, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)
    if show_title:
        st.markdown('<div class="ccd-title"><h1>Customer Care Day</h1><p>WE CARE. YOU DRIVE.</p></div>', unsafe_allow_html=True)


def show_install_help():
    # Compact installation instructions. The actual install prompt is browser-controlled.
    st.html(
        """
        <div class="ccd-info" id="ccd-install-card">
          <strong>📱 Install Customer Care Day</strong><br>
          <span id="ccd-install-text">Use your browser menu and choose <b>Add to Home Screen</b> or <b>Install app</b>.</span>
          <button id="ccd-install-btn" style="display:none;margin-top:10px;width:100%;padding:11px;border:0;border-radius:10px;background:#12a84a;color:#fff;font-weight:800;font-size:15px;">Install Customer Care Day</button>
          <div id="ccd-ios-help" style="display:none;margin-top:8px;font-size:13px;">On iPhone/iPad: open this page in Safari → <b>Share</b> → <b>Add to Home Screen</b> → <b>Add</b>.</div>
          <div id="ccd-android-help" style="display:none;margin-top:8px;font-size:13px;">On Android: open in Chrome → <b>⋮</b> → <b>Install app</b> or <b>Add to Home screen</b>.</div>
        </div>
        <script>
        (() => {
          let deferredPrompt = null;
          const btn = document.getElementById('ccd-install-btn');
          const text = document.getElementById('ccd-install-text');
          const ios = document.getElementById('ccd-ios-help');
          const android = document.getElementById('ccd-android-help');
          const standalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
          const ua = navigator.userAgent || '';
          const isiOS = /iPhone|iPad|iPod/i.test(ua);
          const isAndroid = /Android/i.test(ua);
          if (standalone) {
            text.innerHTML = '<b>Customer Care Day is installed.</b> Open it from your home screen.';
          } else if (isiOS) {
            ios.style.display = 'block';
            text.textContent = 'Install it once so you can open Customer Care Day from your home screen.';
          } else if (isAndroid) {
            android.style.display = 'block';
          }
          window.addEventListener('beforeinstallprompt', (event) => {
            event.preventDefault();
            deferredPrompt = event;
            btn.style.display = 'block';
          });
          btn.addEventListener('click', async () => {
            if (!deferredPrompt) return;
            deferredPrompt.prompt();
            await deferredPrompt.userChoice;
            deferredPrompt = null;
            btn.style.display = 'none';
          });
          window.addEventListener('appinstalled', () => {
            btn.style.display = 'none';
            text.innerHTML = '<b>Customer Care Day is installed.</b> Open it from your home screen.';
          });
        })();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


def show_progress(active_step):
    parts = []
    for i in range(1, 5):
        cls = "done" if i < active_step else ("active" if i == active_step else "")
        parts.append(f'<div class="ccd-dot {cls}">{i}</div>')
        if i < 4:
            line_cls = "done" if i < active_step else ""
            parts.append(f'<div class="ccd-line {line_cls}"></div>')
    st.markdown('<div class="ccd-progress">' + ''.join(parts) + '</div>', unsafe_allow_html=True)


for key, default in {
    "employee_code": "",
    "employee_name": "",
    "outlet_code": "",
    "outlet_name": "",
    "in_location": None,
    "out_location": None,
    "admin_logged_in": False,
    "last_message": "",
    "open_visit": None,
    "details_loaded": False,
    "scroll_target": None,
    "completed_visit": None,
    "outlet_latitude": None,
    "outlet_longitude": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


def now_local():
    return datetime.now(ZoneInfo("Asia/Karachi"))


def auto_scroll_if_needed(anchor_id, delay_ms=180):
    """Scroll once to an anchor requested by the previous user action."""
    if st.session_state.get("scroll_target") != anchor_id:
        return
    safe_id = str(anchor_id).replace("\\", "").replace("'", "\\'")
    st.session_state["scroll_target"] = None
    st.html(
        f"""
        <div id=\"{safe_id}\" style=\"height:1px;margin:0;padding:0;\"></div>
        <script>
        (() => {{
            const id = {safe_id!r};
            setTimeout(() => {{
                const el = document.getElementById(id);
                if (el) el.scrollIntoView({{behavior: 'smooth', block: 'start'}});
            }}, {int(delay_ms)});
        }})();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


# Streamlit 1.52+ Components V2 lets the browser geolocation API run directly
# in the app page instead of inside the old iframe-based component.
GPS_COMPONENT = st.components.v2.component(
    name="petrol_station_gps",
    html="""
        <div class="gps-box">
            <button id="gps-button" type="button">📍 Get Current GPS</button>
            <div id="gps-status" class="gps-status">Ready to capture location.</div>
        </div>
    """,
    css="""
        .gps-box { width: 100%; font-family: var(--st-font); }
        #gps-button {
            width: 100%;
            border: 1px solid var(--st-primary-color);
            border-radius: 0.5rem;
            padding: 0.65rem 0.75rem;
            background: linear-gradient(135deg, var(--ccd-blue), var(--ccd-blue-2));
            color: white;
            font-size: 1rem;
            cursor: pointer;
        }
        #gps-button:disabled { opacity: 0.65; cursor: wait; }
        .gps-status { margin-top: 0.4rem; font-size: 0.9rem; color: var(--st-text-color); }
    """,
    js="""
        export default function({ parentElement, setStateValue }) {
            const button = parentElement.querySelector('#gps-button');
            const status = parentElement.querySelector('#gps-status');

            if (!button || button.dataset.bound === '1') return;
            button.dataset.bound = '1';

            // Keep the browser watch attached to the DOM element so Streamlit
            // reruns do not accidentally start multiple GPS watches.
            parentElement._gpsWatchId = null;
            parentElement._gpsTimer = null;
            parentElement._bestAccuracy = Infinity;
            parentElement._bestLocation = null;

            const stopWatch = () => {
                if (parentElement._gpsWatchId !== null) {
                    navigator.geolocation.clearWatch(parentElement._gpsWatchId);
                    parentElement._gpsWatchId = null;
                }
                if (parentElement._gpsTimer !== null) {
                    clearTimeout(parentElement._gpsTimer);
                    parentElement._gpsTimer = null;
                }
                button.disabled = false;
            };

            const publish = (position, final = false) => {
                const c = position.coords;
                const accuracy = Number(c.accuracy);
                if (!Number.isFinite(c.latitude) || !Number.isFinite(c.longitude)) return;

                // Keep the most accurate reading received during the watch.
                if (Number.isFinite(accuracy) && accuracy < parentElement._bestAccuracy) {
                    parentElement._bestAccuracy = accuracy;
                    parentElement._bestLocation = {
                        latitude: c.latitude,
                        longitude: c.longitude,
                        accuracy: accuracy,
                        altitude: c.altitude,
                        altitudeAccuracy: c.altitudeAccuracy,
                        heading: c.heading,
                        speed: c.speed,
                        timestamp: position.timestamp
                    };
                    setStateValue('location', parentElement._bestLocation);
                    setStateValue('error', null);
                    setStateValue('requesting', true);
                    status.textContent = `Best GPS accuracy so far: ${Math.round(accuracy)} m. Waiting for a better fix...`;

                    // A very good fix has been obtained; no need to wait longer.
                    if (accuracy <= 50) {
                        status.textContent = `GPS captured — accuracy about ${Math.round(accuracy)} m.`;
                        setStateValue('requesting', false);
                        stopWatch();
                    }
                }

                if (final) {
                    const best = parentElement._bestLocation;
                    if (best) {
                        status.textContent = `GPS captured — best accuracy about ${Math.round(best.accuracy)} m.`;
                        setStateValue('location', best);
                        setStateValue('requesting', false);
                    }
                    stopWatch();
                }
            };

            button.onclick = () => {
                if (!window.isSecureContext) {
                    const message = 'Location requires a secure HTTPS connection.';
                    status.textContent = message;
                    setStateValue('error', message);
                    return;
                }

                if (!navigator.geolocation) {
                    const message = 'This browser does not provide geolocation.';
                    status.textContent = message;
                    setStateValue('error', message);
                    return;
                }

                stopWatch();
                parentElement._bestAccuracy = Infinity;
                parentElement._bestLocation = null;
                button.disabled = true;
                status.textContent = 'Searching for the best GPS fix... Keep the phone still and wait up to 30 seconds.';
                setStateValue('location', null);
                setStateValue('error', null);
                setStateValue('requesting', true);

                const options = {
                    enableHighAccuracy: true,
                    maximumAge: 0,
                    timeout: 30000
                };

                parentElement._gpsWatchId = navigator.geolocation.watchPosition(
                    (position) => publish(position, false),
                    (error) => {
                        const best = parentElement._bestLocation;
                        if (best) {
                            status.textContent = `GPS captured — best accuracy about ${Math.round(best.accuracy)} m.`;
                            setStateValue('location', best);
                            setStateValue('error', null);
                            setStateValue('requesting', false);
                            stopWatch();
                            return;
                        }
                        let message = error.message || 'Unable to obtain location.';
                        if (error.code === 1) message = 'Location permission was denied by the browser.';
                        if (error.code === 2) message = 'Location is currently unavailable. Please ensure phone Location/GPS is ON.';
                        if (error.code === 3) message = 'Location request timed out. Please try again.';
                        status.textContent = message;
                        setStateValue('error', `GPS error ${error.code}: ${message}`);
                        setStateValue('requesting', false);
                        stopWatch();
                    },
                    options
                );

                // Do not reject a valid coordinate merely because Android reports
                // a large accuracy radius. Some devices initially report 1000–3000m
                // and improve after several seconds. If it does not improve, the
                // best available coordinate is still returned to the app.
                parentElement._gpsTimer = setTimeout(() => {
                    if (parentElement._bestLocation) {
                        publish({coords: parentElement._bestLocation, timestamp: parentElement._bestLocation.timestamp}, true);
                    } else {
                        status.textContent = 'No GPS coordinate received. Please try again with Location/GPS ON.';
                        setStateValue('error', 'No GPS coordinate was received.');
                        setStateValue('requesting', false);
                        stopWatch();
                    }
                }, 30000);
            };
        }
    """,
)


def _haversine_m(lat1, lon1, lat2, lon2):
    import math
    R = 6371000.0
    p1 = math.radians(float(lat1)); p2 = math.radians(float(lat2))
    dp = math.radians(float(lat2) - float(lat1)); dl = math.radians(float(lon2) - float(lon1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_location(component_key, label, outlet_latitude=None, outlet_longitude=None):
    """Capture GPS using the browser's native Geolocation API."""
    st.caption("Turn ON phone Location/GPS and allow location permission.")

    result = GPS_COMPONENT(
        key=component_key,
        default={"location": None, "error": None, "requesting": False},
        on_location_change=lambda: None,
        on_error_change=lambda: None,
        on_requesting_change=lambda: None,
    )

    location = getattr(result, "location", None)
    error = getattr(result, "error", None)

    if error:
        st.error(error)
        return None

    if location and location.get("latitude") is not None:
        captured = {
            "latitude": float(location["latitude"]),
            "longitude": float(location["longitude"]),
            "accuracy": float(location["accuracy"]) if location.get("accuracy") is not None else None,
        }
        st.success(f"{label} GPS location captured.")
        st.write(f"Latitude: `{captured['latitude']:.6f}`")
        st.write(f"Longitude: `{captured['longitude']:.6f}`")
        if captured["accuracy"] is not None:
            st.write(f"Accuracy: approximately `{captured['accuracy']:.0f} metres`")
        if outlet_latitude is not None and outlet_longitude is not None:
            mismatch_distance = _haversine_m(captured["latitude"], captured["longitude"], float(outlet_latitude), float(outlet_longitude))
            captured["outlet_distance"] = round(mismatch_distance, 1)
            if mismatch_distance > OUTLET_GPS_MISMATCH_METERS:
                st.warning(f"⚠ Your location mismatches outlet location. Difference from outlet location: {mismatch_distance/1000:.2f} km.")
            else:
                st.success(f"✓ Your location is within {mismatch_distance/1000:.2f} km of the outlet master location.")
        return captured

    return None


def location_ok(location):
    if not location:
        return False, "Please capture your current GPS location."
    lat = location.get("latitude")
    lon = location.get("longitude")
    accuracy = location.get("accuracy")
    if lat is None or lon is None:
        return False, "GPS coordinates were not received."
    # Do not block attendance because the browser reports a large accuracy radius.
    # Android/browser location may initially report a coarse fix (e.g. 1000–3000 m)
    # even when the coordinate itself is usable. The actual reported accuracy is
    # stored in the sheet for audit purposes.
    return True, ""


def validate_details(employee_code, employee_name, outlet_code, outlet_name):
    """Validate required fields and the requested character types."""
    errors = []
    if not employee_code:
        errors.append("Employee Code is mandatory.")
    elif not re.fullmatch(r"\d+", employee_code):
        errors.append("Employee Code must contain numbers only.")

    if not employee_name:
        errors.append("Employee Name is mandatory.")
    elif not re.fullmatch(r"[A-Za-z ]+", employee_name):
        errors.append("Employee Name must contain alphabets and spaces only.")

    if not outlet_code:
        errors.append("Outlet Code is mandatory.")
    elif not re.fullmatch(r"\d+", outlet_code):
        errors.append("Outlet Code must contain numbers only.")

    return errors


def validate_master_ready(employee_code, employee_name, outlet_code):
    errors = []
    outlet = lookup_outlet(outlet_code)
    if not employee_code or not re.fullmatch(r"\d+", employee_code):
        errors.append("Employee Code must contain numbers only.")
    if not employee_name:
        errors.append("Employee Name is mandatory.")
    elif not re.fullmatch(r"[A-Za-z ]+", employee_name):
        errors.append("Employee Name must contain alphabets and spaces only.")
    if outlet is None:
        errors.append("Outlet Code was not found or is inactive in Outlet Master.")
    return errors, outlet


def _safe_float_series(df, column):
    if column not in df.columns:
        return pd.Series(index=df.index, dtype="float64")
    return pd.to_numeric(df[column], errors="coerce")


def build_admin_summary(df):
    """Return lightweight management KPIs and exception counts from the live mirror."""
    if df is None or df.empty:
        return {
            "total": 0, "completed": 0, "pending": 0,
            "almost_same": 0, "some_difference": 0, "big_difference": 0,
            "short_visits": 0, "long_visits": 0, "gps_missing": 0,
        }
    status = df["Status"].astype(str).str.strip()
    gps = df["GPS Status"].astype(str).str.strip()
    hours = _safe_float_series(df, "Time Spent Hours")
    accuracy_in = _safe_float_series(df, "IN GPS Accuracy")
    accuracy_out = _safe_float_series(df, "OUT GPS Accuracy")
    return {
        "total": len(df),
        "completed": int((status == "Completed").sum()),
        "pending": int((status == "IN").sum()),
        "almost_same": int((gps == "Almost Same").sum()),
        "some_difference": int((gps == "Some Difference").sum()),
        "big_difference": int((gps == "Big Difference").sum()),
        "short_visits": int(((status == "Completed") & (hours < 1/60)).sum()),
        "long_visits": int(((status == "Completed") & (hours > 8)).sum()),
        "gps_missing": int((accuracy_in.isna() | accuracy_out.isna()).sum()),
    }


def build_admin_exceptions(df):
    """Create an admin-only exception table; it does not alter stored visit data."""
    if df is None or df.empty:
        return pd.DataFrame()
    x = df.copy()
    status = x["Status"].astype(str).str.strip()
    gps = x["GPS Status"].astype(str).str.strip()
    hours = _safe_float_series(x, "Time Spent Hours")
    in_acc = _safe_float_series(x, "IN GPS Accuracy")
    out_acc = _safe_float_series(x, "OUT GPS Accuracy")
    reasons = []
    for i in x.index:
        r = []
        if gps.loc[i] == "Big Difference":
            r.append("Big GPS Difference")
        if status.loc[i] == "Completed" and pd.notna(hours.loc[i]) and hours.loc[i] < 1/60:
            r.append("Very Short Visit")
        if status.loc[i] == "Completed" and pd.notna(hours.loc[i]) and hours.loc[i] > 8:
            r.append("Very Long Visit")
        if pd.isna(in_acc.loc[i]) or pd.isna(out_acc.loc[i]):
            r.append("Missing GPS Accuracy")
        reasons.append(", ".join(r))
    x["Exception"] = reasons
    x = x[x["Exception"] != ""].copy()
    cols = ["Visit ID", "Date", "Employee Code", "Employee Name", "Outlet Code", "Outlet Name",
            "IN Date/Time", "OUT Date/Time", "IN-OUT GPS Distance (m)", "GPS Status",
            "Time Spent", "Status", "Exception"]
    return x[[c for c in cols if c in x.columns]]


def _filter_date(df, selected_date):
    if df is None or df.empty:
        return pd.DataFrame(columns=df.columns if df is not None else [])
    x = df.copy()
    x["Date"] = x["Date"].astype(str)
    return x[x["Date"] == str(selected_date)].copy()


def build_management_analysis(df):
    """Build management analysis tables from the existing Visits data only."""
    if df is None or df.empty:
        empty = pd.DataFrame()
        return empty, empty, empty, empty, empty

    x = df.copy()
    x["Date"] = x["Date"].astype(str)
    x["Status"] = x["Status"].astype(str).str.strip()
    x["GPS Status"] = x["GPS Status"].astype(str).str.strip()
    x["Time Spent Hours"] = pd.to_numeric(x["Time Spent Hours"], errors="coerce")
    x["IN-OUT GPS Distance (m)"] = pd.to_numeric(x["IN-OUT GPS Distance (m)"], errors="coerce")
    x["IN Date/Time Parsed"] = pd.to_datetime(x["IN Date/Time"], errors="coerce")

    completed = x[x["Status"] == "Completed"].copy()
    employee = x.groupby(["Employee Code", "Employee Name"], dropna=False).agg(
        Visits=("Visit ID", "count"),
        Completed=("Status", lambda s: int((s == "Completed").sum())),
        Pending_IN=("Status", lambda s: int((s == "IN").sum())),
        Avg_Time_Hours=("Time Spent Hours", "mean"),
        Big_GPS_Difference=("GPS Status", lambda s: int((s == "Big Difference").sum())),
    ).reset_index()
    employee["Avg_Time_Hours"] = employee["Avg_Time_Hours"].round(2)

    outlet = x.groupby(["Outlet Code", "Outlet Name"], dropna=False).agg(
        Visits=("Visit ID", "count"),
        Completed=("Status", lambda s: int((s == "Completed").sum())),
        Pending_IN=("Status", lambda s: int((s == "IN").sum())),
        Avg_Time_Hours=("Time Spent Hours", "mean"),
        Big_GPS_Difference=("GPS Status", lambda s: int((s == "Big Difference").sum())),
    ).reset_index()
    outlet["Avg_Time_Hours"] = outlet["Avg_Time_Hours"].round(2)

    gps = x.groupby("GPS Status", dropna=False).agg(Visits=("Visit ID", "count")).reset_index()
    gps["Percentage"] = (gps["Visits"] / max(len(x), 1) * 100).round(1)

    daily = x.groupby("Date", dropna=False).agg(
        Visits=("Visit ID", "count"),
        Completed=("Status", lambda s: int((s == "Completed").sum())),
        Pending_IN=("Status", lambda s: int((s == "IN").sum())),
        Big_GPS_Difference=("GPS Status", lambda s: int((s == "Big Difference").sum())),
    ).reset_index().sort_values("Date")

    summary = pd.DataFrame([{
        "Metric": "Total Visits", "Value": len(x)
    }, {
        "Metric": "Completed Visits", "Value": int((x["Status"] == "Completed").sum())
    }, {
        "Metric": "Pending IN", "Value": int((x["Status"] == "IN").sum())
    }, {
        "Metric": "Unique Employees", "Value": x["Employee Code"].astype(str).nunique()
    }, {
        "Metric": "Unique Outlets", "Value": x["Outlet Code"].astype(str).nunique()
    }, {
        "Metric": "Average Completed Visit Hours", "Value": round(completed["Time Spent Hours"].mean(), 2) if not completed.empty else 0
    }, {
        "Metric": "Average GPS Distance (m)", "Value": round(completed["IN-OUT GPS Distance (m)"].mean(), 1) if not completed.empty else 0
    }])
    return summary, employee, outlet, gps, daily


def _apply_management_filters(df, start_date, end_date, employee_filter, outlet_filter, status_filter, gps_filter):
    x = df.copy()
    if x.empty:
        return x
    x["Date"] = x["Date"].astype(str)
    x = x[(x["Date"] >= str(start_date)) & (x["Date"] <= str(end_date))]
    if employee_filter != "All":
        x = x[x["Employee Code"].astype(str) == str(employee_filter)]
    if outlet_filter != "All":
        x = x[x["Outlet Code"].astype(str) == str(outlet_filter)]
    if status_filter != "All":
        x = x[x["Status"].astype(str).str.strip() == status_filter]
    if gps_filter != "All":
        x = x[x["GPS Status"].astype(str).str.strip() == gps_filter]
    return x


@st.cache_data(ttl=120, max_entries=64, show_spinner=False)
def cached_management_analysis(data_version, start_date, end_date, employee_filter, outlet_filter, status_filter, gps_filter):
    """Cache management calculations by the shared Visits-store version and filters.

    The version changes when the shared in-memory mirror is refreshed or updated, so
    cached analytics are reused across normal Streamlit reruns without repeatedly
    recalculating the same Pandas group-bys.
    """
    all_data = get_all_visits()
    filtered = _apply_management_filters(
        all_data, start_date, end_date, employee_filter, outlet_filter, status_filter, gps_filter
    )
    return filtered, build_management_analysis(filtered)


@st.cache_data(ttl=120, max_entries=32, show_spinner=False)
def cached_management_excel(data_version, start_date, end_date, employee_filter, outlet_filter, status_filter, gps_filter):
    """Cache the generated management workbook for repeated UI reruns/downloads."""
    all_data = get_all_visits()
    filtered = _apply_management_filters(
        all_data, start_date, end_date, employee_filter, outlet_filter, status_filter, gps_filter
    )
    summary, employee_analysis, outlet_analysis, gps_analysis, daily_analysis = build_management_analysis(filtered)
    return management_excel_bytes(
        filtered, summary, employee_analysis, outlet_analysis, gps_analysis, daily_analysis
    )


with st.sidebar:
    st.title("Customer Care Day")
    menu = st.radio("Menu", ["Employee Visit", "Admin / Reports"])


if menu == "Employee Visit":
    show_brand_header(show_title=True)
    show_install_help()
    st.caption("Employee IN / OUT Attendance")

    if st.session_state["last_message"]:
        st.info(st.session_state["last_message"])

    st.markdown('<div class="ccd-section">Employee & Outlet Details</div>', unsafe_allow_html=True)
    st.caption("Enter your Employee Code and Employee Name. Enter the Outlet Code; the official Outlet Name and GPS coordinates are loaded from the controlled Outlet Master.")
    try:
        master_info = master_status()
        if master_info["active_outlets"] == 0:
            st.warning("Outlet Master Data is not configured yet. Please ask an administrator to upload the Outlet Master.")
            st.stop()
    except Exception as master_exc:
        st.error(f"Unable to load Outlet Master Data: {master_exc}")
        st.stop()

    employee_code_input = st.text_input("Employee Code", value=st.session_state["employee_code"], max_chars=50)
    employee_name_input = st.text_input("Employee Name", value=st.session_state["employee_name"], max_chars=100)
    outlet_code_input = st.text_input("Outlet Code", value=st.session_state["outlet_code"], max_chars=50)

    # Outlet lookup is intentionally outside the form so the official outlet name
    # appears immediately after the employee enters an outlet code.
    live_outlet = None
    live_outlet_code = outlet_code_input.strip()
    if live_outlet_code:
        if not re.fullmatch(r"\d+", live_outlet_code):
            st.error("Outlet Code must contain numbers only.")
        else:
            try:
                live_outlet = lookup_outlet(live_outlet_code)
                if live_outlet is None:
                    st.error("Outlet not found or inactive in Outlet Master.")
                else:
                    st.success(f"Outlet Name: {live_outlet['Outlet Name']}")
            except Exception as outlet_lookup_exc:
                st.error(f"Unable to check Outlet Master: {outlet_lookup_exc}")

    details_submitted = st.button("Continue", type="primary", use_container_width=True, key="visit_details_continue")

    if details_submitted:
        employee_code_input = employee_code_input.strip()
        employee_name_input = employee_name_input.strip()
        outlet_code_input = outlet_code_input.strip()
        basic_errors = validate_details(employee_code_input, employee_name_input, outlet_code_input, "")
        if basic_errors:
            st.error("Please correct the following:")
            for _error in basic_errors: st.write(f"• {_error}")
        else:
            master_errors, outlet = validate_master_ready(employee_code_input, employee_name_input, outlet_code_input)
            if master_errors:
                st.error("Please correct the following:")
                for _error in master_errors: st.write(f"• {_error}")
            else:
                st.session_state["employee_code"] = employee_code_input
                st.session_state["employee_name"] = employee_name_input
                st.session_state["outlet_code"] = outlet_code_input
                st.session_state["outlet_name"] = outlet["Outlet Name"]
                st.session_state["outlet_latitude"] = float(outlet["Latitude"])
                st.session_state["outlet_longitude"] = float(outlet["Longitude"])
                st.session_state["in_location"] = None
                st.session_state["out_location"] = None
                st.session_state["completed_visit"] = None
                st.session_state["last_message"] = "Master data accepted. Checking today's attendance..."
                try:
                    st.session_state["open_visit"] = find_open_visit(employee_code_input)
                    st.session_state["details_loaded"] = True
                    st.session_state["scroll_target"] = "step3_out" if st.session_state["open_visit"] is not None else "step1_in"
                except Exception as exc:
                    st.session_state["open_visit"] = None
                    st.session_state["details_loaded"] = False
                    st.error(f"Unable to check current attendance: {exc}")
                else:
                    st.rerun()

    employee_code = st.session_state["employee_code"].strip()
    employee_name = st.session_state["employee_name"].strip()
    outlet_code = st.session_state["outlet_code"].strip()
    outlet_name = st.session_state["outlet_name"].strip()

    # Keep the completed visit visible across Streamlit reruns and scroll to the
    # final summary instead of restarting the employee workflow at Step 1.
    completed_visit = st.session_state.get("completed_visit")
    if completed_visit is not None:
        st.divider()
        st.markdown('<div id="visit_summary_anchor"></div>', unsafe_allow_html=True)
        auto_scroll_if_needed("visit_summary_anchor", 250)
        st.success("Visit completed. You can close the app.")
        st.markdown('<div class="ccd-summary"><div class="ccd-summary-head">✓ Visit Summary</div></div>', unsafe_allow_html=True)
        st.write(f"**Visit ID:** {completed_visit['Visit ID']}")
        st.write(f"**Employee:** {completed_visit['Employee Name']}")
        st.write(f"**Outlet:** {completed_visit['Outlet Name']} ({completed_visit['Outlet Code']})")
        st.write(f"**IN:** {completed_visit['IN Date/Time']}")
        st.write(f"**OUT:** {completed_visit['OUT Date/Time']}")
        st.write(f"**Time Spent:** {completed_visit['Time Spent']} ({completed_visit['Time Spent Hours']:.2f} hours)")
        st.write(f"**IN → OUT GPS Distance:** {completed_visit['IN-OUT GPS Distance (m)']:.1f} m")
        in_outlet_dist = completed_visit.get("IN-Outlet GPS Distance (m)", "")
        out_outlet_dist = completed_visit.get("OUT-Outlet GPS Distance (m)", "")
        if in_outlet_dist != "":
            st.write(f"**IN GPS → Outlet Master GPS:** {float(in_outlet_dist):.1f} m")
        if out_outlet_dist != "":
            st.write(f"**OUT GPS → Outlet Master GPS:** {float(out_outlet_dist):.1f} m")
        if str(completed_visit.get("Outlet GPS Status", "")).strip() == "Mismatch > 1.5 km":
            st.warning("⚠ Your location mismatched the outlet location by more than 1.5 km.")
        status = completed_visit["GPS Status"]
        if status == "Almost Same":
            st.success("✓ GPS Status: Almost Same")
        elif status == "Some Difference":
            st.warning("⚠ GPS Status: Some Difference")
        else:
            st.error("GPS Status: Big Difference")
        st.markdown('<div class="ccd-info"><strong>✓ Visit completed.</strong><br>You can close the app.</div>', unsafe_allow_html=True)
        st.stop()

    if not employee_code or not employee_name or not outlet_code or not outlet_name:
        st.info("Enter the employee and outlet details, then press Continue.")
        st.stop()

    # Do NOT query Google Sheets on every Streamlit rerun. The shared visit store
    # refreshes periodically and the selected employee's open visit is kept in session state.
    if not st.session_state.get("details_loaded"):
        try:
            st.session_state["open_visit"] = find_open_visit(employee_code)
            st.session_state["details_loaded"] = True
        except Exception as exc:
            st.error(f"Unable to check current attendance: {exc}")
            st.stop()

    open_visit = st.session_state.get("open_visit")

    st.divider()
    st.caption(f"Employee: {employee_name} | Outlet: {outlet_name} ({outlet_code})")
    st.caption(f"Outlet Master GPS: {st.session_state.get('outlet_latitude', ""):.6f}, {st.session_state.get('outlet_longitude', ""):.6f}")

    if open_visit is None:
        show_progress(1)
        st.subheader("Step 1 — Capture GPS for IN")
        # After Continue, bring the employee directly to the IN GPS step.
        auto_scroll_if_needed("step1_in", 220)
        loc = get_location("gps_in", "IN", st.session_state.get("outlet_latitude"), st.session_state.get("outlet_longitude"))
        if loc:
            st.session_state["in_location"] = loc
            st.session_state["scroll_target"] = "step2_mark_in"

        if st.session_state["in_location"]:
            st.success("IN GPS is ready.")
            show_progress(2)
            st.subheader("Step 2 — Mark IN")
            # Once GPS has been captured, bring the Mark IN button into view.
            auto_scroll_if_needed("step2_mark_in", 220)
            if st.button("🟢 MARK IN", type="primary", use_container_width=True):
                ok, msg = location_ok(st.session_state["in_location"])
                if not ok:
                    st.error(msg)
                else:
                    try:
                        # A second Sheets read is intentionally avoided here. The shared
                        # store is updated atomically after the successful append.
                        if st.session_state.get("open_visit") is not None:
                            st.warning("This employee already has an open IN visit today.")
                        else:
                            visit_id = create_in_visit(employee_code, employee_name, outlet_code, outlet_name, st.session_state["in_location"], st.session_state.get("outlet_latitude"), st.session_state.get("outlet_longitude"))
                            st.session_state["open_visit"] = find_open_visit(employee_code)
                            st.session_state["in_location"] = None
                            st.session_state["scroll_target"] = "step3_out"
                            st.session_state["last_message"] = f"IN recorded successfully. Visit ID: {visit_id}"
                            st.success(f"✅ IN recorded. Visit ID: {visit_id}")
                            st.rerun()
                    except Exception as exc:
                        st.error("IN could not be recorded.")
                        st.exception(exc)
    else:
        st.success("🟢 This employee currently has an open visit.")
        st.write(f"**Visit ID:** {open_visit['Visit ID']}")
        st.write(f"**Employee:** {open_visit['Employee Name']}")
        st.write(f"**Outlet:** {open_visit['Outlet Name']}")
        st.write(f"**Outlet Code:** {open_visit['Outlet Code']}")
        st.write(f"**IN Time:** {open_visit['IN Date/Time']}")

        st.divider()
        show_progress(3)
        st.subheader("Step 3 — Capture GPS for OUT")
        # After a successful IN, automatically bring the employee to the OUT GPS step.
        auto_scroll_if_needed("step3_out", 220)
        loc = get_location("gps_out", "OUT", st.session_state.get("outlet_latitude"), st.session_state.get("outlet_longitude"))
        if loc:
            st.session_state["out_location"] = loc
            st.session_state["scroll_target"] = "step4_mark_out"

        if st.session_state["out_location"]:
            st.success("OUT GPS is ready.")
            show_progress(4)
            # Once OUT GPS is captured, bring the Mark OUT button into view.
            auto_scroll_if_needed("step4_mark_out", 220)
            if st.button("🔴 MARK OUT", type="primary", use_container_width=True):
                ok, msg = location_ok(st.session_state["out_location"])
                if not ok:
                    st.error(msg)
                else:
                    try:
                        if (employee_name.lower() != str(open_visit["Employee Name"]).strip().lower()
                                or outlet_code != str(open_visit["Outlet Code"]).strip()
                                or outlet_name.lower() != str(open_visit["Outlet Name"]).strip().lower()):
                            raise ValueError("Employee/Outlet details do not match the open IN visit. Please enter exactly the same details used for IN.")

                        result = complete_out_visit(open_visit["Visit ID"], st.session_state["out_location"], st.session_state.get("outlet_latitude"), st.session_state.get("outlet_longitude"), GPS_TOLERANCE_METERS, GPS_WARNING_METERS)
                        st.success("✓ OUT recorded successfully.")
                        # Persist the result, then rerun so the final summary has a
                        # stable page position and automatic scrolling can target it.
                        st.session_state["completed_visit"] = result
                        st.session_state["out_location"] = None
                        st.session_state["in_location"] = None
                        st.session_state["open_visit"] = None
                        st.session_state["details_loaded"] = True
                        st.session_state["last_message"] = "Visit completed. You can close the app."
                        st.session_state["scroll_target"] = "visit_summary_anchor"
                        st.rerun()
                    except Exception as exc:
                        st.error("OUT could not be recorded.")
                        st.exception(exc)

else:
    show_brand_header(show_title=True)
    st.markdown('<div class="ccd-section">🔐 Admin / Reports</div>', unsafe_allow_html=True)
    if not st.session_state["admin_logged_in"]:
        password = st.text_input("Admin Password", type="password")
        if st.button("Login", type="primary", use_container_width=True):
            if password == st.secrets["ADMIN_PASSWORD"]:
                st.session_state["admin_logged_in"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")

    if st.session_state["admin_logged_in"]:
        # Production mode: reporting/Excel/GitHub modules are loaded only for an
        # authenticated admin. Employee sessions never import or execute them.
        from report_manager import dataframe_to_excel_bytes, management_excel_bytes
        from github_reports import upload_or_update_report, download_report, mark_report_synced, cached_list_reports
        st.success("Admin access granted.")
        if st.button("🔒 Logout", use_container_width=True):
            st.session_state["admin_logged_in"] = False
            st.rerun()

        try:
            all_visits = get_all_visits()
            if all_visits.empty:
                st.info("No visits have been recorded yet.")
            else:
                all_visits["Date"] = all_visits["Date"].astype(str)
                all_visits["Status"] = all_visits["Status"].astype(str)
                dates = sorted(all_visits["Date"].dropna().unique().tolist(), reverse=True)
                selected_dashboard_date = st.selectbox("Dashboard date", dates)
                day_visits = _filter_date(all_visits, selected_dashboard_date)
                kpi = build_admin_summary(day_visits)

                c1, c2, c3 = st.columns(3)
                c1.metric("Total Visits", kpi["total"])
                c2.metric("Completed", kpi["completed"])
                c3.metric("Pending IN", kpi["pending"])
                c4, c5, c6 = st.columns(3)
                c4.metric("Almost Same", kpi["almost_same"])
                c5.metric("Some Difference", kpi["some_difference"])
                c6.metric("Big Difference", kpi["big_difference"])

                with st.expander("⚠️ Exceptions / Items for Review", expanded=False):
                    exceptions = build_admin_exceptions(day_visits)
                    if exceptions.empty:
                        st.success("No exceptions detected for this date.")
                    else:
                        st.warning(f"{len(exceptions)} record(s) require review.")
                        st.dataframe(exceptions, use_container_width=True, hide_index=True)
                        st.download_button(
                            "⬇️ Download Exception List",
                            data=dataframe_to_excel_bytes(exceptions, selected_dashboard_date),
                            file_name=f"Exceptions_{selected_dashboard_date}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True,
                        )

                with st.expander("📊 Visit Data", expanded=False):
                    st.dataframe(day_visits, use_container_width=True, hide_index=True)

        except Exception as exc:
            st.error(f"Could not read Google Sheet: {exc}")

        st.divider()
        st.subheader("📈 Management Analytics")
        st.caption("Analysis is generated from the existing Visits worksheet; no new employee or outlet database is required.")
        try:
            min_date = pd.to_datetime(all_visits["Date"], errors="coerce").min().date()
            max_date = pd.to_datetime(all_visits["Date"], errors="coerce").max().date()
            d1, d2 = st.columns(2)
            start_date = d1.date_input("From date", value=min_date, min_value=min_date, max_value=max_date, key="analytics_from")
            end_date = d2.date_input("To date", value=max_date, min_value=min_date, max_value=max_date, key="analytics_to")
            if start_date > end_date:
                st.error("From date cannot be after To date.")
            else:
                employee_options = ["All"] + sorted(all_visits["Employee Code"].astype(str).dropna().unique().tolist())
                outlet_options = ["All"] + sorted(all_visits["Outlet Code"].astype(str).dropna().unique().tolist())
                status_options = ["All"] + sorted(all_visits["Status"].astype(str).str.strip().dropna().unique().tolist())
                gps_options = ["All"] + sorted(all_visits["GPS Status"].astype(str).str.strip().dropna().unique().tolist())
                f1, f2 = st.columns(2)
                employee_filter = f1.selectbox("Employee Code", employee_options, key="analytics_employee")
                outlet_filter = f2.selectbox("Outlet Code", outlet_options, key="analytics_outlet")
                f3, f4 = st.columns(2)
                status_filter = f3.selectbox("Status", status_options, key="analytics_status")
                gps_filter = f4.selectbox("GPS Status", gps_options, key="analytics_gps")

                data_version = get_visit_store_version()
                filtered, analysis = cached_management_analysis(
                    data_version, start_date, end_date, employee_filter, outlet_filter, status_filter, gps_filter
                )
                summary, employee_analysis, outlet_analysis, gps_analysis, daily_analysis = analysis

                if filtered.empty:
                    st.info("No records match the selected filters.")
                else:
                    total = len(filtered)
                    completed_count = int((filtered["Status"].astype(str).str.strip() == "Completed").sum())
                    unique_employees = filtered["Employee Code"].astype(str).nunique()
                    unique_outlets = filtered["Outlet Code"].astype(str).nunique()
                    a1, a2, a3, a4 = st.columns(4)
                    a1.metric("Visits", total)
                    a2.metric("Completed", completed_count)
                    a3.metric("Employees", unique_employees)
                    a4.metric("Outlets", unique_outlets)

                    st.markdown("**Daily visit trend**")
                    trend_chart = daily_analysis.set_index("Date")[["Visits", "Completed", "Pending_IN"]]
                    st.line_chart(trend_chart, use_container_width=True)

                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown("**GPS distribution**")
                        st.bar_chart(gps_analysis.set_index("GPS Status")[["Visits"]], use_container_width=True)
                    with c2:
                        st.markdown("**Outlet visit volume**")
                        outlet_chart = outlet_analysis.sort_values("Visits", ascending=False).head(15).copy()
                        st.bar_chart(outlet_chart.set_index("Outlet Code")[["Visits"]], use_container_width=True)

                    with st.expander("Employee Analysis", expanded=False):
                        st.dataframe(employee_analysis, use_container_width=True, hide_index=True)
                    with st.expander("Outlet Analysis", expanded=False):
                        st.dataframe(outlet_analysis, use_container_width=True, hide_index=True)
                    with st.expander("Daily Analysis", expanded=False):
                        st.dataframe(daily_analysis, use_container_width=True, hide_index=True)

                    management_bytes = cached_management_excel(
                        data_version, start_date, end_date, employee_filter, outlet_filter, status_filter, gps_filter
                    )
                    st.download_button(
                        "⬇️ Download Management Analytics Excel",
                        data=management_bytes,
                        file_name=f"Management_Analytics_{start_date}_to_{end_date}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                        key="download_management_analytics",
                    )
        except Exception as analytics_exc:
            st.warning(f"Management analytics could not be generated: {analytics_exc}")

        st.divider()
        st.subheader("🗂️ Outlet Master Data")
        st.caption("Employees enter their own Employee Code and Name. Outlet Code, official Outlet Name and outlet GPS coordinates are controlled by the Outlet Master.")
        try:
            ms = master_status()
            m1, m2 = st.columns(2)
            m1.metric("Outlets", ms["outlets"])
            m2.metric("Active Outlets", ms["active_outlets"])
        except Exception as exc:
            st.warning(f"Outlet Master status unavailable: {exc}")

        st.download_button("⬇️ Outlet Master Template", data=template_excel("outlets"), file_name="Outlet_Master_Template.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)

        with st.expander("Upload / Replace Outlet Master", expanded=False):
            outlet_file = st.file_uploader("Outlet Master Excel", type=["xlsx"], key="outlet_master_upload")
            st.caption("Required columns: Outlet Code, Outlet Name, Latitude, Longitude, Active")
            if st.button("Replace Outlet Master", use_container_width=True, key="replace_outlet_master"):
                if outlet_file is None:
                    st.error("Please select an Outlet Master Excel file.")
                else:
                    try:
                        outlet_df = pd.read_excel(outlet_file, dtype=str)
                        from master_data import get_master_store
                        saved = get_master_store().replace_outlets(outlet_df)
                        st.success(f"Outlet Master updated: {len(saved)} records.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Outlet Master was not updated: {exc}")

        st.divider()
        st.subheader("Daily Excel Reports")
        st.caption("Production mode: GitHub/Excel work is admin-triggered so employee IN/OUT stays lightweight.")
        try:
            if st.button("Sync Today's Report to GitHub", use_container_width=True, key="sync_today_admin"):
                today_str = now_local().strftime("%Y-%m-%d")
                latest = get_all_visits(force_refresh=True)
                today_df = _filter_date(latest, today_str)
                today_path = f"data/visits/Visits_{today_str}.xlsx"
                upload_or_update_report(
                    today_path,
                    dataframe_to_excel_bytes(today_df, today_str),
                    f"Admin sync daily report {today_str}",
                )
                mark_report_synced()
                cached_list_reports.clear()
                st.success(f"Today's report synced: Visits_{today_str}.xlsx")
                st.rerun()

            reports = cached_list_reports()
            if not reports:
                st.info("No Excel reports have been synced yet.")
            else:
                labels = [os.path.basename(x) for x in reports]
                selected_label = st.selectbox("Select report", labels)
                selected_path = reports[labels.index(selected_label)]
                if st.button("Refresh Selected Report from Google Sheet", use_container_width=True):
                    selected_date = selected_label.replace("Visits_", "").replace(".xlsx", "")
                    df = get_all_visits(force_refresh=True)
                    day_df = _filter_date(df, selected_date)
                    upload_or_update_report(
                        selected_path,
                        dataframe_to_excel_bytes(day_df, selected_date),
                        f"Refresh report {selected_date}",
                    )
                    mark_report_synced()
                    st.success("Report refreshed in GitHub.")
                    st.rerun()
                report_bytes = download_report(selected_path)
                st.download_button(
                    "⬇️ Download Excel",
                    data=report_bytes,
                    file_name=selected_label,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
        except Exception as exc:
            st.error(f"Could not load GitHub reports: {exc}")


st.markdown('<div class="ccd-footer">WE CARE. YOU DRIVE. &nbsp;•&nbsp; Customer Care Day</div>', unsafe_allow_html=True)
