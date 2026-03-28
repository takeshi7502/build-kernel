with open('web/js/bot.js', 'r', encoding='utf-8') as f:
    content = f.read()

old = "containerWeb.innerHTML += createBuildCard(build);"
new = "containerWeb.innerHTML += createWebBuildCard(build);"

if old in content:
    content = content.replace(old, new, 1)
    # Also fix empty state (remove grid-column)
    content = content.replace(
        'grid-column: 1 / -1; text-align: center; padding: 40px;">Đang tải dữ liệu hoặc chưa có bản web build nào...',
        'text-align: center; padding: 40px;">Đang tải dữ liệu hoặc chưa có bản web build nào...'
    )
    with open('web/js/bot.js', 'w', encoding='utf-8') as f:
        f.write(content)
    print("SUCCESS: bot.js patched")
else:
    print("ERROR: not found")
    print("Occurrences of createBuildCard:", content.count("createBuildCard"))
