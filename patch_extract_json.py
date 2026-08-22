
import os

files_to_patch = [
    "app/agents/analysis/financial_health.py",
    "app/agents/analysis/risk_intelligence.py",
    "app/agents/analysis/sector_context.py",
    "app/agents/input/realtime_intelligence.py"
]

replacement = """
    def _extract_json_from_text(self, text: str) -> dict:
        from json_repair import repair_json
        import json
        import re
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            try: return json.loads(json_match.group())
            except: 
                try: return json.loads(repair_json(json_match.group()))
                except: pass
        try: return json.loads(repair_json(text))
        except: raise ValueError("No JSON found")
"""
replacement_static = """
    def _extract_json_from_text(text: str) -> dict:
        from json_repair import repair_json
        import json
        import re
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            try: return json.loads(json_match.group())
            except: 
                try: return json.loads(repair_json(json_match.group()))
                except: pass
        try: return json.loads(repair_json(text))
        except: raise ValueError("No JSON found")
"""

import re
for fpath in files_to_patch:
    if os.path.exists(fpath):
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Replace instance method
        content = re.sub(
            r"    def _extract_json_from_text\(self, text: str\) -> dict:.*?(?=    def |$)", 
            replacement, 
            content, 
            flags=re.DOTALL
        )
        # Replace static method
        content = re.sub(
            r"    def _extract_json_from_text\(text: str\) -> dict:.*?(?=    def |$)", 
            replacement_static, 
            content, 
            flags=re.DOTALL
        )
        
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Patched {fpath}")

