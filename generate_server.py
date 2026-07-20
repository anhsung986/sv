import os
import sys
import time
import random
import requests
from google import genai
from google.genai import types
from google.genai import errors as genai_errors
from pydantic import BaseModel

# Header User-Agent theo yêu cầu của Modrinth API (họ có thể chặn/rate-limit
# các request không có User-Agent rõ ràng)
HEADERS = {
    "User-Agent": "MinecraftServerGenerator/1.0 (github-actions; contact: your-email@example.com)"
}


def call_with_retry(func, *args, max_retries=5, base_delay=2, is_gemini=False, **kwargs):
    """
    Gọi 'func' và tự động retry với exponential backoff + jitter khi gặp lỗi 429
    (Too Many Requests). Với Gemini, lỗi 429 thường có thêm gợi ý 'retryDelay' —
    ta cố gắng đọc nó nếu có, nếu không thì dùng backoff mặc định.
    """
    for attempt in range(1, max_retries + 1):
        try:
            return func(*args, **kwargs)
        except genai_errors.APIError as e:
            status = getattr(e, "code", None) or getattr(e, "status_code", None)
            is_429 = status == 429 or "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)
            if is_gemini and is_429 and attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
                print(f"⏳ Gemini rate limit (429). Thử lại lần {attempt}/{max_retries} sau {delay:.1f}s...")
                time.sleep(delay)
                continue
            raise
        except requests.exceptions.RequestException as e:
            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
                print(f"⏳ Lỗi mạng, thử lại lần {attempt}/{max_retries} sau {delay:.1f}s... ({e})")
                time.sleep(delay)
                continue
            raise
    return None

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

# 3. Gọi Gemini AI xử lý cấu trúc đầu ra (có retry khi bị 429)
try:
    response = call_with_retry(
        client.models.generate_content,
        model='gemini-2.0-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=AIResponseStructure,
        ),
        is_gemini=True,
    )

    # Giải mã dữ liệu JSON nhận được từ AI
    import json
    data = json.loads(response.text)
    recommended_plugins = data.get("plugins", [])
except genai_errors.APIError as e:
    status = getattr(e, "code", None) or getattr(e, "status_code", None)
    if status == 429 or "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
        print("❌ Đã bị Google Gemini giới hạn tốc độ (429) sau nhiều lần thử lại.")
        print("   Nguyên nhân thường gặp: dùng API key free-tier chạm quota RPM/RPD,")
        print("   hoặc nhiều workflow chạy song song dùng chung 1 key.")
        print("   Gợi ý: giãn cách các lần chạy workflow, nâng cấp gói trả phí,")
        print("   hoặc dùng nhiều key xoay vòng (round-robin) qua GitHub Secrets.")
    else:
        print(f"❌ Lỗi khi gọi API Gemini: {str(e)}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Lỗi khi gọi API Gemini: {str(e)}")
    sys.exit(1)

# Hàm tự động tìm và tải file từ Modrinth
def _get_with_backoff(url, max_retries=5, base_delay=2):
    """GET với retry/backoff riêng cho lỗi 429 của Modrinth."""
    for attempt in range(1, max_retries + 1):
        res = requests.get(url, headers=HEADERS, timeout=15)
        if res.status_code == 429:
            # Modrinth trả header Retry-After nếu có, ưu tiên dùng nó
            retry_after = res.headers.get("Retry-After")
            if retry_after:
                delay = float(retry_after)
            else:
                delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
            print(f"⏳ Modrinth rate limit (429). Thử lại lần {attempt}/{max_retries} sau {delay:.1f}s...")
            time.sleep(delay)
            continue
        return res
    return res  # trả về response 429 cuối cùng nếu vẫn thất bại sau max_retries


def download_plugin(slug, version):
    try:
        search_url = f"https://api.modrinth.com/v2/project/{slug}/version"
        res = _get_with_backoff(search_url)
        if res.status_code != 200:
            print(f"⚠️ Không tìm thấy plugin '{slug}' trên Modrinth (status {res.status_code}).")
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
            file_res = _get_with_backoff(target_file_url)
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
for idx, item in enumerate(recommended_plugins):
    slug = item['plugin_slug']
    reason = item['reason']
    print(f"\n📌 Gợi ý từ AI: {slug} -> Lý do: {reason}")
    download_plugin(slug, MC_VERSION)
    if idx < len(recommended_plugins) - 1:
        time.sleep(1)  # giãn nhẹ giữa các request để tránh bị rate-limit

print("\n🎉 Hoàn thành quá trình quét và tải plugin!")
