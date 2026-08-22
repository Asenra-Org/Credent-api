import re

with open("app/agents/orchestration/coordinator.py", "r", encoding="utf-8") as f:
    content = f.read()

def replacer(m):
    return """                    logger.info("[ASE-63] Forcing MANUAL REVIEW override.")
                    forced_decision = "MANUAL REVIEW"
                    forced_rationale = f"Pipeline flagged for HITL review. {pause_reason}" """

content = re.sub(r"# Store pause reason in the state.*?\}", replacer, content, flags=re.DOTALL)

with open("app/agents/orchestration/coordinator.py", "w", encoding="utf-8") as f:
    f.write(content)
