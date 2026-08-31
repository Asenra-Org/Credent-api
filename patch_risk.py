import sys
file_path = r'D:\Credent\Credent-api\app\agents\analysis\risk_intelligence.py'
content = open(file_path, encoding='utf-8').read()

old_block = '''        try:
            self.structured_llm = self.llm.with_structured_output(AdjustedRiskScore)
        except Exception as e:
            print(f"[WARN] Structured output init failed: {e}")
            self.structured_llm = None'''

new_block = '''        # Bypassed structured output to prevent token looping on Sarvam
        self.structured_llm = None'''

content = content.replace(old_block, new_block)

old_prompt = '''        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a Senior Risk Officer. 
            Your task is to take a purely quantitative credit score and adjust it up or down based on human field notes.
            If the field notes are highly negative (e.g. factory closed, fake addresses), slash the score aggressively.
            If the field notes are positive, you may slightly boost the score.
            Always provide a clear rationale for your adjustment."""),
            ("user", "Base Quantitative Score: {base_score}\\n\\nField Officer Notes: {qualitative_notes}")
        ])'''

new_prompt = '''        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a Senior Risk Officer. 
            Your task is to take a purely quantitative credit score and adjust it up or down based on human field notes.
            If the field notes are highly negative (e.g. factory closed, fake addresses), slash the score aggressively.
            If the field notes are positive, you may slightly boost the score.
            Always provide a clear rationale for your adjustment.
            
            IMPORTANT: Output ONLY valid JSON matching this schema exactly. DO NOT output any reasoning, markdown formatting, or text outside the JSON.
            {
              "original_score": 60,
              "adjusted_score": 50,
              "adjustment_rationale": "string",
              "critical_flags": ["string"]
            }"""),
            ("user", "Base Quantitative Score: {base_score}\\n\\nField Officer Notes: {qualitative_notes}")
        ])'''

content = content.replace(old_prompt, new_prompt)
open(file_path, 'w', encoding='utf-8').write(content)
print("Patched risk intelligence.")
