
import re

with open("app/agents/input/document_ingestion.py", "r", encoding="utf-8") as f:
    content = f.read()

new_content = content.replace(
    "print(f\"[CLEAN] Text sanitized: {len(raw_text)} -> {len(cleaned)} chars ({(1 - len(cleaned)/len(raw_text))*100:.1f}% reduction)\")",
    "print(f\"[CLEAN] Text sanitized: {len(raw_text)} -> {len(cleaned)} chars ({(1 - len(cleaned)/len(raw_text))*100:.1f}% reduction)\")\n          print(f\"========== PDF EXTRACTED TEXT ==========\\n{cleaned[:1000]}\\n========================================\")"
)

with open("app/agents/input/document_ingestion.py", "w", encoding="utf-8") as f:
    f.write(new_content)

