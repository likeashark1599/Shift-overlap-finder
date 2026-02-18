import re
from datetime import datetime, timedelta
import io
import pandas as pd
import pdfplumber
import streamlit as st

st.set_page_config(page_title="Shift Overlap Finder", layout="centered")

DATE_RE = re.compile(r"Store\s+([A-Za-z]+,\s+[A-Za-z]+\s+\d{1,2},\s+\d{4})")
SHIFT_RE = re.compile(r"^([A-Z][A-Z\s'\-\.]+?)\s+(\d{1,2}:\d{2}[AP]M-\d{1,2}:\d{2}[AP]M)\b")

def norm_name(s: str) -> str:
    return " ".join(s.upper().split())

@st.cache_data(show_spinner=False)
def parse_pdf(file_bytes: bytes) -> pd.DataFrame:
    rows = []
    current_date = None

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for raw in text.splitlines():
                line = raw.strip()

                # Ignore meal & skills lines
                if "MEAL" in line.upper() or "SKILL" in line.upper():
                    continue

                mdate = DATE_RE.search(line)
                if mdate:
                    current_date = datetime.strptime(
                        mdate.group(1), "%A, %B %d, %Y"
                    ).date()
                    continue

                if not current_date:
                    continue

                m = SHIFT_RE.match(line)
                if not m:
                    continue

                name = norm_name(m.group(1))
                shift = m.group(2)
                start_s, end_s = shift.split("-")

                start_t = datetime.strptime(start_s, "%I:%M%p").time()
                end_t = datetime.strptime(end_s, "%I:%M%p").time()

                start_dt = datetime.combine(current_date, start_t)
                end_dt = datetime.combine(current_date, end_t)

                # Overnight fix
                if end_dt <= start_dt:
                    end_dt += timedelta(days=1)

                rows.append({
                    "date": current_date,
                    "name": name,
                    "start_dt": start_dt,
                    "end_dt": end_dt
                })

    return pd.DataFrame(rows)

def compute_overlap(by_date: dict, names: list[str]) -> pd.DataFrame:
    names = [norm_name(n) for n in names if n]
    uniq = list(dict.fromkeys(names))

    if len(uniq) < 3:
        return pd.DataFrame()

    out = []
    need = set(uniq)

    for d in sorted(by_date.keys()):
        per = by_date[d]

        # All selected must exist that day
        if not need.issubset(per.index):
            continue

        sub = per.loc[uniq]

        latest_start = sub["start_dt"].max()
        earliest_end = sub["end_dt"].min()

        if latest_start < earliest_end:
            out.append({
                "Day/Date": datetime.combine(d, datetime.min.time()).strftime("%a %m/%d/%Y"),
                "Common time": f"{latest_start.strftime('%-I:%M %p')} - {earliest_end.strftime('%-I:%M %p')}",
                "Duration (hrs)": round((earliest_end - latest_start).total_seconds()/3600, 2)
            })

    return pd.DataFrame(out)

st.title("Shift Overlap Finder")
st.caption("Upload the schedule PDF, select 3+ employees, and see when they overlap.")

pdf = st.file_uploader("Upload schedule PDF", type=["pdf"])
if not pdf:
    st.stop()

with st.spinner("Reading PDF..."):
    df = parse_pdf(pdf.read())

if df.empty:
    st.error("No shifts detected in the PDF.")
    st.stop()

# Precompute per-day merged shifts
by_date = {}
for d, g in df.groupby("date"):
    per = g.groupby("name").agg(
        start_dt=("start_dt", "min"),
        end_dt=("end_dt", "max")
    )
    by_date[d] = per

names_all = sorted(df["name"].drop_duplicates().tolist())

st.subheader("Select employees (minimum 3)")

col1, col2, col3 = st.columns(3)
n1 = col1.selectbox("Employee 1", names_all)
n2 = col2.selectbox("Employee 2", names_all)
n3 = col3.selectbox("Employee 3", names_all)

extra = st.multiselect(
    "Optional: add more employees",
    [n for n in names_all if n not in {n1, n2, n3}]
)

selected = [n1, n2, n3] + extra

res = compute_overlap(by_date, selected)

st.subheader("Overlap Results")

if res.empty:
    st.warning("No days found where ALL selected employees overlap.")
else:
    st.dataframe(res, use_container_width=True)

    csv = res.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download Results (CSV)",
        data=csv,
        file_name="overlap_results.csv",
        mime="text/csv"
    )
