import sys
file_path = r'D:\Credent\Credent-api\app\agents\input\realtime_intelligence.py'
content = open(file_path, encoding='utf-8').read()

old_block = '''        try:
            self.structured_llm = self.llm.with_structured_output(ResearchReport, method="json_mode")
        except Exception as e:
            print(f"[WARN] Structured output init failed: {e}")
            self.structured_llm = None'''

new_block = '''        # Bypassed structured output to prevent Sarvam looping
        self.structured_llm = None'''

content = content.replace(old_block, new_block)
open(file_path, 'w', encoding='utf-8').write(content)
print("Patched realtime intelligence.")
