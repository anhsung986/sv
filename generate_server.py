import os
import sys
import json
import requests
from google import genai
from google.genai import types

# 1. Khởi tạo cấu hình ban đầu
# Lấy các tham số truyền từ giao diện Web thông qua GitHub Actions
if len(sys.argv) < 3:
    print("Thiếu tham số! Cú pháp: python generate_server.py '<ý_tưởng>' '<phiên_bản>'")
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

# 2. Gọi Gemini AI để phân tích ý tưởng và chọn tên plugin
print(f"🤖 Đang phân tích ý tưởng: '{USER_IDEA}' cho phiên bản MC {MC_VERSION}...")

client = genai.Client(api_key=GEMINI_API_KEY)

# Định nghĩa cấu trúc JSON bắt buộc AI phải trả về để code dễ đọc
class PluginRecommendation(types.BaseModel):
    plugin_slug: str
    reason: str

class AIResponseStructure(types.BaseModel):
    plugins: list[PluginRecommendation]

prompt = f"""
Bạn là một chuyên gia tối ưu hóa server Minecraft.
Người dùng muốn tạo một server với ý tưởng: "{USER_IDEA}" chạy trên phiên bản Minecraft: "{MC_VERSION}".
Hãy đề xuất danh sách từ 3 đến 8 plugin tốt nhất, phổ biến nhất trên Modrinth phù hợp với ý tưởng này.
BẮT BUỘC: Điền chính xác 'plugin_slug' (tên viết liền, không dấu, ví dụ: 'essentialsx', 'worldedit', 'luckperms') để có thể tìm kiếm được trên Modrinth API.
"""

response = client.models.generate_content(
    model='gemini-2.5-flash', # Model tối ưu tốc độ và chi phí cho năm 2026
    contents=prompt,
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=AIResponseStructure,
        temperature=0.3
    ),
)

# Chuyển đổi kết quả từ AI thành object trong Python
ai_result = json.loads(response.text)
recommended_plugins = ai_result.get("plugins", [])

print(f"✅ AI đã đề xuất {len(recommended_plugins)} plugin phù hợp.")

# 3. Lên Modrinth API để tìm file tải tương thích với phiên bản Minecraft
def download_plugin(slug, mc_version):
    # Tìm kiếm project ID của plugin thông qua slug
    search_url = f"https://api.modrinth.com/v2/search?query={slug}&facets=[[\"project_type:plugin\ Antony\"]]"
    headers = {"User-Agent": "GitHub-Minecraft-AutoServer-Builder/1.0"}
    
    try:
        search_res = requests.get(search_url, headers=headers).json()
        if not search_res['hits']:
            print(f"❌ Không tìm thấy plugin '{slug}' trên Modrinth.")
            return False
        
        project_id = search_res['hits'][0]['project_id']
        project_title = search_res['hits'][0]['title']
        
        # Lấy danh sách các phiên bản (versions) của plugin đó
        version_url = f"https://api.modrinth.com/v2/project/{project_id}/version"
        versions = requests.get(version_url, headers=headers).json()
        
        # Lọc tìm phiên bản hỗ trợ đúng bản Minecraft của người dùng
        target_file_url = None
        target_file_name = None
        
        for v in versions:
            if mc_version in v['game_versions']:
                # Lấy file .jar đầu tiên trong danh sách file của version đó
                if v['files']:
                    target_file_url = v['files'][0]['url']
                    target_file_name = v['files'][0]['filename']
                    break
        
        if target_file_url:
            print(f"📥 Đang tải {project_title} ({target_file_name})...")
            file_res = requests.get(target_file_url, headers=headers)
            with open(f"plugins/{target_file_name}", "wb") as f:
                f.write(file_res.content)
            print(f"✨ Tải thành công {project_title}!")
            return True
        else:
            print(f"⚠️ Plugin '{project_title}' không có phiên bản nào hỗ trợ Minecraft {mc_version}.")
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
