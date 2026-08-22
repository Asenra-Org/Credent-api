import re

with open("app/agents/orchestration/cam_generator.py", "r", encoding="utf-8") as f:
    content = f.read()

# Comment out structured LLM entirely
content = content.replace("self.structured_llm = self.llm.with_structured_output(CAMDocument, method=\"json_mode\")", "self.structured_llm = None")

with open("app/agents/orchestration/cam_generator.py", "w", encoding="utf-8") as f:
    f.write(content)

