import re
from datetime import datetime, timedelta
import io

import pandas as pd
import pdfplumber
import streamlit as st

st.set_page_config(page_title="Shift Overlap Finder", layout="centered")

# Works with the provided "Store <Day, Month dd, yyyy>" pattern
DATE_RE = re.compile(r"Store\s+([A-Za-z]+,\s+[A-Za-z]+\s+\d{1,2},\s+\d{4})")

# Lines like: "ALEX L  9:00PM-6:00AM"
SHIFT_RE = re.compile(r"^([A-Z][A-Z\s'\-\.]+?)\s+(\d{1,2}:\d{2}[AP]M-\d{1,2}:\d{2}[AP]M)\b")

def norm_name(s: str) -> str:
    return " ".join(s.upper().split())

def parse_pdf(file_bytes: bytes) -> pd.DataFrame:
    rows = []
    current_date = None

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for raw in text.splitlines():
                line = raw.strip()

                mdate = DATE_RE.search(line)
                if mdate:
                    current_date = datetime.strptime(mdate.group(1), "%A, %B %d, %Y").date()
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
                end_t   = datetime.strptime(end_s, "%I:%M%p").time()

                start_dt = datetime.combine(current_date, start_t)
                end_dt   = datetime.combine(current_date, end_t)

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

def compute_overlap(df: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    names = [norm_name(n) for n in names if n]
    uniq = list(dict.fromkeys(names))  # preserve order, unique
    if len(uniq) < 3:
        return pd.DataFrame()

    out = []
    for d, g in df.groupby("date"):
        sub = g[g["name"].isin(uniq)]
        if sub["name"].nunique() < len(uniq):
            continue

        # If duplicates exist for the same employee/date, merge them
        per = sub.groupby("name").agg(start_dt=("start_dt","min"), end_dt=("end_dt","max")).reset_index()

        latest_start = per["start_dt"].max()
        earliest_end = per["end_dt"].min()

        if latest_start < earliest_end:
            out.append({
                "Day/Date": datetime.combine(d, datetime.min.time()).strftime("%a %m/%d/%Y"),
                "Overlap": "YES",
                "Common time": f"{latest_start.strftime('%-I:%M %p')} - {earliest_end.strftime('%-I:%M %p')}",
                "Duration (hrs)": round((earliest_end - latest_start).total_seconds()/3600, 2)
            })

    return pd.DataFrame(out)

st.title("Shift Overlap Finder")
st.caption("Sube el PDF del horario, elige 3+ nombres y te dice fecha + hora en común (incluye overnight).")

pdf = st.file_uploader("Upload schedule PDF", type=["pdf"])
if not pdf:
    st.stop()

with st.spinner("Reading PDF..."):
    df = parse_pdf(pdf.read())

if df.empty:
    st.error("No pude extraer turnos del PDF. Si el PDF es escaneado como imagen, habría que usar OCR.")
    st.stop()

names_all = sorted(df["name"].drop_duplicates().tolist())

st.subheader("Selecciona empleados (mínimo 3)")
c1, c2, c3 = st.columns(3)
n1 = c1.selectbox("Empleado 1", names_all, index=0)
n2 = c2.selectbox("Empleado 2", names_all, index=min(1, len(names_all)-1))
n3 = c3.selectbox("Empleado 3", names_all, index=min(2, len(names_all)-1))

extra = st.multiselect(
    "Opcional: añade más empleados",
    [n for n in names_all if n not in {n1, n2, n3}]
)

selected = [n1, n2, n3] + extra

res = compute_overlap(df, selected)

st.subheader("Resultados (todos seleccionados a la vez)")
if res.empty:
    st.warning("No hay coincidencias donde estén TODOS seleccionados trabajando el mismo día.")
else:
    st.dataframe(res, use_container_width=True)

# Export button
if not res.empty:
    csv = res.to_csv(index=False).encode("utf-8")
    st.download_button("Download results (CSV)", data=csv, file_name="overlap_results.csv", mime="text/csv")

with st.expander("Ver turnos detectados"):
    show = df.sort_values(["date","name"]).copy()
    show["start"] = show["start_dt"].dt.strftime("%m/%d %I:%M %p")
    show["end"]   = show["end_dt"].dt.strftime("%m/%d %I:%M %p")
    st.dataframe(show[["date","name","start","end"]], use_container_width=True)
