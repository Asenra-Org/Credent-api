
with open("app/core/llm.py", "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace(
    "max_tokens=kwargs.get(\"max_tokens\", None)",
    "max_tokens=kwargs.get(\"max_tokens\", None),\n            timeout=None,\n            max_retries=0"
)

with open("app/core/llm.py", "w", encoding="utf-8") as f:
    f.write(content)

