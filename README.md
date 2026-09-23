# Freddie Mac Home Loan Credit Risk Scorecards

Building an end-to-end credit risk modeling and analytics platform using Freddie Mac's Single-Family Loan-Level Dataset (1999–2026). The project implements an out-of-core ELT data pipeline, Probability of Default (PD) scorecards, Loss Given Default (LGD) models, macroeconomic stress testing, and cloud-native model deployment.

---

## 1. System Architecture & Pipeline Overview

The platform follows an ETL-oriented (Extract-Load-Transform) pipeline designed to process 50 million loans efficiently on consumer and cloud hardware:

```text
Freddie Mac Portal (Gated Portal Session)
    ↓
Data Ingestion (Session-authenticated HTTP streaming -> Local raw ZIP staging)
    ↓
Extraction & Parquet Conversion (Temp quarterly unpack -> Layout resolution -> Hive-partitioned Parquet)
    ↓
Load Cloud Storage Bucket (Google Cloud Storage - GCS)
    ↓
Data Transformation & Cleaning (Polars / DuckDB Out-of-Core Processing)
    ↓
Feature Engineering & WoE Binning (Monotonic Risk Drivers & Cohort Tracking)
    ↓
Credit Risk Scorecards & ML Challengers (Logistic Regression Scorecards, Monotonic LightGBM)
    ↓
Macroeconomic Stress Testing (Supervisory CCAR / DFAST Scenarios)
    ↓
Production Model Serving & Monitoring (FastAPI, Drift Detection, PSI/CSI Tracking)
```

---

## 2. Repository Documentation Map

Each documentation file has a single, non-overlapping responsibility:

| Document | Core Purpose | Key Question Answered |
| :--- | :--- | :--- |
| **[`README.md`](file:///e:/Projects/freddie%20mac%20credit%20risk/README.md)** | **System Overview & Architecture** | *What* does this project do, what is its architecture, and how is it structured? |
| **[`docs/decision.md`](file:///e:/Projects/freddie%20mac%20credit%20risk/docs/decision.md)** | **Engineering & Modeling Decision Log** | *Why* were specific architectural, algorithmic, and engineering choices made over alternatives? |
| **[`docs/domain-context.md`](file:///e:/Projects/freddie%20mac%20credit%20risk/docs/domain-context.md)** | **Mortgage Banking & Risk Domain Knowledge** | *What business rules, regulatory frameworks, and mortgage concepts* govern the modeling? |
| **`docs/data_profile.md`** *(planned)* | **Data Quality & Profiling Report** | *What is the state of the data* (null distributions, schema anomalies, vintage volume shifts)? |
| **`docs/production_spec.md`** *(planned)* | **Production Serving & Monitoring Spec** | *How and when* is the model executed, served, monitored, and retrained in production? |

---

## 3. Data Ingestion & Storage Layout

### Source Data
- **Dataset:** Freddie Mac Single-Family Loan-Level Dataset (1999 to present).
- **Scope:** 28 yearly archives containing 109 historical quarters (218 distinct datasets).
- **Dataset Types:**
  - **Origination:** Static loan-level attributes at acquisition (Credit Score, LTV, DTI, Loan Purpose, Occupancy, UPB).
  - **Performance:** Monthly loan repayment trajectories, delinquency states (`0`, `1`, `2` ... `RA`), modifications, forbearances, and liquidation outcomes.

### Partitioned Storage Scheme
Data is extracted and converted into columnar Parquet format using Hive partitioning to enable instant partition pruning:

```text
dataset/
├── raw/                                 # Staged yearly ZIP archives (1999–2026)
├── extract/
│   ├── origination/
│   │   └── year=YYYY/
│   │       └── quarter=Q/
│   │           └── data.parquet        # Clean columnar origination data (31-32 cols)
│   └── performance/
│       └── year=YYYY/
│           └── quarter=Q/
│               └── data.parquet        # Clean columnar monthly tracking (32-35 cols)
├── layout/                              # Official Freddie Mac Excel data dictionaries
└── manifest.json                        # Ingestion and conversion state metadata
```

---

## 4. Key Pipeline Components

- **[`script/download_dataset.py`](file:///e:/Projects/freddie%20mac%20credit%20risk/script/download_dataset.py):** Session-authenticated stream downloader with size validation and manifest tracking.
- **[`script/extract_dataset.py`](file:///e:/Projects/freddie%20mac%20credit%20risk/script/extract_dataset.py):** Bounded single-quarter unpacker, dynamic Excel layout parser, and streaming Polars Parquet converter.
- **[`src/utils/config.py`](file:///e:/Projects/freddie%20mac%20credit%20risk/src/utils/config.py):** Centralized path definitions and global project constants.
- **[`src/utils/logger.py`](file:///e:/Projects/freddie%20mac%20credit%20risk/src/utils/logger.py):** Standardized console and file logging utility.

---

## 5. Methodology & Modeling Scope

1. **Cohort & Vintage Analysis:** Evaluating loan default and prepayment curves across historical economic regimes (Pre-Crisis 2005–2007, Post-Crisis recovery 2012–2019, and COVID-19 stimulus era 2020–2021).
2. **Scorecard Development:**
   - Interpretable **Weight of Evidence (WoE)** Logistic Regression scorecards scaled to standard points (e.g. Base Score 600, PDO 20).
   - Challenger gradient boosting models (**LightGBM / XGBoost**) with monotonic constraints.
3. **Macroeconomic Stress Testing:** Stressing the credit portfolio under Federal Reserve supervisory scenarios (CCAR / DFAST Baseline, Adverse, Severely Adverse).

---

## 6. Quickstart

### Prerequisites
- Python 3.10+
- Virtual environment with Polars, DuckDB, PyArrow, OpenPyXL

### Setup & Execution
```bash
# Clone the repository
git clone https://github.com/shashankgarewal/credit-risk-scorecard.git
cd "credit-risk-scorecard"

# Create and activate virtual environment
python -m venv venv
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements_dev.txt

# Run dataset extraction and parquet conversion
python script/extract_dataset.py
```