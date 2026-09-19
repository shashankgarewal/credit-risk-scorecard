# Engineering & Modeling Decisions

> A structured record of key architectural, engineering, and statistical decisions across the project lifecycle. Focuses strictly on the **why**, trade-offs, other experimented approaches, and lessons learned.

---

## 1. Ingestion: Session-Authenticated Streaming vs. Browser/Manual Downloads

**Status:** Completed

### Context & Problem
The dataset is gated behind login and dynamic session-based terms acceptance with 5 MBPS server side bandwidth limitation. Data was only downloadable through the Freddie Mac UI. 
In an authenticated sessions, both manual requests (`wget` / `curl`) using download url and authenticated requests with session tokens fail with HTTP 401/403 or access denied errors.

### Decision
Use programmatic HTTP requests carrying active session cookies to fetch dataset metadata and stream raw archives directly to disk with chunked progress monitoring.

### Other Experimented Approaches
1. **Manual browser download:** Impossible to orchestrate or schedule, tedious across 28+ yearly archives, observed higher failure rate due to DNS blocks and session timeouts.
2. **Browser UI automation (Selenium/Playwright):** Heavyweight, fragile against UI DOM changes, scrolling react div window makes automation slow and prone to missing files.

### Trade-offs
- **Gained:** Repeatable, scriptable, lightweight, supports automatic resume, and content-length for version update and integrity checks.
- **Gave up:** Authentication method for download may change, manual cookie refresh required, no resume capability and file corruption on network interruption.

### Lesson Learned
> For gated datasets, understanding authentication mechanism and api endpoints responsible for dataset info and download is more robust and maintainable than automating browser UI interactions.

Tip: For Freddie Mac data utilizing multiple DNS resolved the redirection and dns blocking issues.

---

## 2. Extraction: Bounded Single-Quarter Staging vs. In-Memory Streaming

**Status:** Completed

### Context & Problem
Data is distributed in nested archives of yearly ZIPs (`historical_data_YYYY.zip`) containing quarterly ZIPs (`historical_data_YYYYQQ.zip`) with raw TXT files upto 4 GB uncompressed with 10M+ rows. 
Attempting in-memory streaming (`io.BytesIO`) freezes the system for hours with no progress and eventually crashes OS. This is more concerning as python isn't raising any explicit error or out of memory warnings.

### Decision
Adopted bounded single-quarter temporary unpacking with explicit garbage collection and retry-wrapped directory removal (`safe_rmtree`) to handle Windows file-lock latencies. Extract one inner quarterly ZIP at a time into `mkdtemp`, process and sink to Parquet, detach temp files from memory, and immediately purge the folder.

### Other Experimented Approaches
1. **In-memory byte streaming (`io.BytesIO`):** Avoids disk writes, but exceeds RAM limits and lacks seek support needed for multi-threaded parsing.
2. **Full dataset extraction upfront:** Requires > 50 GB of free local disk space and causes massive I/O overhead.

### Trade-offs & Evidence
- **Gained:** Bounded peak disk footprint to a single quarter (~5 GB max) and kept RAM footprint strictly under 2 GB across all 28 years of data.
- **Gave up:** Incurs temporary disk write/delete cycles per quarter.

### Lesson Learned
> When handling multi-gigabyte nested archives, bound temporary storage to the smallest processing unit and provide physical seekable files for multi-threaded parser performance.

---

## 3. Ingestion Typing: Lazy String Sinks vs. Eager Type Inference

**Status:** Completed

### Context & Problem
Performance TXT files contain up to 70M+ rows per quarter with missing value markers (e.g. `.` in numeric columns) and schema evolutions across vintages. Eager parsing (`pl.read_csv`) spiked memory beyond 16 GB RAM, while lazy inference (`infer_schema_length`) risked guessing incorrect data types on late-appearing values.

### Decision
Enforce a uniform `pl.Utf8` schema during raw extraction and stream directly to disk using `sink_parquet(compression="snappy")`. All domain validation, numeric parsing, and type casting are deferred to downstream transformation.

### Other Experimented Approaches
1. **Eager loading with automatic type inference (`pl.read_csv`):** High memory consumption, prone to OOM, slow on 50M+ row files.
2. **Lazy scan with early type casting:** Fails mid-stream whenever dirty records, non-standard nulls (`.`), or unexpected characters appear.

### Trade-offs
- **Gained:** True out-of-core streaming execution with zero OOM risk, 100% preservation of raw source text without ingestion-time data loss.
- **Gave up:** Raw Parquet files remain string-typed, requiring explicit type casting downstream.

### Lesson Learned
> Decouple raw extraction from schema validation. Ingest raw data as clean strings in streaming batches, and perform strict type coercion in dedicated cleaning stages.

---

## 4. Layout Resolution: Dynamic Dictionary Discovery vs. Hardcoded Schemas

**Status:** Completed

### Context & Problem
Raw Freddie Mac TXT files lack header rows. Column counts evolved significantly over the 28-year timeline (Origination: 26 to 32 columns; Performance: 22 to 35 columns). Hardcoded column lists break across vintage boundaries.

### Decision
Implemented `LayoutManager` to discover Excel layouts (`file_layout.xlsx`) and dynamically match column names based on detected file column counts and dataset types.

### Other Experimented Approaches
1. **Hardcoding column lists per vintage range:** High maintenance burden and error-prone when Freddie Mac updates layout specs.
2. **Generating generic headers (`column_1`, `column_2`, ...):** Loses semantic meaning and requires manual mapping in downstream queries.

### Trade-offs
- **Gained:** Self-adapting header alignment across all 28 vintages; automatically handles historical layout revisions.
- **Gave up:** Requires valid layout Excel files to be present during ingestion.

---

## 5. Storage Architecture: Multi-Level Hive Partitioning vs. Monolithic Storage

**Status:** Completed

### Context & Problem
Downstream modeling and stress testing require filtering by specific historical periods (e.g. pre-crisis 2005–2007 vs. COVID-era 2020–2021). Monolithic files or single-vintage strings (`vintage=2020Q1.parquet`) prevent efficient date range queries and partition pruning.

### Decision
Store all extracted data under standard Hive partition paths:
`dataset/extract/<dataset_type>/year=<YYYY>/quarter=<Q>/data.parquet`

### Other Experimented Approaches
1. **Monolithic single files per dataset:** Extremely slow to query single quarters; requires reading unnecessary gigabytes.
2. **Flat vintage naming (`vintage=2020Q1/data.parquet`):** Limits query engines to string matching rather than hierarchical numerical slicing.

### Trade-offs
- **Gained:** Direct partition pruning in Polars, DuckDB, Spark, and BigQuery (e.g. `filter(pl.col("year") <= 2008)` skips reading later years completely).
- **Gave up:** Results in 218 individual Parquet files across the directory tree.

---

## 6. Pipeline State: Dual-Check Idempotency vs. Blind Re-execution

**Status:** Completed

### Context & Problem
Processing 28 years of data takes significant CPU time. Rerunning the pipeline must be fast and resilient: it should not re-extract completed quarters, yet must self-heal if files are deleted or if new data is published. Relying solely on `manifest.json` timestamps caused false skips when archives had partial extractions.

### Decision
Enforce dual verification across both the archive and quarterly levels: validate that `extract_utc >= download_utc` AND `output_parquet.exists()` AND all inner zip members are accounted for.

### Other Experimented Approaches
1. **Blind execution (always re-extract):** Wastes hours of compute on every run.
2. **Manifest-only timestamp checks:** Fast, but creates "ghost" records if a run failed midway or if files were deleted from disk.

### Trade-offs
- **Gained:** 100% idempotent and self-healing. Total scan time for up-to-date dataset is < 2 seconds.
- **Gave up:** Minor overhead of checking file existence on disk during startup.

---

## 7. Compute Placement: Local Extraction vs. Direct Cloud VM Processing

**Status:** Completed

### Context & Problem
Should raw archive downloading and Parquet extraction run directly on cloud VMs (e.g. GCP GCE / Dataproc) or on a local workstation?

### Decision
Perform download and conversion locally, then push clean Parquet files to Cloud Storage (GCS, AWS S3).

### Other Experimented Approaches
1. **Direct cloud VM extraction:** Download speed from Freddie Mac portal is throttled (5–10 MB/s), meaning expensive cloud compute sits idle waiting on network I/O. Additionally, storing 50+ GB raw zip files in cloud storage increases storage costs.
2. **Uploading raw ZIPs to GCS and extracting with serverless functions:** Zip sizes (up to 4 GB) exceed typical function memory/disk limits.

### Trade-offs
- **Gained:** Zero cloud compute costs for network-bound extraction; reduces cloud storage volume by ~90%.
- **Gave up:** Consumes local disk space and compute during the initial ingestion phase.

---

## 8. Data Transformation: Lazy Out-of-Core Processing Engine (Polars / DuckDB)

**Status:** Planned

### Context & Problem
Performance data exceeds 1.5 billion rows across the full history. In-memory dataframes (Pandas) cannot load this volume on a single workstation.

### Decision
Use Polars LazyFrames and DuckDB/PySpark for out-of-core transformation, filtering, and aggregation before feeding feature stores and modeling pipelines.

### Other Experimented Approaches
1. **Pandas with chunking:** High boilerplate, single-threaded bottlenecks, manual chunk aggregation logic.
2. **PySpark local cluster:** Heavy JVM overhead, complex local environment setup for single-node development.

---

## 9. Modeling: Interpretable Scorecard vs. Pure Black-Box Classifiers

**Status:** Planned

### Context & Problem
Credit risk applications (Basel / CECL / IFRS 9) require rigorous interpretability, monotonic risk behavior, and clear point allocation tables, whereas pure black-box ensembles (unconstrained XGBoost/Neural Nets) can violate monotonicity and fail regulatory audits.

### Decision
- **Primary / Benchmark:** Logistic Regression with monotonic Weight of Evidence (WoE) binning to generate an industry-standard Scorecard point table.
- **Challenger:** LightGBM with monotonic feature constraints and SHAP explainability.

### Other Experimented Approaches
1. **Unconstrained Deep Neural Networks:** Black-box nature makes regulatory adverse action notices and score calibration unfeasible.
2. **Pure unconstrained GBDT:** Can learn non-monotonic quirks from sample noise (e.g., predicting higher risk for higher credit scores in small subsets).

---

> ## Decision Log Summary
> | Stage | Decision | Choice Made | Other Experimented Approaches | Key Reason | Status |
> | :--- | :--- | :--- | :--- | :--- | :--- |
> | **Ingestion** | Authentication | Session-authenticated HTTP streaming | Browser UI automation | Scriptable, headless, and robust | Completed |
> | **Extraction** | Archive Unpacking | Bounded single-quarter temporary disk unpack | In-memory `io.BytesIO` streaming | Prevented OOM; bounded RAM to < 1.2 GB | Completed |
> | **Extraction** | OS File Handling | Retry-wrapped removal + explicit GC | Naked `shutil.rmtree` | Eliminated Windows file-lock `PermissionError` | Completed |
> | **Ingestion** | Typing & Parsing | Lazy `pl.scan_csv` with `pl.Utf8` schema | Eager `pl.read_csv` with type inference | Out-of-core streaming; zero data loss | Completed |
> | **Layout** | Header Mapping | Dynamic Excel layout resolution | Hardcoded column name lists | Handled schema variations across 28 years | Completed |
> | **Storage** | Partition Layout | Hive `year=YYYY/quarter=Q/data.parquet` | Monolithic flat naming | Enables native partition pruning | Completed |
> | **Pipeline** | Idempotency & State | Manifest timestamp + disk `.exists()` guard | Manifest-only timestamp check | Self-healing; guarantees zero false skips | Completed |
> | **Architecture** | Compute Placement | Local extraction -> GCS Parquet upload | Cloud VM direct download | Avoided cloud compute fees during slow download | Completed |
> | **Processing** | Transformation Engine | Polars / DuckDB out-of-core lazy execution | Pandas eager loading | Handles multi-gigabyte dataset efficiently | Planned |
> | **Modeling** | Scorecard Architecture | WoE Logistic Regression + Constrained GBDT | Pure unconstrained blackbox | Regulatory interpretability & monotonicity | Planned |
