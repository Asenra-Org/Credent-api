import os
import glob

replacements = {
    '"SuperAdmin"': '"SUPER_ADMIN"',
    '"Admin"': '"ORG_ADMIN"',
    '"Credit Manager"': '"UNDERWRITING_MANAGER"',
    '"Credit Analyst"': '"CREDIT_ANALYST"',
    '"Auditor"': '"VIEWER"'
}

for root, _, files in os.walk('tests'):
    for file in files:
        if file.endswith('.py'):
            f = os.path.join(root, file)
            with open(f, 'r', encoding='utf-8') as file_obj:
                content = file_obj.read()
            
            for old, new in replacements.items():
                content = content.replace(old, new)
                
            with open(f, 'w', encoding='utf-8') as file_obj:
                file_obj.write(content)

print("Tests updated")
