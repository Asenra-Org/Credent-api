import os
import glob

replacements = {
    '"SuperAdmin"': '"SUPER_ADMIN"',
    '"Admin"': '"ORG_ADMIN"',
    '"Credit Manager"': '"UNDERWRITING_MANAGER"',
    '"Credit Analyst"': '"CREDIT_ANALYST"',
    '"Auditor"': '"VIEWER"'
}

for f in glob.glob('app/routes/*.py'):
    with open(f, 'r', encoding='utf-8') as file:
        content = file.read()
    
    for old, new in replacements.items():
        content = content.replace(old, new)
        
    with open(f, 'w', encoding='utf-8') as file:
        file.write(content)

print("Routes updated")
