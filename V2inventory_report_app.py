# inventory_report_app.py

import streamlit as st
import pandas as pd
from io import BytesIO
from datetime import datetime, timedelta
import numpy as np
import re

# ---- ACCESS GATE: this goes here ----
def _gate():
    if st.session_state.get("_auth_ok", False):
        return

    st.title("🔒 Protected Access")
    with st.form("gate", clear_on_submit=True):
        pw = st.text_input("Enter passcode", type="password")
        ok = st.form_submit_button("Enter")

    if ok:
        # Get list of allowed passcodes from secrets
        allowed = st.secrets.get("APP_PASSCODES", [])

        # In case someone accidentally makes it a single string in secrets,
        # normalize to a list.
        if isinstance(allowed, str):
            allowed = [allowed]

        if pw in allowed:
            st.session_state["_auth_ok"] = True
            st.rerun()
        else:
            st.error("❌ Incorrect passcode.")

    st.stop()

_gate()
# ---- END ACCESS GATE ----

st.set_page_config(page_title="Inventory Report Generator", layout="wide")

# --- Compact spacing + sticky toolbar CSS ---
st.markdown("""
<style>
:root {
  --app-surface: rgba(255,255,255,.045);
  --app-surface-strong: rgba(255,255,255,.075);
  --app-border: rgba(255,255,255,.13);
  --app-muted: rgba(250,250,250,.66);
  --app-accent: #38bdf8;
}

.block-container {
  max-width: 100%;
  padding: 1.35rem 2rem 1.5rem;
}

section[data-testid="stSidebar"] .block-container { padding-top: .8rem; }

h1 {
  font-size: 2rem !important;
  line-height: 1.15 !important;
  margin-bottom: .35rem !important;
}

h2, h3 {
  letter-spacing: 0;
}

div[data-testid="stAlert"] {
  border-radius: 8px;
  border: 1px solid var(--app-border);
}

.app-status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin: .2rem 0 .75rem;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: .45rem;
  color: #d7fbe8;
  background: rgba(34,197,94,.12);
  border: 1px solid rgba(34,197,94,.28);
  border-radius: 999px;
  padding: .38rem .7rem;
  font-weight: 700;
  font-size: .86rem;
}

.upload-panel {
  border: 1px solid var(--app-border);
  background: var(--app-surface);
  border-radius: 8px;
  padding: 1rem 1.15rem 1.2rem;
  margin-top: .8rem;
}

.section-label {
  color: var(--app-muted);
  font-size: .78rem;
  font-weight: 800;
  letter-spacing: .04em;
  margin: .15rem 0 .5rem;
  text-transform: uppercase;
}

.sticky-toolbar {
  position: sticky;
  top: 0;
  z-index: 999;
  background: color-mix(in srgb, var(--background-color) 94%, #101827 6%);
  border: 1px solid var(--app-border);
  border-radius: 8px;
  padding: .85rem .95rem .7rem;
  margin: .65rem 0 .7rem;
  box-shadow: 0 10px 32px rgba(0,0,0,.22);
}

.sticky-toolbar [data-testid="stMetric"] {
  background: var(--app-surface);
  border: 1px solid rgba(255,255,255,.09);
  border-radius: 8px;
  padding: .55rem .65rem;
  min-height: 4.55rem;
}

.sticky-toolbar [data-testid="stMetricLabel"] {
  color: var(--app-muted);
  font-size: .74rem;
}

.sticky-toolbar [data-testid="stMetricValue"] {
  font-size: 1.35rem;
}

.small-caption {
  color: var(--app-muted);
  font-size: .82rem;
  margin: -.15rem 0 .75rem;
}

div[data-testid="stTabs"] button {
  font-weight: 750;
}

div[data-testid="stDataFrame"] {
  border: 1px solid var(--app-border);
  border-radius: 8px;
  overflow: hidden;
}

.stButton > button,
.stDownloadButton > button {
  border-radius: 7px;
  border-color: var(--app-border);
  font-weight: 700;
}

label[data-testid="stWidgetLabel"] p {
  font-weight: 700;
}
</style>
""", unsafe_allow_html=True)

# --------- Header + KPI placeholder ---------
st.title("📦 Inventory Report Generator")
# Placeholder we'll create inside the toolbar instead
kpi_toolbar_placeholder = None

# -------------------- Utilities & Helpers --------------------

def _lower_name(name):
    try:
        return str(name).strip().lower()
    except Exception:
        return ""

def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df

def clean_key(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    s = str(x).replace("\u00A0", "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return re.sub(r"\s+", " ", s)

def _as_item_key(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    s = str(x).replace("\u00A0", "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    # collapse any runs of whitespace to a single space
    s = re.sub(r"\s+", " ", s)

    # remove spaces for canonical form, but KEEP hyphens
    no_space = s.replace(" ", "")

    # If the value is purely numeric when ignoring hyphens, normalize to digits (no hyphens) and strip leading zeros
    if re.sub(r"-", "", no_space).isdigit():
        digits_only = re.sub(r"-", "", no_space)
        return digits_only.lstrip("0") or "0"

    # Otherwise (alphanumeric), keep hyphens and normalize case
    return no_space.upper()

def to_dt(series: pd.Series) -> pd.Series:
    s = pd.to_datetime(series, errors="coerce")
    need_fix = s.isna() & pd.to_numeric(series, errors="coerce").notna()
    if need_fix.any():
        serial = pd.to_numeric(series[need_fix], errors="coerce")
        s.loc[need_fix] = pd.to_datetime("1899-12-30") + pd.to_timedelta(serial, unit="D")
    return s

def _find_weekly_cols(df: pd.DataFrame) -> list:
    weekly = []
    for c in df.columns:
        c_str = str(c).strip()
        dt = pd.to_datetime(c_str, errors="coerce")
        if pd.notna(dt):
            weekly.append(c); continue
        if re.match(r"^\d{1,2}[-/ ]?[A-Za-z]{3}$", c_str):
            weekly.append(c)
    return weekly

# -------------------- NOTES-aware logic --------------------

def _parse_mmdd_to_dt(mmdd: str, year_hint: int | None = None):
    try:
        m, d = [int(x) for x in mmdd.split("/")]
        y = year_hint or datetime.today().year
        dt = datetime(y, m, d)
        return pd.to_datetime(dt.date())
    except Exception:
        return pd.NaT

def analyze_notes(row: pd.Series) -> dict:
    text = ""
    if "NOTES" in row.index and pd.notna(row["NOTES"]):
        text = str(row["NOTES"]).lower()

    hot = "hot container" in text or "hot container" in text.replace("-", " ")
    hold = "hold" in text

    delivered_date = pd.NaT
    da_date = pd.NaT

    year_hint = None
    if "DUE DATE" in row.index and pd.notna(row["DUE DATE"]):
        try:
            year_hint = pd.to_datetime(row["DUE DATE"]).year
        except Exception:
            year_hint = None

    if text:
        for line in text.splitlines():
            s = line.strip().lower()
            m_del = re.search(r"delivered:\s*(\d{1,2}/\d{1,2})", s)
            if m_del and pd.isna(delivered_date):
                delivered_date = _parse_mmdd_to_dt(m_del.group(1), year_hint)
            m_da = re.search(r"\bda:\s*(\d{1,2}/\d{1,2})", s)
            if m_da and pd.isna(da_date):
                da_date = _parse_mmdd_to_dt(m_da.group(1), year_hint)

    return {"delivered_date": delivered_date, "da_date": da_date,
            "hot_flag": bool(hot), "hold_flag": bool(hold)}

ETA_PRIORITY = [
    "ETA AT PLACE OF DELIVERY", "ETA", "ETD", "ATD",
    "DUE DATE", "RQD. DATE", "LOAD DATE", "ORDER DATE"
]

def best_eta_for_row(row: pd.Series):
    for col in ["ETA AT PLACE OF DELIVERY", "ETA", "ETD", "ATD"]:
        if col in row.index and pd.notna(row[col]):
            return to_dt(pd.Series([row[col]])).iloc[0]
    da = analyze_notes(row)["da_date"]
    if pd.notna(da):
        return da
    for col in ["DUE DATE", "RQD. DATE", "LOAD DATE", "ORDER DATE"]:
        if col in row.index and pd.notna(row[col]):
            return to_dt(pd.Series([row[col]])).iloc[0]
    return pd.NaT

def compute_status_flag(row: pd.Series) -> bool:
    fields = []
    for col in ["CLEAN STATUS", "SHIPMENT STATUS", "LAST STATUS", "STATUS"]:
        if col in row.index and pd.notna(row[col]):
            fields.append(str(row[col]).strip().lower())
    joined = " ".join(fields)
    exclude_keywords = ["delivered", "closed"]
    delivered_like = any(k in joined for k in exclude_keywords)

    delivered_in_notes = analyze_notes(row)["delivered_date"]
    delivered_flag = pd.notna(delivered_in_notes)

    invoiced = False
    if "INVOICED" in row.index:
        inv_val = row["INVOICED"]
        invoiced = (pd.notna(inv_val) and str(inv_val).strip().lower() not in ["", "0", "0.0", "nan", "no", "false"])
    return delivered_like or delivered_flag or invoiced

# -------------------- Master & Client builders --------------------

def normalize_master(master_raw: pd.DataFrame) -> pd.DataFrame:
    m = master_raw.copy()
    m.columns = m.columns.str.strip().str.upper()
    if "ITEM #" not in m.columns or "PO/LOT" not in m.columns:
        raise ValueError("Master must contain columns 'ITEM #' and 'PO/LOT' in sheet OPEN_ORDERS.")
    HEADER_ALIASES = {
        "ETA PLACE OF DELIVERY": "ETA AT PLACE OF DELIVERY",
        "ETA PLACE DELIVERY": "ETA AT PLACE OF DELIVERY",
        "ETA POD": "ETA AT PLACE OF DELIVERY",
        "SHIPMENT": "SHIPMENT STATUS",
    }
    m.columns = [HEADER_ALIASES.get(c, c) for c in m.columns]
    m["ITEM #"] = m["ITEM #"].apply(_as_item_key)
    m["PO/LOT"] = m["PO/LOT"].apply(clean_key)
    for dcol in list(set(ETA_PRIORITY + ["UPDATE D"])):
        if dcol in m.columns:
            m[dcol] = to_dt(m[dcol])
    m["QTY"] = pd.to_numeric(m["QTY"], errors="coerce") if "QTY" in m.columns else np.nan
    m["_EXCLUDE"] = m.apply(compute_status_flag, axis=1)
    m["AVAILABLE_QTY"] = m["QTY"].fillna(0)
    return m

def _auto_find_sheet(sheet_names, keywords):
    for sn in sheet_names:
        if any(k in _lower_name(sn) for k in keywords):
            return sn
    return None

def build_client_from_raw_or_prepared(uploaded_client_file) -> pd.DataFrame:
    # Prepared single-sheet first
    df0 = pd.read_excel(uploaded_client_file, sheet_name=0, engine="openpyxl")
    df0 = _norm_cols(df0)
    prepared = {"ITEM #","PO/LOT","Weekly Sales","Total On Hand","Total SO"}
    if prepared.issubset(df0.columns):
        df0["ITEM #"] = df0["ITEM #"].apply(_as_item_key)
        for c in ["Weekly Sales","Total On Hand","Total SO"]:
            df0[c] = pd.to_numeric(df0[c], errors="coerce").fillna(0)
        return df0[["ITEM #","PO/LOT","Weekly Sales","Total On Hand","Total SO"]]

    # Raw workbook
    xl = pd.ExcelFile(uploaded_client_file, engine="openpyxl")
    sheet_names = xl.sheet_names
    forecast_guess = _auto_find_sheet(sheet_names, ["forecast","per item","per-item"])
    avail_guess    = _auto_find_sheet(sheet_names, ["avail","availability","summary"])
    if not forecast_guess or not avail_guess:
        st.warning("Couldn’t auto-detect Forecast/Availability sheets. Please select them below.")
        forecast_guess = st.selectbox("Select the Forecast sheet", sheet_names, index=0)
        avail_default_idx = 1 if len(sheet_names) > 1 else 0
        avail_guess = st.selectbox("Select the Availability sheet", sheet_names, index=avail_default_idx)

    fc = pd.read_excel(uploaded_client_file, sheet_name=forecast_guess, engine="openpyxl")
    av = pd.read_excel(uploaded_client_file, sheet_name=avail_guess,    engine="openpyxl")
    fc = _norm_cols(fc); av = _norm_cols(av)

    fc_item_col = next((c for c in fc.columns if _lower_name(c) in ["item","fin item#","fin item #","item #","item#"]), None) or fc.columns[0]
    av_item_col = next((c for c in av.columns if _lower_name(c) in ["fin item#","fin item #","item","item #","item#"]), None) or av.columns[0]

    fc["_ITEM"] = fc[fc_item_col].apply(_as_item_key)
    av["_ITEM"] = av[av_item_col].apply(_as_item_key)

    weekly_cols = _find_weekly_cols(fc)
    av_wk_col = next((c for c in av.columns if _lower_name(c) in
                     ["wkly sls","weekly sales","wkly sales","avg wkly sls","avg weekly sales"]), None)

    if av_wk_col:
        wkly = av[["_ITEM", av_wk_col]].rename(columns={av_wk_col: "Weekly Sales"})
    else:
        if not weekly_cols:
            weekly_cols = [c for c in fc.columns if pd.to_datetime(str(c), errors="coerce").notna()]
        if not weekly_cols:
            raise ValueError("Couldn't detect weekly forecast columns on the selected Forecast sheet.")
        wkly = (
            fc.set_index("_ITEM")[weekly_cols]
              .apply(pd.to_numeric, errors="coerce")
              .mean(axis=1)
              .rename("Weekly Sales")
              .reset_index()
        )

    fc_oh_col = next((c for c in fc.columns if _lower_name(c) in
                     ["current on hand qty","current on hand","on hand","oh","total on hand"]), None)
    av_oh_col = next((c for c in av.columns if _lower_name(c) in
                     ["total oh","oh","on hand","total on hand"]), None)
    if fc_oh_col:
        oh_df = fc[["_ITEM", fc_oh_col]].rename(columns={fc_oh_col: "Total On Hand"})
    elif av_oh_col:
        oh_df = av[["_ITEM", av_oh_col]].rename(columns={av_oh_col: "Total On Hand"})
    else:
        oh_df = pd.DataFrame({"_ITEM": av["_ITEM"].unique(), "Total On Hand": 0})

    av_so_col = next((c for c in av.columns if _lower_name(c) in
                     ["total so","so","open so","open sales orders","sales orders"]), None)
    if av_so_col:
        so_df = av[["_ITEM", av_so_col]].rename(columns={av_so_col: "Total SO"})
    else:
        so_df = pd.DataFrame({"_ITEM": av["_ITEM"].unique(), "Total SO": 0})

    client = wkly.merge(oh_df, on="_ITEM", how="outer").merge(so_df, on="_ITEM", how="outer")
    client.rename(columns={"_ITEM": "ITEM #"}, inplace=True)
    client["ITEM #"] = client["ITEM #"].apply(_as_item_key)

    for c in ["Weekly Sales","Total On Hand","Total SO"]:
        client[c] = pd.to_numeric(client[c], errors="coerce").fillna(0)

    client["PO/LOT"] = ""
    client = client[["ITEM #","PO/LOT","Weekly Sales","Total On Hand","Total SO"]]
    return client

# -------------------- Picker --------------------

def select_best_po_for_item(master_norm: pd.DataFrame, item: str, maybe_code: str | None = None) -> dict | None:
    sub = master_norm[master_norm["ITEM #"] == item].copy()
    if "C. CODE" in master_norm.columns and maybe_code:
        sub = sub[sub["C. CODE"].astype(str).str.strip() == str(maybe_code).strip()]

    sub = sub[(~sub["_EXCLUDE"]) & (sub["AVAILABLE_QTY"] > 0)]
    if sub.empty:
        return None

    sub["_ETA"] = sub.apply(best_eta_for_row, axis=1)
    od = "ORDER DATE" if "ORDER DATE" in sub.columns else None

    def po_num(x):
        try:
            return int(re.sub(r"\D","",str(x)))
        except Exception:
            return 10**12

    sub["_PO_NUM"] = sub["PO/LOT"].apply(po_num)
    sub["_ETA_SORT"] = sub["_ETA"].fillna(pd.Timestamp.max)

    sort_by = ["_ETA_SORT", "AVAILABLE_QTY"]
    ascending = [True, False]
    if od:
        sort_by.append(od); ascending.append(True)
    sort_by.append("_PO_NUM"); ascending.append(True)

    chosen = sub.sort_values(by=sort_by, ascending=ascending).iloc[0]
    delivery_date = chosen["_ETA"]

    return {"PO/LOT": chosen["PO/LOT"], "Delivery Date": delivery_date, "Delivery Qty": chosen["QTY"]}

# -------------------- Overrides UI helpers --------------------

AUTO_SENTINEL = "__AUTO__"

def build_po_choices_by_item(master_norm: pd.DataFrame, client_df: pd.DataFrame) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for item in client_df["ITEM #"].dropna().astype(str).map(_as_item_key).unique():
        sub = master_norm[(master_norm["ITEM #"] == item) & (~master_norm["_EXCLUDE"]) & (master_norm["AVAILABLE_QTY"] > 0)].copy()
        if sub.empty:
            out[item] = []; continue
        sub["_ETA"] = sub.apply(best_eta_for_row, axis=1)
        choices = []
        for _, r in sub.iterrows():
            choices.append({"po": r["PO/LOT"], "eta": r["_ETA"], "qty": r.get("QTY", np.nan)})
        def po_num(x):
            try:
                return int(re.sub(r"\D","",str(x)))
            except Exception:
                return 10**12
        tmp = pd.DataFrame({
            "i": range(len(choices)),
            "eta": [c["eta"] if pd.notna(c["eta"]) else pd.Timestamp.max for c in choices],
            "qty": [-float(c["qty"] or 0) for c in choices],
            "po": [po_num(c["po"]) for c in choices]
        }).sort_values(by=["eta","qty","po"], ascending=[True, True, True])
        out[item] = [choices[i] for i in tmp["i"]]
    return out

def label_for_choice(choice: dict) -> str:
    po = str(choice["po"]); eta = choice["eta"]; qty = choice["qty"]
    eta_s = (pd.to_datetime(eta).date().isoformat() if pd.notna(eta) else "—")
    qty_s = "" if (qty is None or (isinstance(qty, float) and np.isnan(qty))) else str(int(qty) if float(qty).is_integer() else f"{qty:.2f}")
    return f"{po}  |  ETA: {eta_s}  |  Qty: {qty_s}"

# -------------------- Cached file readers --------------------

@st.cache_data(show_spinner=False)
def _read_master_bytes(file_bytes: bytes) -> pd.DataFrame:
    return pd.read_excel(BytesIO(file_bytes), sheet_name="OPEN_ORDERS", engine="openpyxl")

@st.cache_data(show_spinner=False)
def _read_client_bytes(file_bytes: bytes) -> pd.DataFrame:
    return build_client_from_raw_or_prepared(BytesIO(file_bytes))

# -------------------- Upload handling (hide after uploaded) --------------------

def _set_uploaded_files_to_state(master_upl, client_upl):
    if master_upl and client_upl:
        st.session_state["master_bytes"] = master_upl.getvalue()
        st.session_state["client_bytes"] = client_upl.getvalue()
        st.session_state["files_uploaded"] = True
        st.success("✅ Files loaded. Dashboard ready.")
        st.rerun()

if "files_uploaded" not in st.session_state:
    st.session_state["files_uploaded"] = False

if not st.session_state["files_uploaded"]:
    st.markdown('<div class="upload-panel">', unsafe_allow_html=True)
    st.markdown('<div class="section-label">Start here</div>', unsafe_allow_html=True)
    up_left, up_right = st.columns(2, gap="large")
    with up_left:
        uploaded_master = st.file_uploader("Master Excel file", type=["xlsx","xlsm"], key="master")
    with up_right:
        uploaded_client = st.file_uploader("Client Excel file", type=["xlsx","xlsm"], key="client")
    st.markdown('</div>', unsafe_allow_html=True)
    _set_uploaded_files_to_state(uploaded_master, uploaded_client)
else:
    status_col, reset_col = st.columns([5, 1.25])
    with status_col:
        st.markdown(
            '<div class="app-status-row"><span class="status-pill">✓ Files loaded</span></div>',
            unsafe_allow_html=True,
        )
    with reset_col:
        reset_files = st.button("Reset files", help="Upload a different master/client file pair.", use_container_width=True)
    if reset_files:
        for k in ["files_uploaded", "master_bytes", "client_bytes", "po_overrides", "last_selected_item"]:
            st.session_state.pop(k, None)
        st.rerun()

# -------------------- Date override --------------------
today = datetime.today().date()
with st.expander("Date settings", expanded=False):
    custom_today = st.date_input("Use a custom 'today' date", today)
    if custom_today:
        today = custom_today

# -------------------- Main flow --------------------

master = None
client = None
po_choices_by_item = {}

if st.session_state.get("files_uploaded") and st.session_state.get("master_bytes") and st.session_state.get("client_bytes"):
    try:
        master_raw = _read_master_bytes(st.session_state["master_bytes"])
        master = normalize_master(master_raw)
    except Exception as e:
        st.error(f"❌ Problem with master file: {e}")

    try:
        client = _read_client_bytes(st.session_state["client_bytes"])
    except Exception as e:
        st.error(f"❌ Problem reading client file: {e}")

    if master is not None and client is not None:
        po_choices_by_item = build_po_choices_by_item(master, client)

        # -------------------- STICKY TOOLBAR --------------------
        with st.container():
            st.markdown('<div class="sticky-toolbar">', unsafe_allow_html=True)

            # Toolbar row: Left (Item/PO) | Middle (Targets) | Right (placeholder for KPIs)
            tl, tm, tr = st.columns([1.7, 1.6, 2.7], gap="large")

            # LEFT: item & PO selectors
            with tl:
                st.markdown('<div class="section-label">Item and PO</div>', unsafe_allow_html=True)

                # Item select (single-click stable)
                all_items = client["ITEM #"].astype(str).map(_as_item_key).unique().tolist()
                if "last_selected_item" not in st.session_state:
                    st.session_state["last_selected_item"] = all_items[0] if all_items else ""

                def _update_selected_item():
                    st.session_state["last_selected_item"] = st.session_state["_temp_item_select"]

                st.selectbox(
                    "Item",
                    all_items,
                    index=all_items.index(st.session_state["last_selected_item"]) if all_items else 0,
                    key="_temp_item_select",
                    on_change=_update_selected_item,
                )
                selected_item = st.session_state["last_selected_item"]

                # PO/LOT selector (single-click stable)
                options = po_choices_by_item.get(selected_item, [])
                labels = ["Auto (best)"] + [label_for_choice(c) for c in options]
                values = [AUTO_SENTINEL] + [c["po"] for c in options]

                if "po_overrides" not in st.session_state:
                    st.session_state["po_overrides"] = {}

                temp_key = f"_temp_po_select_{selected_item}"
                if temp_key not in st.session_state:
                    st.session_state[temp_key] = st.session_state["po_overrides"].get(selected_item, AUTO_SENTINEL)

                def _update_po_override():
                    st.session_state["po_overrides"][selected_item] = st.session_state[temp_key]

                st.selectbox(
                    "PO/LOT",
                    values,
                    index=values.index(st.session_state[temp_key]) if st.session_state[temp_key] in values else 0,
                    format_func=lambda v: "Auto (best)" if v == AUTO_SENTINEL else next((label_for_choice(c) for c in options if c["po"] == v), v),
                    key=temp_key,
                    on_change=_update_po_override,
                )

                # --- Reset buttons (inserted right after the PO/LOT selectbox) ---
                b1, b2 = st.columns([1.6, 1.4])

                with b1:
                    if st.button("Reset item", help="Clear the PO override for the selected item.", use_container_width=True):
                        # clear override
                        st.session_state["po_overrides"].pop(selected_item, None)
                    
                        # REMOVE widget state instead of assigning
                        st.session_state.pop(f"_temp_po_select_{selected_item}", None)
                    
                        st.toast(f"Override cleared for {selected_item}.")
                        st.rerun()

                with b2:
                    if st.button("Reset all", help="Revert every item to Auto (best).", use_container_width=True):
                        st.session_state["po_overrides"] = {}
                    
                        # REMOVE widget states instead of assigning
                        for k in list(st.session_state.keys()):
                            if k.startswith("_temp_po_select_"):
                                st.session_state.pop(k, None)
                    
                        st.toast("All PO overrides cleared. Items now using Auto (best).")
                        st.rerun()

                # --- end reset buttons ---

                st.checkbox("Keep selected item first", value=True, key="pin_selected_top")

            # MIDDLE: targets + KPI options
            with tm:
                st.markdown('<div class="section-label">Targets</div>', unsafe_allow_html=True)
                c1, c2 = st.columns(2)
                with c1:
                    target_stock_months = st.number_input(
                        "On-hand (mo.)", min_value=0.0, max_value=24.0, value=2.0, step=0.25
                    )
                with c2:
                    target_supply_months = st.number_input(
                        "Supply (mo.)", min_value=0.0, max_value=24.0, value=3.85, step=0.25
                    )

            # KPI checkboxes
            with st.expander("KPI options", expanded=False):
                import re

                all_kpis = [
                    "Item #", "PO/LOT",
                    "Weeks in Stock", "Months in Stock", "Under/Over (on-hand)",
                    "Weeks in Supply", "Months in Supply", "Under/Over (supply)"
                ]
                default_visible = {
                    "Item #": True,
                    "PO/LOT": True,
                    "Weeks in Stock": True,
                    "Months in Stock": True,
                    "Under/Over (on-hand)": True,
                    "Weeks in Supply": True,
                    "Months in Supply": True,
                    "Under/Over (supply)": True,
                }

                def kpi_key(name: str) -> str:
                    # stable, safe key per checkbox
                    slug = re.sub(r"[^a-z0-9]+", "_", name.lower())
                    return f"kpi_chk__{slug}"

                # initialize checkbox state once
                for name in all_kpis:
                    key = kpi_key(name)
                    if key not in st.session_state:
                        st.session_state[key] = default_visible.get(name, True)

                colA, colB = st.columns(2)
                halfway = len(all_kpis) // 2
                for i, name in enumerate(all_kpis):
                    container = colA if i < halfway else colB
                    with container:
                        st.checkbox(name, key=kpi_key(name))

                # derive the visible list from checkbox values (no in-loop mutations)
                st.session_state["visible_kpis"] = [
                    name for name in all_kpis if st.session_state.get(kpi_key(name), True)
                ]

            # RIGHT: just reserve space visually; real KPIs render after calculations
            with tr:
                st.markdown('<div class="section-label">Selected item KPIs</div>', unsafe_allow_html=True)
                # placeholder that we'll fill after calculations
                kpi_toolbar_placeholder = st.empty()

            st.markdown('</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="small-caption">Auto-picking the best PO/LOT. Manual overrides apply instantly.</div>',
                unsafe_allow_html=True
            )

        # -------------------- Picks (apply overrides) --------------------
        has_client_code = "C. CODE" in client.columns
        po_overrides = st.session_state.get("po_overrides", {})

        picks = []
        for idx, row in client.iterrows():
            item = _as_item_key(row["ITEM #"])
            code = row["C. CODE"] if has_client_code else None

            chosen_po = po_overrides.get(item, AUTO_SENTINEL)
            if chosen_po and chosen_po != AUTO_SENTINEL:
                sub = master[(master["ITEM #"] == item) & (~master["_EXCLUDE"]) & (master["AVAILABLE_QTY"] > 0)].copy()
                if code is not None and "C. CODE" in master.columns:
                    sub = sub[sub["C. CODE"].astype(str).str.strip() == str(code).strip()]
                sub = sub[sub["PO/LOT"].astype(str).str.strip() == str(chosen_po).strip()]
                if not sub.empty:
                    r = sub.iloc[0]
                    eta = best_eta_for_row(r)
                    picks.append({"idx": idx, "ITEM #": item, "PO/LOT": r["PO/LOT"], "Delivery Date": eta, "Delivery Qty": r.get("QTY", np.nan)})
                    continue

            sel = select_best_po_for_item(master, item, code)
            if sel is None:
                picks.append({"idx": idx, "ITEM #": item, "PO/LOT": "", "Delivery Date": pd.NaT, "Delivery Qty": np.nan})
            else:
                picks.append({"idx": idx, "ITEM #": item, **sel})

        picks_df = pd.DataFrame(picks).set_index("idx")

        for c, default in [("Delivery Date", pd.NaT), ("Delivery Qty", np.nan), ("PO/LOT","")]:
            if c not in client.columns:
                client[c] = default

        client_updated = client.copy()
        client_updated.update(picks_df[["PO/LOT","Delivery Date","Delivery Qty"]])

        # -------------------- Forecast calculations --------------------
        df = client_updated.copy()
        df["Today"] = today
        df["Today Week"] = pd.to_datetime(today).isocalendar().week
        df["Delivery Week No."] = to_dt(df["Delivery Date"]).dt.isocalendar().week

        total_same = (
            master.groupby("ITEM #", as_index=False)["QTY"]
                  .sum()
                  .rename(columns={"QTY": "Total same item"})
        )
        df = df.merge(total_same, on="ITEM #", how="left")
        df["Total same item"] = df["Total same item"].fillna(0)

        E = pd.to_numeric(df.get("Weekly Sales"), errors="coerce")
        F = pd.to_numeric(df.get("Total On Hand"), errors="coerce")
        G = pd.to_numeric(df.get("Total SO"), errors="coerce")
        E_safe = E.replace(0, np.nan)
        deliv_qty = pd.to_numeric(df.get("Delivery Qty", 0), errors="coerce").fillna(0)

        df["Chosen PO Qty"] = deliv_qty
        df["Stock + Ordered"] = df.get("Total same item", 0).fillna(0) + F - G

        df["Weeks in Stock"]    = (F - G) / E_safe
        df["Months in Stock"]   = df["Weeks in Stock"] / 4.25
        df["Under/Over"]        = df["Months in Stock"] - target_stock_months

        df["Weeks in Supply"]   = df["Stock + Ordered"] / E_safe
        df["Months in Supply"]  = df["Weeks in Supply"] / 4.25
        df["Under/Over2"]       = df["Months in Supply"] - target_supply_months
        df["Suggested Order Qty"] = -(df["Under/Over2"]) * 4.25 * E_safe

        df["To reach 0 (wks)"]  = ((df["Stock + Ordered"] / E_safe).fillna(0)).astype(int)

        df["Day 0"]         = pd.to_datetime(today) + pd.to_timedelta(df["To reach 0 (wks)"] * 7, unit="D")
        df["Day 0"]         = pd.to_datetime(df["Day 0"], errors="coerce").dt.date
        df["Re order"]      = pd.to_datetime(df["Day 0"]) - timedelta(weeks=11)
        df["Place order 1"] = pd.to_datetime(df["Re order"]) - timedelta(weeks=12)
        df["Before today"]  = (pd.to_datetime(df["Place order 1"]) - pd.to_datetime(df["Today"])).dt.days

        df["Re orders no."] = (df["Chosen PO Qty"] / E_safe) * 7
        df["2"] = pd.to_datetime(df["Place order 1"]) + pd.to_timedelta(df["Re orders no."], unit="D")
        df["3"] = df["2"] + pd.to_timedelta(df["Re orders no."], unit="D")
        df["4"] = df["3"] + pd.to_timedelta(df["Re orders no."], unit="D")

        for col in ["Delivery Date","Day 0","Re order","Place order 1","2","3","4"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce").dt.date

        num_cols = [
            "Weeks in Stock","Months in Stock","Under/Over","Stock + Ordered",
            "Weeks in Supply","Months in Supply","Under/Over2","Suggested Order Qty",
            "Before today","Re orders no.","Total same item","Chosen PO Qty"
        ]
        for col in num_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").round(2)

        final_cols = [
            "ITEM #","PO/LOT",
            "Delivery Date","Delivery Week No.",
            "Weekly Sales","Total On Hand","Total SO",
            "Weeks in Stock","Months in Stock","Under/Over",
            "Delivery Qty","Chosen PO Qty","Today Week",
            "Total same item","Stock + Ordered",
            "Weeks in Supply","Months in Supply","Under/Over2",
            "Suggested Order Qty","To reach 0 (wks)","Day 0",
            "Re order","Place order 1","Before today","Re orders no.","2","3","4"
        ]
        df = df[[c for c in final_cols if c in df.columns]]

        # -------------------- DISPLAY VIEW (only these columns in UI) --------------------
        # Keep df unchanged for backend calculations/export; use df_view for Streamlit tables.
        display_cols_internal = [
            "ITEM #",
            "PO/LOT",
            "Delivery Date",
            "Weekly Sales",
            "Total On Hand",
            "Total SO",
            "Months in Stock",
            "Under/Over",
            "Delivery Qty",
            "Total same item",
            "Stock + Ordered",
            "Months in Supply",
            "Under/Over2",
            "Suggested Order Qty",
            "Re order",
        ]
        display_cols_internal = [c for c in display_cols_internal if c in df.columns]

        # Rename just for display labels (backend df keeps original names)
        display_rename = {
            "Total same item": "Total Same Item",
            "Stock + Ordered": "Stock + ordered",
            "Months in Supply": "Months in supply",
        }

        df_view = df[display_cols_internal].rename(columns=display_rename).copy()

        # -------------------- KPIs in sticky toolbar (driven by checkboxes) --------------------
        with kpi_toolbar_placeholder:
            sel_item_key = st.session_state.get("last_selected_item")
            sel_row = df[df["ITEM #"].astype(str).map(_as_item_key) == sel_item_key]
            visible = st.session_state.get("visible_kpis", [])

            if not sel_row.empty and visible:
                r = sel_row.iloc[0]
                cols = st.columns(len(visible))
                for i, kpi in enumerate(visible):
                    if kpi == "Item #":
                        cols[i].metric("Item #", str(r.get("ITEM #","")))
                    elif kpi == "PO/LOT":
                        cols[i].metric("PO/LOT", str(r.get("PO/LOT","")))
                    elif kpi == "Weeks in Stock":
                        cols[i].metric("Weeks in Stock", f'{r.get("Weeks in Stock","")}')
                    elif kpi == "Months in Stock":
                        cols[i].metric("Months in Stock", f'{r.get("Months in Stock","")}')
                    elif kpi == "Under/Over (on-hand)":
                        cols[i].metric("Under/Over (on-hand)", f'{r.get("Under/Over","")}')
                    elif kpi == "Weeks in Supply":
                        cols[i].metric("Weeks in Supply", f'{r.get("Weeks in Supply","")}')
                    elif kpi == "Months in Supply":
                        cols[i].metric("Months in Supply", f'{r.get("Months in Supply","")}')
                    elif kpi == "Under/Over (supply)":
                        cols[i].metric("Under/Over (supply)", f'{r.get("Under/Over2","")}')

        # -------------------- Styling helpers for tables --------------------
        def style_under_over(val):
            if pd.isna(val): return ''
            if val < 0:      return 'background-color: #f43f5e; color: white; font-weight: 700;'
            if val > 1.5:    return 'background-color: #fef08a; color: #1f2937; font-weight: 700;'
            return ''

        def make_date_urgency_styler(today_):
            today_ = pd.to_datetime(today_).date()
            def _styler(x):
                if pd.isna(x) or x == "": return ""
                try:
                    d = pd.to_datetime(x).date()
                except Exception:
                    return ""
                delta = (d - today_).days
                if delta < 0:                return "background-color: #f43f5e; color: white; font-weight: 700;"
                if 0 <= delta <= 30:         return "background-color: #fdba74; color: #1f2937; font-weight: 700;"
                if 31 <= delta <= 90:        return "background-color: #fef08a; color: #1f2937; font-weight: 700;"
                return ""
            return _styler

        def smart_format(x):
            if pd.isna(x): return ""
            if isinstance(x, (int, float, np.integer, np.floating)):
                if float(x).is_integer(): return f"{int(x)}"
                return f"{float(x):.2f}"
            return str(x)

        # -------------------- Tabs --------------------
        tab1, tab2, tab3 = st.tabs(["Selected item", "All items", "PO picks"])

        with tab1:
            st.subheader(f"Selected item: {st.session_state.get('last_selected_item') or '—'}")
            sel_row_full = df[df["ITEM #"].astype(str).map(_as_item_key) == st.session_state.get("last_selected_item")]
            if sel_row_full.empty:
                st.info("No item selected or not found.")
            else:
                sel_row_view = df_view.loc[sel_row_full.index]

                # Styling subsets based on DISPLAY column names
                uo_cols = [c for c in ["Under/Over", "Under/Over2"] if c in sel_row_view.columns]
                date_cols = [c for c in ["Re order"] if c in sel_row_view.columns]

                date_styler = make_date_urgency_styler(today)
                styled_one = (
                    sel_row_view.style
                        .map(style_under_over, subset=uo_cols)
                        .map(date_styler, subset=date_cols)
                        .format(smart_format)
                )
                st.dataframe(styled_one, use_container_width=True, height=260)

        with tab2:
            df_disp_full = df.copy()
            st.caption("Use the filters below to focus the table. The selected item stays pinned when that option is enabled.")

            # ---- Quick filters just above table ----
            filter_mode = st.radio(
                "Filter",
                ["All items", "Low on-hand", "Low supply", "Overridden only"],
                index=0, horizontal=True
            )

            if filter_mode == "Low on-hand" and "Under/Over" in df_disp_full.columns:
                df_disp_full = df_disp_full[df_disp_full["Under/Over"] < 0]
            elif filter_mode == "Low supply" and "Under/Over2" in df_disp_full.columns:
                df_disp_full = df_disp_full[df_disp_full["Under/Over2"] < 0]
            elif filter_mode == "Overridden only":
                overridden_items = {
                    k for k, v in st.session_state.get("po_overrides", {}).items() if v != AUTO_SENTINEL
                }
                df_disp_full = df_disp_full[df_disp_full["ITEM #"].astype(str).map(_as_item_key).isin(overridden_items)]

            if st.session_state.get("pin_selected_top", True) and st.session_state.get("last_selected_item"):
                df_disp_full["__is_selected"] = (df_disp_full["ITEM #"].astype(str).map(_as_item_key) == st.session_state["last_selected_item"])
                df_disp_full = df_disp_full.sort_values(by="__is_selected", ascending=False).drop(columns="__is_selected")

            # Apply the same row filtering/sorting to df_view
            df_disp_view = df_view.loc[df_disp_full.index]

            uo_cols = [c for c in ["Under/Over", "Under/Over2"] if c in df_disp_view.columns]
            date_cols = [c for c in ["Re order"] if c in df_disp_view.columns]

            date_styler = make_date_urgency_styler(today)
            styled_all = (
                df_disp_view.style
                    .map(style_under_over, subset=uo_cols)
                    .map(date_styler, subset=date_cols)
                    .format(smart_format)
            )
            st.dataframe(styled_all, use_container_width=True, height=560)

        with tab3:
            audit = client_updated[["ITEM #","PO/LOT","Delivery Date","Delivery Qty"]].copy()
            st.caption("Current PO/LOT selection for each item.")
            st.dataframe(audit, use_container_width=True, height=380)

        # -------------------- Excel Output with formulas + conditional formatting --------------------
        output = BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter", datetime_format="yyyy-mm-dd", date_format="yyyy-mm-dd") as writer:
            df.to_excel(writer, index=False, sheet_name="Inventory Report")
            audit = client_updated[["ITEM #","PO/LOT","Delivery Date","Delivery Qty"]].copy()
            audit.to_excel(writer, index=False, sheet_name="PO Picks")

            wb = writer.book
            ws = writer.sheets["Inventory Report"]

            # Named constants for targets
            wb.define_name("TargetStock",  f"={float(target_stock_months)}")
            wb.define_name("TargetSupply", f"={float(target_supply_months)}")

            def col_letter(idx: int) -> str:
                s = ""
                idx += 1
                while idx:
                    idx, r = divmod(idx - 1, 26)
                    s = chr(65 + r) + s
                return s

            nrows = len(df) + 1
            col_idx = {c: i for i, c in enumerate(df.columns)}
            colL = {c: col_letter(i) for c, i in col_idx.items()}
            def has(*cols): return all(c in col_idx for c in cols)

            for r in range(2, nrows + 1):
                L = colL

                if has("Delivery Date","Delivery Week No."):
                    ws.write_formula(r-1, col_idx["Delivery Week No."],
                        f'=IF({L["Delivery Date"]}{r}="", "", ISOWEEKNUM({L["Delivery Date"]}{r}))')

                if "Today Week" in col_idx:
                    ws.write_formula(r-1, col_idx["Today Week"], "=ISOWEEKNUM(TODAY())")

                if has("Stock + Ordered","Total same item","Total On Hand","Total SO"):
                    ws.write_formula(r-1, col_idx["Stock + Ordered"],
                        f'=IFERROR({L["Total same item"]}{r}+{L["Total On Hand"]}{r}-{L["Total SO"]}{r},"")')

                if has("Weeks in Stock","Total On Hand","Total SO","Weekly Sales"):
                    ws.write_formula(r-1, col_idx["Weeks in Stock"],
                        f'=IFERROR(({L["Total On Hand"]}{r}-{L["Total SO"]}{r})/{L["Weekly Sales"]}{r},"")')

                if has("Months in Stock","Weeks in Stock"):
                    ws.write_formula(r-1, col_idx["Months in Stock"],
                        f'=IFERROR({L["Weeks in Stock"]}{r}/4.25,"")')

                if has("Under/Over","Months in Stock"):
                    ws.write_formula(r-1, col_idx["Under/Over"],
                        f'=IFERROR({L["Months in Stock"]}{r}-TargetStock,"")')

                if has("Weeks in Supply","Stock + Ordered","Weekly Sales"):
                    ws.write_formula(r-1, col_idx["Weeks in Supply"],
                        f'=IFERROR({L["Stock + Ordered"]}{r}/{L["Weekly Sales"]}{r},"")')

                if has("Months in Supply","Weeks in Supply"):
                    ws.write_formula(r-1, col_idx["Months in Supply"],
                        f'=IFERROR({L["Weeks in Supply"]}{r}/4.25,"")')

                if has("Under/Over2","Months in Supply"):
                    ws.write_formula(r-1, col_idx["Under/Over2"],
                        f'=IFERROR({L["Months in Supply"]}{r}-TargetSupply,"")')

                if has("Suggested Order Qty","Under/Over2","Weekly Sales"):
                    ws.write_formula(r-1, col_idx["Suggested Order Qty"],
                        f'=IFERROR(-{L["Under/Over2"]}{r}*4.25*{L["Weekly Sales"]}{r},"")')

                if has("To reach 0 (wks)","Stock + Ordered","Weekly Sales"):
                    ws.write_formula(r-1, col_idx["To reach 0 (wks)"],
                        f'=IFERROR(INT({L["Stock + Ordered"]}{r}/{L["Weekly Sales"]}{r}),0)')

                if has("Day 0","To reach 0 (wks)"):
                    ws.write_formula(r-1, col_idx["Day 0"],
                        f'=IF({L["To reach 0 (wks)"]}{r}="", "", TODAY()+{L["To reach 0 (wks)"]}{r}*7)')

                if has("Re order","Day 0"):
                    ws.write_formula(r-1, col_idx["Re order"],
                        f'=IF({L["Day 0"]}{r}="", "", {L["Day 0"]}{r}-77)')

                if has("Place order 1","Re order"):
                    ws.write_formula(r-1, col_idx["Place order 1"],
                        f'=IF({L["Re order"]}{r}="", "", {L["Re order"]}{r}-84)')

                if has("Before today","Place order 1"):
                    ws.write_formula(r-1, col_idx["Before today"],
                        f'=IF({L["Place order 1"]}{r}="", "", {L["Place order 1"]}{r}-TODAY())')

                if has("Re orders no.","Chosen PO Qty","Weekly Sales"):
                    ws.write_formula(r-1, col_idx["Re orders no."],
                        f'=IFERROR(({L["Chosen PO Qty"]}{r}/{L["Weekly Sales"]}{r})*7,"")')

                if has("2","Place order 1","Re orders no."):
                    ws.write_formula(r-1, col_idx["2"],
                        f'=IF(OR({L["Place order 1"]}{r}="", {L["Re orders no."]}{r}=""), "", {L["Place order 1"]}{r}+{L["Re orders no."]}{r})')

                if has("3","2","Re orders no."):
                    ws.write_formula(r-1, col_idx["3"],
                        f'=IF(OR({L["2"]}{r}="", {L["Re orders no."]}{r}=""), "", {L["2"]}{r}+{L["Re orders no."]}{r})')

                if has("4","3","Re orders no."):
                    ws.write_formula(r-1, col_idx["4"],
                        f'=IF(OR({L["3"]}{r}="", {L["Re orders no."]}{r}=""), "", {L["3"]}{r}+{L["Re orders no."]}{r})')

            # Conditional formatting
            fmt_red    = wb.add_format({"bg_color": "#FF0000", "font_color": "#FFFFFF"})
            fmt_yellow = wb.add_format({"bg_color": "#FFFF00"})
            fmt_orange = wb.add_format({"bg_color": "#FFA500"})

            for col in ["Under/Over","Under/Over2"]:
                if col not in col_idx: continue
                cL = colL[col]; rng = f"{cL}2:{cL}{nrows}"
                ws.conditional_format(rng, {"type": "cell", "criteria": "<", "value": 0,   "format": fmt_red})
                ws.conditional_format(rng, {"type": "cell", "criteria": ">", "value": 1.5, "format": fmt_yellow})

            for col in [c for c in ["Place order 1","2","3","4"] if c in col_idx]:
                cL = colL[col]; rng = f"{cL}2:{cL}{nrows}"
                ws.conditional_format(rng, {"type": "formula", "criteria": f'={cL}2<TODAY()',                          "format": fmt_red})
                ws.conditional_format(rng, {"type": "formula", "criteria": f'=AND({cL}2>=TODAY(), {cL}2<=TODAY()+30)',  "format": fmt_orange})
                ws.conditional_format(rng, {"type": "formula", "criteria": f'=AND({cL}2>TODAY()+30, {cL}2<=TODAY()+90)',"format": fmt_yellow})

        output.seek(0)

        st.download_button(
            label="Download Excel report",
            data=output,
            file_name=f"inventory_report_{today.strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.warning("Please fix the errors above to see the report.")
else:
    st.info("⬆️ Upload both files to start.")



