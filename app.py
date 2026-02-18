import re
from datetime import datetime, timedelta
import io

import pandas as pd
import pdfplumber
import streamlit as st

st.set_page_config(page_title="Shift Overlap Finder", layout="centered")

# Finds a date anywhere on the line (PDF often has extra text)
DATE_ANYWHERE_RE = re.compile(
    r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
    r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})"
)

# First time-range token on the line = MAIN SHIFT (ignore meal/skills later)
SHIFT_TOKEN_RE = re.compile(r"\+?\d{1,2}:\d{2}[AP]M-\d{1,2}:\d{2}[AP]M\+?")

def parse_date_from_line(line: str):
    m = DATE_ANYWHERE_RE.search(line)
    if not m:
        return None
    clean = f"{m.group(1)}, {m.group(2)} {m.group(3)}, {m.group(4)}"
    return datetime.strptime(clean, "%A, %B %d, %Y").date()

def clean_name(raw: str) -> str:
    """
    Converts coverage labels like:
      '024 - PAINT PAUL G'  -> 'PAUL G'
      '021 - CONSTRUCTION BRIAN C' -> 'BRIAN C'
    Also fixes leading bullets/dots like '. ALEJANDRO P' -> 'ALEJANDRO P'
    """
    s = raw.upper().strip()

    # Remove leading junk (bullets/dots/etc.)
    s = re.sub(r"^[^A-Z0-9]+", "", s)

    # Remove leading code like "024 - "
    s = re.sub(r"^\d+\s*-\s*", "", s)

    # Collapse spaces
    s = " ".join(s.split())

    # If it ends with an initial (single letter), keep "FIRST INITIAL" from the end
    parts = s.split()
    if len(parts) >= 2 and re.fullmatch(r"[A-Z]", parts[-1]):
        # keep last 2 tokens (e.g., PAUL G)
        return f"{parts[-2]} {parts[-1]}"

    # If it ends with 2 initials (rare), keep last 3 tokens (e.g., MARY A B)
    if len(parts) >= 3 and re.fullmatch(r"[A-Z]", parts[-1]) and re.fullmatch(r"[A-Z]", parts[-2]):
        return f"{parts[-3]} {parts[-2]} {parts[-1]}"

    # Fallback: keep last token(s) if no initial was found (not ideal but better than dept)
    if len(parts) >= 1:
        return parts[-1]

    return s

@st.cache_data(show_spinner=False)
def parse_pdf(file_bytes: bytes) -> pd.DataFrame:
    rows = []
    current_date = None

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for raw in text.splitlines():
                line = raw.strip()
                if not line:
                    continue

                d = parse_date_from_line(line)
                if d:
                    current_date = d
                    continue

                if not current_date:
                    continue

                mshift = SHIFT_TOKEN_RE.search(line)
                if not mshift:
                    continue

                # MAIN shift = first token only
                shift = mshift.group(0).replace("+", "")
                start_s, end_s = shift.split("-")

                name_raw = line[:mshift.start()].strip()
                name = clean_name(name_raw)
                if not name:
                    continue

                # skip obvious non-people headings
                bad_prefix = (
                    "NAME", "SHIFT", "TOTAL", "TIME PERIOD", "QUERY", "PAGE",
                    "FCST", "SCH", "O/U", "SVF"
                )
                if any(name.startswith(x) for x in bad_prefix):
                    continue

                start_t = datetime.strptime(start_s, "%I:%M%p").time()
                end_t   = datetime.strptime(end_s, "%I:%M%p").time()

                start_dt = datetime.combine(current_date, start_t)
                end_dt   = datetime.combine(current_date, end_t)
                if end_dt <= start_dt:
                    end_dt += timedelta(days=1)  # overnight

                rows.append({"date": current_date, "name": name, "start_dt": start_dt, "end_dt": end_dt})

    return pd.DataFrame(rows)

def overlap_days(by_date: dict, names: list[str], require_k: int = 3) -> pd.DataFrame:
    """
    Returns only YES days.
    require_k = 3 means all 3 must overlap.
    require_k = 2 means any 2 of them overlap (optional mode).
    """
    names = [clean_name(n) for n in names if n]
    uniq = list(dict.fromkeys(names))
    if len(uniq) < 3:
        return pd.DataFrame()

    out = []

    for d in sorted(by_date.keys()):
        per = by_date[d]  # index = name, cols start_dt/end_dt

        # only consider selected people who exist that date
        present = [n for n in uniq if n in per.index]
        if len(present) < require_k:
            continue

        # if require_k == len(uniq), require all selected present
        # else check combinations of size require_k
        if require_k == len(uniq):
            groups = [present]  # all
        else:
            # small list -> brute force combos is fine
            import itertools
            groups = list(itertools.combinations(present, require_k))

        best = None  # (latest_start, earliest_end, group)
        for group in groups:
            sub = per.loc[list(group)]
            latest_start = sub["start_dt"].max()
            earliest_end = sub["end_dt"].min()
            if latest_start < earliest_end:
                # keep the longest overlap
                dur = (earliest_end - latest_start).total_seconds()
                if (best is None) or (dur > (best[1] - best[0]).total_seconds()):
                    best = (latest_start, earliest_end, group)

        if best:
            latest_start, earliest_end, group = best
            out.append({
                "Day/Date": datetime.combine(d, datetime.min.time()).strftime("%a %m/%d/%Y"),
                "Common time": f"{latest_start.strftime('%-I:%M %p')} - {earliest_end.strftime('%-I:%M %p')}",
                "Duration (hrs)": round((earliest_end - latest_start).total_seconds()/3600, 2),
                "Who overlapped": ", ".join(group)
            })

    return pd.DataFrame(out)

st.title("Shift Overlap Finder")
st.caption("Upload schedule PDF → select 3+ employees → see ONLY the days they overlap. (Ignores meal/skills)")

pdf = st.file_uploader("Upload schedule PDF", type=["pdf"])
if not pdf:
    st.stop()

with st.spinner("Reading PDF..."):
    df = parse_pdf(pdf.read())

if df.empty:
    st.error("No shifts detected. If the PDF is scanned as images, it needs OCR.")
    st.stop()

# Precompute merged daily shift per person: earliest start + latest end
by_date = {}
for d, g in df.groupby("date"):
    per = g.groupby("name").agg(start_dt=("start_dt","min"), end_dt=("end_dt","max"))
    by_date[d] = per

names_all = sorted(df["name"].drop_duplicates().tolist())

st.subheader("Select employees (minimum 3)")
c1, c2, c3 = st.columns(3)
n1 = c1.selectbox("Employee 1", names_all)
n2 = c2.selectbox("Employee 2", names_all)
n3 = c3.selectbox("Employee 3", names_all)

extra = st.multiselect(
    "Optional: add more employees",
    [n for n in names_all if n not in {n1, n2, n3}]
)

selected = [n1, n2, n3] + extra

# Optional: if boss tries 3 people that truly never overlap, app can still be useful
mode = st.radio("Overlap requirement", ["All selected (strict)", "Any 2 of them (backup)"], horizontal=True)
require_k = len([x for x in selected if x]) if mode == "All selected (strict)" else 2

res = overlap_days(by_date, selected, require_k=require_k)

st.subheader("Overlap Results (YES days only)")
if res.empty:
    st.warning("No overlap days found with the current selection.")
else:
    st.dataframe(res, use_container_width=True)
    csv = res.to_csv(index=False).encode("utf-8")
    st.download_button("Download Results (CSV)", data=csv, file_name="overlap_results.csv", mime="text/csv")

with st.expander("Debug (optional)"):
    st.caption(f"Shifts read: {len(df)} | Dates: {df['date'].nunique()} | Employees: {df['name'].nunique()}")
    # Show a few names to confirm they look clean (no leading dots)
    st.write("Example employee names detected:")
    st.write(df["name"].drop_duplicates().head(25).tolist())
