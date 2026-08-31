import os
from langchain_groq import ChatGroq
from dotenv import load_dotenv
load_dotenv()
try:
    llm = ChatGroq(model='openai/gpt-oss-20b', api_key=os.getenv('GROQ_API_KEY'))
    print(llm.invoke('Hi').content)
except Exception as e:
    print('Error:', e)
