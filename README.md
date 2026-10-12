# Serverless Multimodal Nutrition & Macro Analytics Tracker

**Live Demo:** [https://macro-tracker-703981694118.us-west1.run.app](https://macro-tracker-703981694118.us-west1.run.app)

A stateful, serverless nutrition and macronutrient tracking platform deployed on **Google Cloud Run (Gen2)**. The application combines multimodal computer vision for nutrition label OCR, one-tap staple logging, vectorized 7-day rolling adherence analytics in **Polars**, and automated **SMTP-to-SMS** mobile notifications when daily targets are met.

---

## Architecture & Engineering Highlights

* **Structured Multimodal Vision OCR:** Uses the `google-genai` SDK and **Pydantic** response schemas to extract deterministic JSON (`calories`, `protein_g`, `carbs_g`, `fat_g`, and `serving_size`) directly from mobile camera captures of Nutrition Facts labels.
* **Zero-Database-Cost Serverless Persistence:** Persists relational state across container scale-to-zero lifecycles by mounting a **Google Cloud Storage** bucket via Cloud Storage FUSE (`/mnt/gcs/nutrition.db`) to a single-concurrency **SQLite (WAL)** instance.
* **Decoupled Container Boot Initialization:** Isolates database DDL, indexing (`idx_logs_user_date`), and initial data seeding into `init_db.py` executed at container startup, eliminating redundant schema-check I/O from the Streamlit request loop.
* **Vectorized Time-Series Analytics (Polars):** Constructs a contiguous 7-day calendar date spine left-joined against daily aggregations in **Polars** to compute rolling macro adherence percentages and target streaks without gaps on unlogged days.
* **Multi-Tenant Profiles & Idempotent SMS Alerting:** Supports multiple isolated user profiles with optional PIN protection and triggers automated mobile text alerts via carrier Email-to-SMS gateways backed by database idempotency locks (`sms_status`).

---

## Tech Stack

* **Backend & Analytics:** Python 3.12, Polars, Pydantic, SQLite3 (WAL mode)
* **AI / Computer Vision:** Google GenAI SDK (`gemini-2.5-flash` / `gemini-3-flash-preview`), Pillow
* **Frontend:** Streamlit
* **Cloud & Infrastructure:** Docker, GCP Cloud Run (Gen2 execution environment), Google Cloud Storage FUSE, Cloud Build, Artifact Registry

---

## Local Quickstart

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment variables
export GEMINI_API_KEY="your_gemini_api_key"
export SMTP_EMAIL="your_email@gmail.com"
export SMTP_APP_PASSWORD="your_16_char_app_password"
export SMS_GATEWAY_ADDRESS="5551234567@vtext.com"

# 3. Initialize SQLite schema & launch app
python init_db.py
streamlit run app.py