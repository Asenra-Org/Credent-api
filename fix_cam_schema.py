
import re

with open('app/agents/orchestration/cam_generator.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix types to be Any to prevent Pydantic validation errors from LLM outputting ints instead of strings
content = content.replace('value: str', 'value: Any')
content = content.replace('page: str', 'page: Any')

# Let's also simplify the EvidenceRegister instruction so Groq handles it better
content = content.replace(
    '3. EVIDENCE TRACEABILITY: Every major financial number MUST map to an EvidenceItem in the evidence_register. DO NOT use \\'id\\' or \\'description\\'. Use EXACTLY the schema fields: finding, value, source_document, page, status.',
    '3. EVIDENCE TRACEABILITY: Add 1 or 2 items to the evidence_register to prove key numbers. Keep it short.'
)

with open('app/agents/orchestration/cam_generator.py', 'w', encoding='utf-8') as f:
    f.write(content)

