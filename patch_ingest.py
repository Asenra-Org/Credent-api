
import re

with open("app/agents/input/document_ingestion.py", "r", encoding="utf-8") as f:
    content = f.read()

# Add a print statement before self._extract_json_from_text
new_content = content.replace(
    "raw_response = raw_result.content if hasattr(raw_result, 'content') else str(raw_result)\n            parsed = self._extract_json_from_text(raw_response)",
    "raw_response = raw_result.content if hasattr(raw_result, 'content') else str(raw_result)\n            print(f'========== SARVAM RAW INGESTION RESPONSE ==========\\n{raw_response}\\n========================================')\n            parsed = self._extract_json_from_text(raw_response)"
)

with open("app/agents/input/document_ingestion.py", "w", encoding="utf-8") as f:
    f.write(new_content)

