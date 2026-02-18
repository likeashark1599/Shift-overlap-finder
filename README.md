# Shift Overlap Finder (PDF -> overlap)

## What it does
Upload the weekly schedule PDF, pick 3+ employees, and it returns:
- Day/Date
- Common overlapping time window
- Duration (hours)

Includes **overnight** shifts (end time can be next day).

## Run locally (Windows / Mac)
1. Install Python 3.10+.
2. In this folder:
   - `pip install -r requirements.txt`
3. Run:
   - `streamlit run app.py`

It will open in a browser (usually http://localhost:8501).

## Deploy (recommended for your boss)
### Streamlit Community Cloud (free)
1. Create a GitHub repo with `app.py` and `requirements.txt`.
2. Go to Streamlit Cloud and deploy the repo.
3. Share the resulting link with your boss.
