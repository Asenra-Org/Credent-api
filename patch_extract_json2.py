
import os

files_to_patch = [
    "app/agents/analysis/financial_health.py",
    "app/agents/analysis/risk_intelligence.py",
    "app/agents/analysis/sector_context.py",
    "app/agents/input/realtime_intelligence.py"
]

for fpath in files_to_patch:
    if os.path.exists(fpath):
        with open(fpath, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        new_lines = []
        skip = False
        patched = False
        for line in lines:
            if line.startswith("    def _extract_json_from_text(self, text: str) -> dict:") or line.startswith("    def _extract_json_from_text(text: str) -> dict:"):
                skip = True
                is_static = "(text: str)" in line and "(self" not in line
                if is_static:
                    new_lines.append("    @staticmethod\n    def _extract_json_from_text(text: str) -> dict:\n")
                else:
                    new_lines.append("    def _extract_json_from_text(self, text: str) -> dict:\n")
                
                new_lines.append("        from json_repair import repair_json\n")
                new_lines.append("        import json\n")
                new_lines.append("        import re\n")
                new_lines.append("        json_match = re.search(r'\\{[\\s\\S]*\\}', text)\n")
                new_lines.append("        if json_match:\n")
                new_lines.append("            try: return json.loads(json_match.group())\n")
                new_lines.append("            except:\n")
                new_lines.append("                try: return json.loads(repair_json(json_match.group()))\n")
                new_lines.append("                except: pass\n")
                new_lines.append("        try: return json.loads(repair_json(text))\n")
                new_lines.append("        except: raise ValueError(\"No JSON found\")\n")
                patched = True
            elif skip and line.startswith("    def "):
                skip = False
                new_lines.append(line)
            elif not skip:
                new_lines.append(line)
                
        if patched:
            with open(fpath, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            print(f"Patched {fpath}")

