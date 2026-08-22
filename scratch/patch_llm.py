import os
import re

LLM_CONFIG_CONTENT = """import os
from langchain_groq import ChatGroq

def get_llm(temperature=0, api_key=None):
    if not api_key:
        api_key = os.getenv('GROQ_API_KEY')
        
    primary_model = os.getenv('PRIMARY_LLM_MODEL', 'openai/gpt-oss-20b')
    fallback_model_1 = os.getenv('FALLBACK_LLM_MODEL_1', 'qwen/qwen3.6-27b')
    fallback_model_2 = os.getenv('FALLBACK_LLM_MODEL_2', 'openai/gpt-oss-120b')
    
    primary_llm = ChatGroq(model=primary_model, temperature=temperature, api_key=api_key)
    fallback_llm_1 = ChatGroq(model=fallback_model_1, temperature=temperature, api_key=api_key)
    fallback_llm_2 = ChatGroq(model=fallback_model_2, temperature=temperature, api_key=api_key)
    
    return primary_llm.with_fallbacks([fallback_llm_1, fallback_llm_2])
"""

with open('app/core/llm.py', 'w') as f:
    f.write(LLM_CONFIG_CONTENT)

def patch_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    if 'from app.core.llm import get_llm' not in content and 'ChatGroq' in content:
        content = re.sub(
            r'(from langchain_groq import ChatGroq)',
            r'\1\nfrom app.core.llm import get_llm',
            content
        )
        pattern = r'self\.llm\s*=\s*ChatGroq\([^)]+\)'
        content = re.sub(pattern, 'self.llm = get_llm(temperature=0, api_key=api_key)', content)

        with open(filepath, 'w') as f:
            f.write(content)
        print(f'Patched {filepath}')

for root, dirs, files in os.walk('app/agents'):
    for file in files:
        if file.endswith('.py'):
            patch_file(os.path.join(root, file))
