# Domain Gateway — image để deploy
#
# docker build -t domain-gateway .
# docker run -d --name domain-gateway -p 127.0.0.1:8787:8787 -v dg-data:/app/data domain-gateway
#
# Bốn thứ trong file này là bắt buộc với ĐÚNG app này, không phải khuôn mẫu chung:
#   1. fonts-dejavu-core — xuất PDF cần font TTF thật. Font lõi của PDF không có
#      dấu tiếng Việt, và image slim thì không có font nào. Thiếu gói này là chữ
#      trong PDF vỡ hết. Xem FONT_CANDIDATES trong gateway/exporters.py.
#   2. TZ=Asia/Ho_Chi_Minh — app hiển thị ngày theo giờ địa phương (.astimezone()).
#      Container mặc định UTC nên ngày hết hạn lệch đúng một ngày với người ở +7.
#      Đây là lỗi đã dính một lần khi làm giao diện, không phải lo xa.
#   3. DG_CONFIG trỏ vào volume — /app do root sở hữu, tiến trình chạy bằng
#      tài khoản thường nên không tạo nổi file .tmp ở đó; lưu token từ trang
#      Cài đặt sẽ hỏng. Để trong volume thì vừa ghi được, vừa không mất
#      token mỗi lần dựng lại image.
#   4. Chỉ MỘT worker — xem CMD ở cuối file.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Ho_Chi_Minh \
    DG_CONFIG=/app/data/config.json

# fonts-dejavu-core: cho PDF có dấu tiếng Việt (xem ghi chú 1 ở trên)
# tzdata          : cho TZ ở trên có tác dụng
# ca-certificates : gọi HTTPS tới RDAP / BKNS / Telegram / Cloudflare
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         fonts-dejavu-core tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Cài dependency trước, copy mã sau: sửa code không phải cài lại thư viện
COPY requirements.txt .
RUN pip install -r requirements.txt \
    # gunicorn CHỈ có trong image, không nằm trong requirements.txt của app:
    # bản thân app không import nó, và chạy dưới Windows thì gunicorn không cài
    # được. Werkzeug dev server không dành cho môi trường thật.
    && pip install "gunicorn>=21.2,<24"

COPY . .

# Chạy dưới tài khoản thường. /app/data phải ghi được vì SQLite nằm ở đó.
RUN useradd --create-home --uid 10001 dg \
    && mkdir -p /app/data \
    && chown -R dg:dg /app/data
USER dg

# SQLite + lịch sử tra cứu nằm đây. KHÔNG mount thì xoá container là mất sạch.
VOLUME ["/app/data"]

EXPOSE 8787

# Dùng urllib thay vì curl: image slim không có curl, cài thêm chỉ để healthcheck
# thì phí. Timeout ngắn vì endpoint này chỉ đọc SQLite, không gọi mạng.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=4).status == 200 else 1)"

# MỘT worker, nhiều thread — không phải để tiết kiệm RAM:
#
#   - RefreshJob giữ trạng thái lần tra cứu trong BỘ NHỚ tiến trình, và khoá
#     "mỗi lúc chỉ một job". Nhiều worker là nhiều tiến trình, mỗi cái một khoá
#     riêng -> bấm "Tra cứu lại" có thể chạy song song mấy lần, đốt sạch quota
#     BKNS (2 request/phút với key demo).
#   - /api/refresh/status hỏi trạng thái đó. Nhiều worker thì request hỏi có thể
#     rơi vào tiến trình khác với tiến trình đang chạy job -> thanh tiến độ đứng im.
#
# Thread thì an toàn: Store dùng connection theo từng thread (threading.local).
CMD ["gunicorn", "--bind", "0.0.0.0:8787", \
     "--workers", "1", "--threads", "4", "--worker-class", "gthread", \
     "--timeout", "120", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "app:create_app()"]
