from text_processing import extract_keywords

print("===== Credit Keyword Matcher =====")

text = input("Enter financial text: ")

result = extract_keywords(text)

if result:
    print("\nDetected Keywords:")
    for keyword in result:
        print("-", keyword)
else:
    print("\nNo credit-risk keywords found.")