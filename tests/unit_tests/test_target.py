"""
Unit tests for data cleaning and OOP target building module (PD, LGD, EAD).
"""

import pytest
import polars as pl
from src.data.cleaning import DataCleaner, yyyymm_add_months, yyyymm_diff_months
from src.data.target import (
    PDTargetStrategy,
    LGDTargetStrategy,
    EADTargetStrategy,
    TargetFactory,
    CreditRiskTargetPipeline,
)


def test_yyyymm_helpers():
    assert yyyymm_add_months("202602", 12) == "202702"
    assert yyyymm_add_months("202611", 3) == "202702"
    assert yyyymm_diff_months("202702", "202602") == 12


def test_data_cleaner_delinquency():
    df_raw = pl.DataFrame({
        "current_loan_delinquency_status": ["XX", "00", "01", "03", "RA"]
    })
    cleaned = DataCleaner.clean_delinquency_status(df_raw)
    
    assert "delinquency_status_num" in cleaned.columns
    assert "is_delinquent_90" in cleaned.columns
    
    status_list = cleaned["delinquency_status_num"].to_list()
    assert status_list == [0, 0, 1, 3, 99]
    
    delinq_90 = cleaned["is_delinquent_90"].to_list()
    assert delinq_90 == [False, False, False, True, True]


def test_target_factory():
    pd_strat = TargetFactory.get_strategy("pd")
    lgd_strat = TargetFactory.get_strategy("lgd")
    ead_strat = TargetFactory.get_strategy("ead")
    
    assert isinstance(pd_strat, PDTargetStrategy)
    assert isinstance(lgd_strat, LGDTargetStrategy)
    assert isinstance(ead_strat, EADTargetStrategy)
    
    with pytest.raises(ValueError):
        TargetFactory.get_strategy("invalid_type")


def test_target_pipeline_execution():
    # Synthetic Origination Data
    df_org = pl.DataFrame({
        "loan_sequence_number": ["L001", "L002", "L003"],
        "first_payment_date": ["202603", "202603", "202603"],
        "original_upb": [200000.0, 300000.0, 150000.0],
    })

    # Synthetic Performance Data
    # L001: stays current
    # L002: defaults at 202605 with 90+ DPD
    # L003: zero balance default at 202606 with zero_balance_code=3
    df_perf = pl.DataFrame({
        "loan_sequence_number": ["L001", "L001", "L002", "L002", "L003", "L003"],
        "monthly_reporting_period": ["202602", "202605", "202602", "202605", "202602", "202606"],
        "current_actual_upb": [200000.0, 198000.0, 300000.0, 295000.0, 150000.0, 145000.0],
        "current_loan_delinquency_status": ["00", "00", "00", "03", "00", "00"],
        "zero_balance_code": [0, 0, 0, 0, 0, 3],
        "actual_loss_calculation": [0.0, 0.0, 0.0, 50000.0, 0.0, 30000.0],
    })

    pipeline = CreditRiskTargetPipeline(
        ground_period="202602",
        horizon_months=12
    )

    df_targets = pipeline.build_targets(df_perf, df_org)

    assert "loan_sequence_number" in df_targets.columns
    assert "target_pd" in df_targets.columns
    assert "target_lgd" in df_targets.columns
    assert "target_ead" in df_targets.columns

    pd_dict = dict(zip(df_targets["loan_sequence_number"], df_targets["target_pd"]))
    assert pd_dict["L001"] == 0
    assert pd_dict["L002"] == 1
    assert pd_dict["L003"] == 1
