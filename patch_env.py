env_path = r'D:\Credent\Credent-api\.env'
env_content = open(env_path, encoding='utf-8').read()
env_content = env_content.replace('LLM_MAX_TOKENS=800', 'LLM_MAX_TOKENS=4096')
open(env_path, 'w', encoding='utf-8').write(env_content)
print("Restored 4096 tokens.")
