
from json_repair import repair_json
import json

text = """```json
{
  "document_control": {
    "borrower_name": "Test Sarvam Ltd"
  },
  "management": {
    "promoter_background":
"""

try:
    repaired = repair_json(text)
    print("Repaired string:", repr(repaired))
    parsed = json.loads(repaired)
    print("Parsed JSON:", parsed)
except Exception as e:
    print(f"ERROR: {e}")

