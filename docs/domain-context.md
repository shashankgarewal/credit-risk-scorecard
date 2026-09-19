# Mortgage Banking & Credit Risk Domain Context

> A domain reference document covering mortgage mechanics, regulatory frameworks (Basel, IFRS 9, CECL), delinquency states, and statistical methodologies for defining default horizons in credit risk modeling.

---

## 1. Regulatory Frameworks: Basel, IFRS 9 & CECL

- **Basel II / III (Capital Adequacy & IRB Approach):** Mandates banking institutions to calculate **12-month Point-in-Time (PIT) or Through-the-Cycle (TTC) Probability of Default (PD)**, Loss Given Default (LGD), and Exposure at Default (EAD) to determine minimum regulatory capital requirements under advanced IRB approach.
- **IFRS 9 / CECL (Expected Credit Loss - ECL):** Replaced the incurred loss model with a forward-looking expected loss standard. Loans are categorized into three stages:
  - **Stage 1 (Performing):** Requires a **12-month ECL** calculation.
  - **Stage 2 (Significant Increase in Credit Risk - SICR):** Requires **Lifetime ECL** calculation across the remaining loan tenure.
  - **Stage 3 (Credit Impaired / In Default):** Requires Lifetime ECL based on net carrying amount.

---

## 2. Delinquency (DPD) vs. Default Definition

### Days Past Due (DPD)
A continuous measure tracking how many days a borrower is late on a contractual payment:
- **Current (0 DPD):** Paid on time.
- **30 DPD (1 month):** First missed payment; high cure/roll-back rate.
- **60 DPD (2 months):** Early-stage delinquency; transitioning risk.
- **90+ DPD (3 months):** Hard contractual breach; regulatory threshold where cure probability drops sharply.

### Regulatory & Industry Definition of Default
Under Basel and Freddie Mac conventions, a loan is formally marked **in default** upon hitting:
1. **90+ Days Past Due (3+ missed monthly payments), OR**
2. **Unlikeliness to Pay (UTP) / Loss Events:** Foreclosure initiation, Deed-in-Lieu, Short Sale, Bankruptcy, or Hard Distressed Loan Modification.

---

## 3. Choosing the Default Horizon: 12-Month vs. 24–36-Month vs. Lifetime

| Observation Horizon | Primary Use Case | Advantages | Trade-offs |
| :--- | :--- | :--- | :--- |
| **12-Month Horizon** | **Basel IRB & Standard Scorecards** | Fast feedback loop; allows including recent vintages; aligns with Basel 12-month PD regulations. | Misses long-term mortgage default peaks that occur in years 3–5. |
| **24-Month / 36-Month Horizon** | **Origination & Underwriting Scorecards** | Captures the **"seasoning peak"** where mortgage defaults historically concentrate (months 18–36 post-origination). | Requires excluding the most recent 2–3 years of originations due to maturity truncation. |
| **Lifetime / Multi-Period** | **IFRS 9 Stage 2 / Stress Testing** | Full lifecycle loss estimation; essential for macroeconomic scenario simulations. | Requires survival analysis or multi-state Markov transition matrices. |

---

