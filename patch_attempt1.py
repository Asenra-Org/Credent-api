import sys
file_path = r'D:\Credent\Credent-api\app\agents\input\document_ingestion.py'
content = open(file_path, encoding='utf-8').read()

old_block = '''        # Attempt 1: Structured output
        if self.structured_llm:
            try:
                chain = prompt | self.structured_llm
                result = await chain.ainvoke({"text": truncated_text})
                parsed = result.model_dump()

                # NORMALIZE FINANCIALS
                fin_fields = ["total_revenue", "total_debt", "shareholder_equity", "current_assets", "current_liabilities", "ebitda", "pat"]
                for field in fin_fields:
                    parsed[field] = normalize_to_inr(parsed.get(field))

                parsed["citations"] = _clean_citations(parsed.get("citations"))
                parsed["extraction_degraded"] = False
                parsed["degradation_reason"] = None
                return parsed
            except Exception as e:
                structured_error = str(e)
                print(f"[PARSE] Structured output failed: {e}")'''

new_block = '''        # Attempt 1: Bypassed for Sarvam efficiency
        structured_error = "Bypassed"'''

if old_block in content:
    content = content.replace(old_block, new_block)
    open(file_path, 'w', encoding='utf-8').write(content)
    print("Patched Attempt 1 successfully.")
else:
    print("Block not found!")
