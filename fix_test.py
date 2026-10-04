import re

file_path = r'D:\Credent\Credent-api\tests\test_sector_classifier.py'
content = open(file_path, encoding='utf-8').read()

old_mock = '''        fake_model = MagicMock()
        fake_model.model_dump.return_value = fake_result

        fake_chain = AsyncMock()
        fake_chain.ainvoke.return_value = fake_model'''

new_mock = '''        import json
        fake_model = MagicMock()
        fake_model.content = json.dumps(fake_result)
        fake_model.model_dump.return_value = fake_result

        fake_chain = AsyncMock()
        fake_chain.ainvoke.return_value = fake_model'''

content = content.replace(old_mock, new_mock)
open(file_path, 'w', encoding='utf-8').write(content)
print("Patched test_sector_classifier.py")
