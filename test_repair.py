import json_repair

s = '''{
  "document_control": {
    "borrower_name": "Test Co",
    "case_id": "CRESEM-XXXX",
    "appraisal_date": "NOT PROVIDED",
    "status": "PENDING",
    "version": "v1.0"
  },
  "executive_s'''

res = json_repair.repair_json(s)
print(res)
