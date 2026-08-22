
from json_repair import repair_json
import re
import json

text = """```json
{
  "document_control": {
    "borrower_name": "Test Sarvam Ltd"
  },
  "management": {
    "promoter_background":
"""

json_match = re.search(r"\{[\s\S]*\}", text)
if json_match:
    matched = json_match.group()
    print("MATCHED:", repr(matched))
    repaired = repair_json(matched)
    print("REPAIRED:", repr(repaired))
    try:
        parsed = json.loads(repaired)
        print("PARSED JSON:", parsed)
    except Exception as e:
        print("ERROR PARSING:", e)
else:
    print("NO MATCH")

