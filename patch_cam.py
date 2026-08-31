import re

def extract_json_from_reasoning(reasoning: str) -> str:
    # Try to find something that looks like JSON
    match = re.search(r'(\{[\s\S]+)', reasoning)
    if match:
        return match.group(1)
    return ""
    
print(extract_json_from_reasoning('Blah blah\n\n{\n  "name": "Karan",'))
