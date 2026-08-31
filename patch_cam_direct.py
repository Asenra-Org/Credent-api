import os
import re

file_path = r'D:\Credent\Credent-api\app\agents\orchestration\cam_generator.py'
content = open(file_path, encoding='utf-8').read()

imports = '''import json
import json_repair
from pydantic import BaseModel, Field
from typing import List, Optional, Any
from langchain_core.prompts import ChatPromptTemplate
from app.core.llm import active_provider
from app.agents.orchestration.case_state import CaseState
import httpx
import os'''

content = re.sub(r'import json\nimport json_repair.*?from app\.agents\.orchestration\.case_state import CaseState', imports, content, flags=re.DOTALL)

old_logic = '''        try:
            if self.structured_llm:
                chain = prompt | self.structured_llm
                result = await chain.ainvoke(invoke_params)
                
                # result is a CAMDocument Pydantic model
                data = result.model_dump()
                
                # To maintain compatibility with existing route assumptions:
                data["decision"] = data["recommendation"]["decision"]
                data["recommended_loan_amount"] = data["facility"]["requested_amount"]
                data["recommended_interest_rate"] = "TBD"
                data["decision_rationale"] = data["recommendation"]["rationale"]
                return data
            else:
                chain = prompt | self.llm
                res = await chain.ainvoke(invoke_params)
                # [P0-1] The CAM response embeds borrower financials verbatim.
                print(f"[CAM] LLM response received | chars={len(res.content or [])}")
                data = self._extract_json_from_text(res.content)
                data["decision"] = data.get("recommendation", {}).get("decision", "MANUAL REVIEW")
                data["recommended_loan_amount"] = data.get("facility", {}).get("requested_amount", "NOT PROVIDED")
                data["recommended_interest_rate"] = "TBD"
                data["decision_rationale"] = data.get("recommendation", {}).get("rationale", "N/A")
                return data'''

new_logic = '''        try:
            sarvam_key = os.getenv("SARVAM_API_KEY")
            if sarvam_key:
                # Direct API call to salvage reasoning_content from Sarvam
                print("[CAM] Using direct Sarvam API call to prevent truncation data loss...")
                messages = prompt.format_messages(**invoke_params)
                payload = {
                    "model": "sarvam-105b",
                    "messages": [{"role": m.type, "content": m.content} for m in messages],
                    "temperature": 0.1,
                    "max_tokens": int(os.getenv("LLM_MAX_TOKENS", 4000))
                }
                async with httpx.AsyncClient(timeout=180.0) as client:
                    resp = await client.post(
                        "https://api.sarvam.ai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {sarvam_key}", "Content-Type": "application/json"},
                        json=payload
                    )
                resp_data = resp.json()
                choice = resp_data.get("choices", [{}])[0].get("message", {})
                content_str = choice.get("content") or ""
                if not content_str and choice.get("reasoning_content"):
                    reasoning = choice.get("reasoning_content")
                    print(f"[CAM] Content empty. Salvaging from reasoning_content (len={len(reasoning)})...")
                    import re
                    match = re.search(r'(\\{[\\s\\S]+)', reasoning)
                    content_str = match.group(1) if match else reasoning
                
                print(f"[CAM] LLM response received | chars={len(content_str)}")
                data = self._extract_json_from_text(content_str)
            else:
                if self.structured_llm:
                    chain = prompt | self.structured_llm
                    result = await chain.ainvoke(invoke_params)
                    data = result.model_dump()
                else:
                    chain = prompt | self.llm
                    res = await chain.ainvoke(invoke_params)
                    print(f"[CAM] LLM response received | chars={len(res.content or [])}")
                    data = self._extract_json_from_text(res.content)
            
            # Formatting
            data["decision"] = data.get("recommendation", {}).get("decision", "MANUAL REVIEW")
            data["recommended_loan_amount"] = data.get("facility", {}).get("requested_amount", "NOT PROVIDED")
            data["recommended_interest_rate"] = "TBD"
            data["decision_rationale"] = data.get("recommendation", {}).get("rationale", "N/A")
            return data'''

content = content.replace(old_logic, new_logic)
open(file_path, 'w', encoding='utf-8').write(content)
print("Patched CAM generator.")
