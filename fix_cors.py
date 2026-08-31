with open('app/main.py', 'r') as f:
    content = f.read()

content = content.replace('"http://localhost:5173",', '"http://localhost:5173",\n        "http://localhost:5174",\n        "http://localhost:4173",')

with open('app/main.py', 'w') as f:
    f.write(content)
