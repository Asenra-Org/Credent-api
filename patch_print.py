
import re

with open("app/agents/orchestration/cam_generator.py", "r", encoding="utf-8") as f:
    content = f.read()

new_content = content.replace(
    "print(f\"========== SARVAM RAW RESPONSE ==========\\n{res.content}\\n========================================\")",
    "print(f\"========== SARVAM RAW RESPONSE ==========\\n{res.content}\\nMetadata: {res.response_metadata}\\n========================================\")"
)

with open("app/agents/orchestration/cam_generator.py", "w", encoding="utf-8") as f:
    f.write(new_content)

