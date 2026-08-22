import os
import re

for root, dirs, files in os.walk('app/agents'):
    for file in files:
        if file.endswith('.py'):
            filepath = os.path.join(root, file)
            with open(filepath, 'r') as f:
                content = f.read()

            if 'from langchain_groq import ChatGroq' in content:
                content = content.replace('from langchain_groq import ChatGroq', 'from app.core.llm import ChatGroqWithFallback as ChatGroq')
                with open(filepath, 'w') as f:
                    f.write(content)
                print(f'Patched {filepath}')
