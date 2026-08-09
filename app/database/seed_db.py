import os
import sys
import time

# Ensure the 'app' module can be imported from the parent directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from app.database.database import init_db, save_appraisal

def run_seed():
    """Populates the database with realistic mock data for testing."""
    print("🌱 Starting Database Seeding Process...")

    # Ensure tables exist (will also print Supabase status)
    init_db()

    mock_approved_loan = {
        "company_name": "TechFlow Innovators Pvt Ltd",
        "company_id": "CMP_1001",
        "sector": "Software & SaaS",
        "revenue": "12.5 Cr",
        "debt": "1.2 Cr",
        "base_score": 850,
        "adjusted_score": 820,
        "decision": "APPROVE",
        "recommended_loan_amount": "5.0 Cr",
        "recommended_interest_rate": "10.5%",
        "decision_rationale": "Strong revenue growth and low debt-to-equity ratio offset minor market volatility concerns.",
        "raw_document_data": {
            "company_name": "TechFlow Innovators Pvt Ltd",
            "sector": "Software & SaaS",
            "extracted_revenue": 125000000
        },
        "integrity_flags": {
            "fraud_detected": False,
            "discrepancies": []
        },
        "web_research": {
            "sentiment": "Positive",
            "recent_news": ["TechFlow secures major government contract.", "SaaS sector seeing 20% YoY growth."]
        },
        "cam_report": {
            "company_name": "TechFlow Innovators Pvt Ltd",
            "summary": "Highly favorable credit profile with strong recurring revenue.",
            "risk_factors": ["High dependency on key engineering talent."]
        },
        "management_score": 85.5,
        "promoter_analysis": [
            {
                "name": "Arjun Malhotra",
                "experience_years": 15,
                "risk_flags": [],
                "verdict": "Clean record, high credibility"
            }
        ],
        "governance_assessment": {
            "board_independence": "Good",
            "regulatory_compliance": "Fully Compliant",
            "risk_level": "Low"
        }
    }

    mock_rejected_loan = {
        "company_name": "Globex Retail Solutions",
        "company_id": "CMP_1002",
        "sector": "Retail & Distribution",
        "revenue": "2.1 Cr",
        "debt": "4.5 Cr",
        "base_score": 450,
        "adjusted_score": 410,
        "decision": "REJECT",
        "recommended_loan_amount": "0",
        "recommended_interest_rate": "N/A",
        "decision_rationale": "Severe debt overload relative to revenue and declining sector performance.",
        "raw_document_data": {
            "company_name": "Globex Retail Solutions",
            "sector": "Retail & Distribution",
            "extracted_revenue": 21000000
        },
        "integrity_flags": {
            "fraud_detected": True,
            "discrepancies": ["Reported revenue conflicts with GST filings."]
        },
        "web_research": {
            "sentiment": "Negative",
            "recent_news": ["Globex facing lawsuit over unpaid vendor invoices.", "Retail foot traffic down 15%."]
        },
        "cam_report": {
            "company_name": "Globex Retail Solutions",
            "summary": "High-risk profile. Solvency is questionable in the near term.",
            "risk_factors": ["High debt burden", "Pending litigation", "Revenue mismatch"]
        },
        "management_score": 38.0,
        "promoter_analysis": [
            {
                "name": "Vijay Shah",
                "experience_years": 6,
                "risk_flags": ["PREVIOUS_DEFAULT: Promoter was director of defaulted entity Shah Plastics Ltd"],
                "verdict": "High risk flag detected"
            }
        ],
        "governance_assessment": {
            "board_independence": "Poor",
            "regulatory_compliance": "Non-compliant (late filings)",
            "risk_level": "High"
        }
    }

    mock_manufacturing_approved = {
        "company_name": "Acme Manufacturing Corp",
        "company_id": "CMP_1003",
        "sector": "Manufacturing",
        "revenue": "45.0 Cr",
        "debt": "15.0 Cr",
        "base_score": 750,
        "adjusted_score": 760,
        "decision": "APPROVE",
        "recommended_loan_amount": "8.0 Cr",
        "recommended_interest_rate": "11.0%",
        "decision_rationale": "Consistent cash flow and solid collateral backing justify the loan despite higher debt levels.",
        "raw_document_data": {
            "company_name": "Acme Manufacturing Corp",
            "sector": "Manufacturing",
            "extracted_revenue": 450000000
        },
        "integrity_flags": {
            "fraud_detected": False,
            "discrepancies": []
        },
        "web_research": {
            "sentiment": "Neutral",
            "recent_news": ["Acme opens new production facility in Gujarat."]
        },
        "cam_report": {
            "company_name": "Acme Manufacturing Corp",
            "summary": "Stable, mature business with reliable, asset-backed debt.",
            "risk_factors": ["Supply chain vulnerabilities to raw material costs."]
        },
        "management_score": 75.0,
        "promoter_analysis": [
            {
                "name": "Ramesh Patel",
                "experience_years": 22,
                "risk_flags": [],
                "verdict": "Experienced promoter, clear track record"
            }
        ],
        "governance_assessment": {
            "board_independence": "Adequate",
            "regulatory_compliance": "Fully Compliant",
            "risk_level": "Low"
        }
    }

    mock_logistics_under_review = {
        "company_name": "Zenith Logistics",
        "company_id": "CMP_1004",
        "sector": "Logistics & Supply Chain",
        "revenue": "8.4 Cr",
        "debt": "6.0 Cr",
        "base_score": 600,
        "adjusted_score": 580,
        "decision": "MANUAL REVIEW",
        "recommended_loan_amount": "TBD",
        "recommended_interest_rate": "TBD",
        "decision_rationale": "Borderline debt-to-equity ratio requires manual review by credit committee.",
        "raw_document_data": {
            "company_name": "Zenith Logistics",
            "sector": "Logistics & Supply Chain",
            "extracted_revenue": 84000000
        },
        "integrity_flags": {
            "fraud_detected": False,
            "discrepancies": ["Minor mismatch in projected vs actual operating costs."]
        },
        "web_research": {
            "sentiment": "Neutral",
            "recent_news": ["Zenith expands fleet with 50 new EV trucks."]
        },
        "cam_report": {
            "company_name": "Zenith Logistics",
            "summary": "Growing company but highly leveraged. Needs closer inspection of cash flow cycles.",
            "risk_factors": ["High operational costs", "Fuel price volatility"]
        },
        "management_score": 62.5,
        "promoter_analysis": [
            {
                "name": "Sanjay Verma",
                "experience_years": 10,
                "risk_flags": ["MINOR_DISPUTE: Ongoing civil dispute related to personal property"],
                "verdict": "Adequate track record, minor personal dispute"
            }
        ],
        "governance_assessment": {
            "board_independence": "Adequate",
            "regulatory_compliance": "Fully Compliant",
            "risk_level": "Medium"
        }
    }

    mock_healthcare_approved = {
        "company_name": "Horizon Healthcare Partners",
        "company_id": "CMP_1005",
        "sector": "Healthcare & Pharmaceuticals",
        "revenue": "28.0 Cr",
        "debt": "2.5 Cr",
        "base_score": 900,
        "adjusted_score": 910,
        "decision": "APPROVE",
        "recommended_loan_amount": "10.0 Cr",
        "recommended_interest_rate": "9.5%",
        "decision_rationale": "Exceptional financial health, low debt, and recession-proof sector.",
        "raw_document_data": {
            "company_name": "Horizon Healthcare Partners",
            "sector": "Healthcare & Pharmaceuticals",
            "extracted_revenue": 280000000
        },
        "integrity_flags": {
            "fraud_detected": False,
            "discrepancies": []
        },
        "web_research": {
            "sentiment": "Positive",
            "recent_news": ["Horizon wins national healthcare excellence award."]
        },
        "cam_report": {
            "company_name": "Horizon Healthcare Partners",
            "summary": "Prime borrower. Excellent liquidity and highly favorable market position.",
            "risk_factors": ["Regulatory changes in healthcare sector."]
        },
        "management_score": 92.0,
        "promoter_analysis": [
            {
                "name": "Dr. Sunita Sharma",
                "experience_years": 25,
                "risk_flags": [],
                "verdict": "Highly respected medical professional and entrepreneur"
            }
        ],
        "governance_assessment": {
            "board_independence": "Excellent",
            "regulatory_compliance": "Fully Compliant",
            "risk_level": "Low"
        }
    }

    payloads = [
        mock_approved_loan, 
        mock_rejected_loan,
        mock_manufacturing_approved,
        mock_logistics_under_review,
        mock_healthcare_approved
    ]

    success_count = 0
    fail_count = 0

    for idx, payload in enumerate(payloads):
        print(f"\n⏳ Seeding record {idx + 1}/{len(payloads)}: {payload['company_name']}...")
        try:
            # Reusing the exact pipeline used by the main API
            record_id = save_appraisal(payload)
            print(f"[OK] Successfully seeded record ID: {record_id}")
            success_count += 1
        except Exception as e:
            print(f"[ERROR] Failed to seed {payload['company_name']}: {e}")
            fail_count += 1
        
        # Mandatory 1-second sleep to prevent primary key collision (REC_{timestamp})
        if idx < len(payloads) - 1:
            time.sleep(1)

    print("\n========================================")
    print("🎉 Seeding complete. Testing environment is ready!")
    print(f"📊 Summary: {success_count} Successful | {fail_count} Failed | Total: {len(payloads)}")
    print("========================================")

if __name__ == "__main__":
    run_seed()
