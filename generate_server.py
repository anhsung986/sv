import os
import sys
import requests
from google import genai
from google.genai import types
from pydantic import BaseModel

# Kiểm tra tham số truyền vào từ web
if len(sys.argv) < 3:
    print("Thiếu tham số! Cú pháp: python generate_server.py '<ý tưởng>' '<phiên bản>'")
    sys.exit(1)

USER_IDEA = sys.argv[1]
MC_VERSION = sys.argv[2]

# Lấy API Key được giấu bảo mật trong GitHub Secrets
GEMINI_API_KEY = os.environ.get("AI_API_KEY")

if not GEMINI_API_KEY:
    print("Lỗi: Không tìm thấy AI_API_KEY trong môi trường.")
    sys.exit(1)

# Tạo thư mục chứa các file .jar tải về
os.makedirs("plugins", exist_ok=True)

print(f"🤖 Đang phân tích ý tưởng: '{USER_IDEA}' cho phiên bản MC {MC_VERSION}...")

# Khởi tạo Gemini Client
client = genai.Client(api_key=GEMINI_API_KEY)

# 1. Định nghĩa cấu trúc JSON bắt buộc AI phải trả về dựa trên Pydantic
class PluginRecommendation(BaseModel):
    plugin_slug: str
    reason: str

class AIResponseStructure(BaseModel):
    plugins: list[PluginRecommendation]

# 2. Tạo nội dung yêu cầu gửi cho AI
prompt = f"""
Bạn là một chuyên gia tối ưu hóa server Minecraft.
Người dùng muốn tạo một server với ý tưởng: "{USER_IDEA}" chạy trên phiên bản {MC_VERSION}.
Hãy đề xuất danh sách từ 3 đến 8 plugin tốt nhất, phổ biến nhất trên Modrinth.
BẮT BUỘC: Điền chính xác 'plugin_slug' (tên viết liền, không dấu, ví dụ: 'essentialsx', 'worldedit').
"""

# 3. Gọi Gemini AI xử lý cấu trúc đầu ra
try:
    response = client.models.generate_content(
        model='gemini-2.0-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=AIResponseStructure,
        ),
    )
    
    # Giải mã dữ liệu JSON nhận được từ AI
    import json
    data = json.loads(response.text)
    recommended_plugins = data.get("plugins", [])
except Exception as e:
    print(f"❌ Lỗi khi gọi API Gemini: {str(e)}")
    sys.exit(1)

# Hàm tự động tìm và tải file từ Modrinth
def download_plugin(slug, version):
    try:
        search_url = f"https://api.modrinth.com/v2/project/{slug}/version"
        res = requests.get(search_url)
        if res.status_code != 200:
            print(f"⚠️ Không tìm thấy plugin '{slug}' trên Modrinth.")
            return False
            
        versions = res.json()
        target_file_url = None
        target_file_name = None
        
        for v in versions:
            if version in v['game_versions']:
                if v['files']:
                    target_file_url = v['files'][0]['url']
                    target_file_name = v['files'][0]['filename']
                    break
                    
        if target_file_url:
            print(f"📥 Đang tải {slug} ({target_file_name})...")
            file_res = requests.get(target_file_url)
            with open(f"plugins/{target_file_name}", "wb") as f:
                f.write(file_res.content)
            print(f"✨ Tải thành công {slug}!")
            return True
        else:
            print(f"⚠️ Plugin '{slug}' không có phiên bản phù hợp cho MC {version}.")
            return False
    except Exception as e:
        print(f"💥 Lỗi khi xử lý plugin {slug}: {str(e)}")
        return False

# 4. Chạy vòng lặp tải toàn bộ plugin được gợi ý
for item in recommended_plugins:
    slug = item['plugin_slug']
    reason = item['reason']
    print(f"\n📌 Gợi ý từ AI: {slug} -> Lý do: {reason}")
    download_plugin(slug, MC_VERSION)

print("\n🎉 Hoàn thành quá trình quét và tải plugin!")
