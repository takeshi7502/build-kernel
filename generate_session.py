import os
import sys
from dotenv import load_dotenv

load_dotenv()

try:
    from telethon.sync import TelegramClient
    from telethon.sessions import StringSession
except ImportError:
    print("❌ LỖI: Thư viện Telethon chưa được cài đặt!")
    print("Hãy chạy lệnh: pip install telethon")
    sys.exit(1)

print("==============================================")
print("🤖 TẠO TELEGRAM STRING SESSION CHO USERBOT 🤖")
print("==============================================")
print("Bạn có thể tự lấy API_ID và API_HASH tại: https://my.telegram.org/apps")
print()

env_api_id = os.getenv("TELEGRAM_API_ID", "")
env_api_hash = os.getenv("TELEGRAM_API_HASH", "")

api_id_input = env_api_id if env_api_id else input("Nhập API_ID (Chỉ nhập số): ").strip()
api_hash_input = env_api_hash if env_api_hash else input("Nhập API_HASH (Chuỗi ký tự): ").strip()
phone_number = input("Nhập Số Điện Thoại (VD: +84987654321): ").strip()

if not api_id_input.isdigit() or not api_hash_input or not phone_number:
    print("❌ LỖI: Thông tin trống hoặc sai định dạng!")
    sys.exit(1)

print("\n📞 Đang kết nối tới máy chủ Telegram (Kiểm tra app Telegram để lấy mã OTP)...")
try:
    client = TelegramClient(StringSession(), int(api_id_input), api_hash_input)
    client.start(phone=phone_number)
    session_string = client.session.save()
    
    print("\n✅ ĐĂNG NHẬP THÀNH CÔNG VÀ ĐÃ KẾT XUẤT STRING SESSION!")
    print("Mã dưới đây đóng vai trò như CHÌA KHÓA NHÀ của bạn. AI CÓ MÃ NÀY LÀ CÓ TOÀN QUYỀN TÀI KHOẢN!")
    print("Tuyệt đối KHÔNG ĐƯỢC chia sẻ cho ai, KHÔNG ĐƯỢC up lên Github public.")
    print("\n" + "="*60)
    print(session_string)
    print("="*60 + "\n")
    print("👉 HƯỚNG DẪN: Copy toàn bộ đoạn mã lằng ngoằng trên.")
    print("👉 Mở file .env rỗng ra và điền vào dòng: TELEGRAM_STRING_SESSION=đoạn_mã_vừa_copy")
    
    client.disconnect()
except Exception as e:
    print(f"\n❌ LỖI TRONG QUÁ TRÌNH KHỞI TẠO: {e}")
