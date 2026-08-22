import os

filepath = "app/agents/orchestration/coordinator.py"
with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

old_block = """                    logger.warning(f"[ASE-63] Critical risk detected ({pause_reason}). Pausing pipeline for HITL review.")

                    # Store pause reason in the state so it persists in the snapshot
                    state.pause_reason = pause_reason
                    # Persist the snapshot along with the updated status
                    update_case_result(state.case_id, state.to_snapshot(), status="PAUSED")
                    logger.info("[ASE-63] Case %s PAUSED for manual review: %s", state.case_id, state.pause_reason)

                    # Return immediately to halt execution.
                    return {
                        "status": "paused",
                        "case_id": state.case_id,
                        "pause_reason": pause_reason,
                        "message": f"Pipeline paused for HITL review. {pause_reason}"
                    }"""

new_block = """                    logger.warning(f"[ASE-63] Critical risk detected ({pause_reason}). Forcing MANUAL REVIEW override.")
                    
                    forced_decision = "MANUAL REVIEW"
                    forced_rationale = f"Pipeline flagged for HITL review. {pause_reason}" """

content = content.replace(old_block, new_block)

with open(filepath, "w", encoding="utf-8") as f:
    f.write(content)

import py_compile
py_compile.compile(filepath)
print("Success!")
