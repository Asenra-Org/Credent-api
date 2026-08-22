import re

file_path = r"D:\coding\Credent-api\app\services\outbox_dispatcher.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace("class CeleryTransportAdapter(MessageBrokerTransport):\n\n(MessageBrokerTransport):\n", "class CeleryTransportAdapter(MessageBrokerTransport):\n")

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Syntax error fixed.")
