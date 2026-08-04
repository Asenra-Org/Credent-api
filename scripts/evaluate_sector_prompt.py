import asyncio
import os
import sys

# Allow importing the app package
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

print("GROQ KEY:", os.getenv("GROQ_API_KEY"))

from app.agents.input.document_ingestion import DocumentIngestionAgent

# -------------------------------------------------------------------
# Test Cases
# -------------------------------------------------------------------

TEST_CASES = [
    {
        "text": "ABC Motors manufactures passenger vehicles, commercial trucks and automobile spare parts.",
        "expected": "Automotive",
    },
    {
        "text": "Sunrise Hospital provides healthcare services including surgery, diagnostics and patient care.",
        "expected": "Healthcare",
    },
    {
        "text": "TechNova develops cloud software, AI platforms and cybersecurity products.",
        "expected": "Technology",
    },
    {
        "text": "Cotton Mills Ltd manufactures garments, fabrics and textile products.",
        "expected": "Textiles",
    },
    {
        "text": "LifeCare Pharma manufactures pharmaceutical medicines, vaccines and drug formulations.",
        "expected": "Pharmaceuticals",
    },
    {
        "text": "Mega Mart operates supermarkets, grocery stores and retail outlets.",
        "expected": "Retail",
    },
    {
        "text": "BuildInfra constructs highways, bridges and metro infrastructure projects.",
        "expected": "Infrastructure",
    },
    {
        "text": "Green Energy develops solar power plants and renewable energy projects.",
        "expected": "Energy",
    },
    {
        "text": "National Bank provides loans, deposits, investment banking and financial services.",
        "expected": "Banking and Financial Services",
    },
    {
        "text": "AgriFresh produces crops, seeds and agricultural products.",
        "expected": "Agriculture",
    },
]

# -------------------------------------------------------------------
# Normalize Sector Names
# -------------------------------------------------------------------

def normalize_sector(name: str) -> str:
    if not name:
        return ""

    mapping = {
        "automotive": "Automotive",

        "healthcare": "Healthcare",
        "pharmaceuticals": "Healthcare",
        "pharma": "Healthcare",

        "technology": "Technology",
        "information technology": "Technology",
        "it": "Technology",

        "textiles": "Textiles",
        "textile": "Textiles",

        "retail": "Retail",

        "infrastructure": "Infrastructure",
        "construction": "Infrastructure",

        "energy": "Energy",
        "renewable energy": "Energy",
        "power": "Energy",
        "oil & gas": "Energy",

        "banking": "Banking and Financial Services",
        "financial services": "Banking and Financial Services",
        "banking and financial services": "Banking and Financial Services",
        "bfsi": "Banking and Financial Services",

        "agriculture": "Agriculture",
        "agri": "Agriculture",
    }

    return mapping.get(name.strip().lower(), name.strip())


# -------------------------------------------------------------------
# Evaluation
# -------------------------------------------------------------------

async def evaluate():

    agent = DocumentIngestionAgent()

    total = len(TEST_CASES)
    passed = 0

    print("=" * 80)
    print("Sector Classifier Prompt Evaluation")
    print("=" * 80)

    for index, case in enumerate(TEST_CASES, start=1):

        print(f"\nTest {index}")

        result = await agent.parse_financial_statement(case["text"])

        predicted = result.get("sector", "Unknown")
        expected = case["expected"]

        predicted_norm = normalize_sector(predicted)
        expected_norm = normalize_sector(expected)

        success = predicted_norm == expected_norm

        if success:
            passed += 1

        print("Expected   :", expected)
        print("Predicted  :", predicted)
        print("Normalized :", predicted_norm)
        print("Result     :", "PASS" if success else "FAIL")

    accuracy = (passed / total) * 100

    print("\n" + "=" * 80)
    print(f"Correct  : {passed}/{total}")
    print(f"Accuracy : {accuracy:.2f}%")
    print("=" * 80)

    if accuracy >= 90:
        print("\nPASS ✅ Accuracy above 90%")
        sys.exit(0)
    else:
        print("\nFAIL ❌ Accuracy below 90%")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(evaluate())