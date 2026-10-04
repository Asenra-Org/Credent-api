import re
file_path = r'D:\Credent\Credent-api\app\agents\orchestration\cam_generator.py'
content = open(file_path, encoding='utf-8').read()

if 'import httpx' not in content:
    content = content.replace('import json', 'import json\nimport httpx')
    open(file_path, 'w', encoding='utf-8').write(content)
    print("Added import httpx")
else:
    print("httpx already imported")
