import pandas as pd
import numpy as np

# Default response when analysis cannot be performed
DEFAULT_INTEGRITY = {
    "status": "completed",
    "flags_detected": 0,
    "flags": [],
    "warning": None
}

class IntegrityVerificationAgent:
    def __init__(self):
        pass

    async def cross_validate(self, gst_data: list, bank_data: list) -> dict:
        """Cross-validate data across GST returns and bank statements with full error handling."""
        
        flags = []
        warnings = []

        # Validate inputs
        if not gst_data or not isinstance(gst_data, list) or len(gst_data) == 0:
            return {
                "status": "completed",
                "flags_detected": 0,
                "flags": [],
                "warning": "No GST data provided. Integrity check skipped."
            }

        if not bank_data or not isinstance(bank_data, list) or len(bank_data) == 0:
            return {
                "status": "completed",
                "flags_detected": 0,
                "flags": [],
                "warning": "No bank data provided. Integrity check skipped."
            }

        # Create DataFrames safely
        try:
            df_gst = pd.DataFrame(gst_data)
        except Exception as e:
            return {
                "status": "completed",
                "flags_detected": 0,
                "flags": [],
                "warning": f"Could not parse GST data: {str(e)}"
            }

        try:
            df_bank = pd.DataFrame(bank_data)
        except Exception as e:
            return {
                "status": "completed",
                "flags_detected": 0,
                "flags": [],
                "warning": f"Could not parse bank data: {str(e)}"
            }
        
        # 1. Revenue Inflation Check (only if required columns exist)
        if 'taxable_value' in df_gst.columns and 'type' in df_bank.columns and 'amount' in df_bank.columns:
            try:
                total_gst_sales = pd.to_numeric(df_gst['taxable_value'], errors='coerce').fillna(0).sum()
                
                bank_credits = df_bank[df_bank['type'] == 'CREDIT']
                total_bank_inflows = pd.to_numeric(bank_credits['amount'], errors='coerce').fillna(0).sum() if len(bank_credits) > 0 else 0
                
                denominator = max(float(total_gst_sales), 1.0)
                variance = abs(float(total_gst_sales) - float(total_bank_inflows)) / denominator
                
                if variance > 0.20:
                    flags.append({
                        "flag": "Revenue Discrepancy",
                        "severity": "HIGH" if variance > 0.4 else "MEDIUM",
                        "details": f"GST Sales ({total_gst_sales:,.0f}) differ from Bank Inflows ({total_bank_inflows:,.0f}) by {variance:.1%}"
                    })
            except Exception as e:
                warnings.append(f"Revenue inflation check failed: {str(e)}")
        else:
            missing_cols = []
            if 'taxable_value' not in df_gst.columns:
                missing_cols.append("gst_data.taxable_value")
            if 'type' not in df_bank.columns:
                missing_cols.append("bank_data.type")
            if 'amount' not in df_bank.columns:
                missing_cols.append("bank_data.amount")
            warnings.append(f"Revenue check skipped — missing columns: {', '.join(missing_cols)}")

        # 2. Circular Trading Detection (only if required columns exist)
        if 'type' in df_gst.columns and 'counterparty_gstin' in df_gst.columns and 'taxable_value' in df_gst.columns:
            try:
                gst_with_type = df_gst[df_gst['type'].isin(['SALE', 'PURCHASE'])]
                
                if len(gst_with_type) > 0:
                    sales_mask = gst_with_type['type'] == 'SALE'
                    purchase_mask = gst_with_type['type'] == 'PURCHASE'
                    
                    if sales_mask.any() and purchase_mask.any():
                        sales_by_entity = gst_with_type[sales_mask].groupby('counterparty_gstin')['taxable_value'].sum()
                        purchases_by_entity = gst_with_type[purchase_mask].groupby('counterparty_gstin')['taxable_value'].sum()
                        
                        intersection = sales_by_entity.index.intersection(purchases_by_entity.index)
                        for gstin in intersection:
                            try:
                                sale_amt = float(sales_by_entity[gstin])
                                purch_amt = float(purchases_by_entity[gstin])
                                if abs(sale_amt - purch_amt) / max(sale_amt, 1.0) < 0.05:
                                    flags.append({
                                        "flag": "Potential Circular Trading",
                                        "severity": "CRITICAL",
                                        "details": f"Identical buy/sell volume with GSTIN {gstin} (Amount: {sale_amt:,.0f})"
                                    })
                            except (ValueError, TypeError) as e:
                                warnings.append(f"Circular trading check error for {gstin}: {str(e)}")
            except Exception as e:
                warnings.append(f"Circular trading detection failed: {str(e)}")
        else:
            warnings.append("Circular trading check skipped — missing required GST columns (type, counterparty_gstin, taxable_value).")

        result = {
            "status": "completed",
            "flags_detected": len(flags),
            "flags": flags
        }
        
        if warnings:
            result["warnings"] = warnings
        
        return result