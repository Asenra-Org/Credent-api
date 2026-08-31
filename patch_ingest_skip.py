import re
file_path = r'D:\Credent\Credent-api\app\agents\input\document_ingestion.py'
content = open(file_path, encoding='utf-8').read()

# Replace Attempt 1 block
pattern = r"# Attempt 1: Native Structured Output.*?# Attempt 2: Raw LLM"
new_content = re.sub(pattern, '# Attempt 1 bypassed\n        structured_error = "Bypassed"\n\n        # Attempt 2: Raw LLM', content, flags=re.DOTALL)

open(file_path, 'w', encoding='utf-8').write(new_content)
print("Regex patched.")
