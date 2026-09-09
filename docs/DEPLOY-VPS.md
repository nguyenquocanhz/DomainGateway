# Deploy Domain Gateway lên VPS

Hướng dẫn cho Ubuntu 22.04 / 24.04 hoặc Debian 12. Dùng Docker, mất khoảng 15 phút.

---

## Đọc cái này trước

**App không có đăng nhập.** Không có màn hình nhập mật khẩu, không có phân quyền. Ai gọi
được cổng 8787 đều xoá được tên miền, đọc được ghi chú, và đổi được ngưỡng cảnh báo.

Cho nên **đừng mở cổng 8787 ra Internet**. Ba cách truy cập an toàn, xếp theo thứ tự tôi
khuyên dùng:

| Cách | Hợp khi nào | Công sức |
|---|---|---|
| **SSH tunnel** | Chỉ mình bạn dùng, từ 1–2 máy | Không cài gì thêm |
| **nginx + Basic Auth + TLS** | Cần vào từ điện thoại, hoặc chia cho vài người | ~10 phút |
| **Tailscale / WireGuard** | Đã có sẵn VPN trong nhà | Tuỳ hệ thống |

Chi tiết từng cách ở mục [Truy cập](#truy-cập).

**Phải đặt ở gốc tên miền.** 16 lời gọi API trong `app.js` là đường dẫn tuyệt đối `/api/...`
và không có base URL cấu hình được. `dg.example.com` chạy được; `example.com/domain-gateway/`
thì **không** — trang tải xong nhưng mọi lời gọi API rơi vào `example.com/api/...` và trả 404.

---

## 1. Chuẩn bị VPS

Cấu hình tối thiểu thật sự cần: **1 vCPU, 512 MB RAM, 5 GB đĩa**. App gần như chỉ nằm chờ;
lúc tra cứu thì chờ mạng chứ không tốn CPU.

```bash
sudo apt update && sudo apt install -y ca-certificates curl
```

```bash
curl -fsSL https://get.docker.com | sudo sh
```

```bash
sudo usermod -aG docker $USER && newgrp docker
```

Đóng tường lửa, chỉ để SSH (và 80/443 nếu dùng nginx):

```bash
sudo ufw allow OpenSSH && sudo ufw enable && sudo ufw status
```

> **Không** `ufw allow 8787`. Cổng đó chỉ nghe ở `127.0.0.1` theo `docker-compose.yml`, ufw
> không đụng tới nó — nhưng mở ra là hỏng cả thiết kế.

---

## 2. Đưa mã lên

```bash
git clone <repo-của-bạn> ~/domain-gateway && cd ~/domain-gateway
```

Không dùng git thì `scp` cả thư mục lên. Nhớ **không** mang theo `config.json` và
`data/gateway.db` của máy dev — `.dockerignore` đã chặn chúng khỏi image, nhưng file vẫn nằm
trong thư mục nếu bạn copy tay.

---

## 3. Điền token

```bash
nano .env
```

> Không cần tạo `config.json` trên VPS. Container đọc/ghi nó ở
> `/app/data/config.json` trong volume (biến `DG_CONFIG`), và `.dockerignore`
> chặn file đó khỏi image — token đi vào bằng biến môi trường.

Nội dung `.env` (cạnh `docker-compose.yml`, đã bị git bỏ qua):

```
DG_TELEGRAM_TOKEN=123456789:AAH...
DG_TELEGRAM_CHAT=987654321
DG_CF_TOKEN=...
DG_BKNS_KEY=...
DG_WARN_DAYS=45
DG_CRITICAL_DAYS=14
```

Không bắt buộc điền hết. Thiếu token nào thì tính năng đó tắt, app vẫn chạy — RDAP và
WHOIS:43 không cần key gì cả.

```bash
chmod 600 .env
```

---

## 4. Chạy

```bash
docker compose up -d --build
```

Xem log lần đầu để chắc container không chết ngay:

```bash
docker compose logs -f
```

Kiểm tra sống:

```bash
curl -s localhost:8787/api/summary | head -c 200
```

---

## 5. Nạp dữ liệu lần đầu

```bash
docker exec domain-gateway python cli.py import data/domains.example.json
```

```bash
docker exec domain-gateway python cli.py refresh --all
```

> Lần đầu mất khoảng 90 giây nếu có tên miền `.vn`: key demo BKNS chỉ cho 2 request/phút.
> VPS có IP mới nên hạn mức tính lại từ đầu, không dính hạn mức của máy dev.

Kiểm tra:

```bash
docker exec domain-gateway python cli.py list
```

---

## Truy cập

### Cách 1 — SSH tunnel (khuyên dùng)

Không cài gì trên VPS. Chạy trên **máy của bạn**:

```bash
ssh -N -L 8787:127.0.0.1:8787 user@vps-cua-ban
```

Rồi mở <http://127.0.0.1:8787>. Đóng SSH là đóng luôn đường vào — không có cổng nào hở ra
Internet, không có mật khẩu nào để lộ.

Windows dùng PowerShell hoặc PuTTY (`Connection → SSH → Tunnels`, Source `8787`, Destination
`127.0.0.1:8787`).

### Cách 2 — nginx + Basic Auth + TLS

Cần một bản ghi A trỏ `dg.example.com` về IP VPS.

```bash
sudo apt install -y nginx apache2-utils certbot python3-certbot-nginx
```

```bash
sudo ufw allow 80,443/tcp
```

**Thứ tự quan trọng: xin chứng chỉ trước, nạp cấu hình đầy đủ sau.** Khối `listen 443 ssl`
mà chưa có `ssl_certificate` thì `nginx -t` báo lỗi ngay, nginx không khởi động lại được.

#### Bước 1 — khối tạm cho ACME

`/etc/nginx/sites-available/domain-gateway`:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name dg.example.com;
    root /var/www/html;
}
```

```bash
sudo ln -s /etc/nginx/sites-available/domain-gateway /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx
```

#### Bước 2 — xin chứng chỉ

```bash
sudo certbot certonly --webroot -w /var/www/html -d dg.example.com
```

Dùng `certonly` chứ không phải `--nginx`: nó chỉ lấy chứng chỉ, không tự sửa file cấu hình.
Bước sau mình tự viết cấu hình đầy đủ, khỏi phải gỡ những dòng certbot chèn vào.

#### Bước 3 — tài khoản đăng nhập

```bash
sudo htpasswd -c /etc/nginx/.htpasswd-dg admin
```

```bash
sudo chown root:www-data /etc/nginx/.htpasswd-dg && sudo chmod 640 /etc/nginx/.htpasswd-dg
```

#### Bước 4 — cấu hình đầy đủ

Ghi đè `/etc/nginx/sites-available/domain-gateway`:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name dg.example.com;

    # Để nguyên đường này KHÔNG chuyển hướng: certbot gia hạn 60 ngày một lần
    # và vẫn cần đọc được nó qua http.
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name dg.example.com;

    ssl_certificate     /etc/letsencrypt/live/dg.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/dg.example.com/privkey.pem;

    # HSTS là header DUY NHẤT nginx nên tự thêm: app chạy sau proxy nên không
    # biết mình đang được phục vụ qua TLS, không thể tự đặt cái này.
    add_header Strict-Transport-Security "max-age=31536000" always;

    # KHÔNG thêm X-Frame-Options / X-Content-Type-Options / CSP / Referrer-Policy
    # ở đây. App đã đặt đủ trong after_request; nginx add_header không gộp mà
    # đẻ ra header thứ hai, trình duyệt gặp hai giá trị khác nhau thì xử lý
    # khó đoán.

    # App tự chặn ở 1 MB và trả thông báo tiếng Việt. Để nginx rộng hơn một
    # chút thì lỗi đến từ app chứ không phải trang 413 trần của nginx.
    client_max_body_size 2m;

    access_log /var/log/nginx/dg-access.log;
    error_log  /var/log/nginx/dg-error.log;

    location / {
        auth_basic           "Domain Gateway";
        auth_basic_user_file /etc/nginx/.htpasswd-dg;

        proxy_pass http://127.0.0.1:8787;
        proxy_http_version 1.1;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Khớp với --timeout 120 của gunicorn. Xuất PDF vài trăm tên miền là
        # chỗ chậm nhất, còn lại đều trả về ngay.
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }

    gzip on;
    gzip_types application/json application/javascript text/css text/plain;
    gzip_min_length 1024;
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

Kiểm tra gia hạn tự động chạy được:

```bash
sudo certbot renew --dry-run
```

**Vẫn giữ `127.0.0.1:8787` trong `docker-compose.yml`.** nginx nói chuyện với app qua
loopback, cổng đó không cần hở ra ngoài.

**Đừng tắt việc chuyển tiếp header của client.** Lớp chặn CSRF của app đọc `Sec-Fetch-Site` —
header do chính trình duyệt đặt. nginx chuyển tiếp mặc định, nhưng nếu bạn thêm
`proxy_pass_request_headers off` thì mọi thao tác ghi sẽ hỏng theo kiểu rất khó đoán.

**App không dùng cookie hay session nào**, nên không phải cấu hình `proxy_cookie_path`, cũng
không có chuyện cookie thiếu cờ `Secure` khi chạy sau TLS.

> Basic Auth là lớp mỏng. Nó chặn được bot quét và người vô tình, không chặn được người quyết
> tâm. Dữ liệu ở đây không phải bí mật quốc gia, nhưng token Telegram và Cloudflare thì có
> trong `config.json` — cân nhắc kỹ trước khi mở ra Internet.

Thích Caddy hơn thì cũng được: nó tự lo TLS nên gọn hơn nhiều, nhưng phải thêm kho phần mềm.
nginx có sẵn trong apt của mọi VPS nên hướng dẫn này chọn nginx.

### Cách 3 — Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
```

Đổi cổng trong `docker-compose.yml` sang IP Tailscale của VPS (`100.x.y.z:8787:8787`), rồi vào
bằng địa chỉ đó. Không cần domain, không cần chứng chỉ, không hở cổng nào.

---

## Chạy định kỳ

Trang *Cài đặt* sinh lệnh `schtasks` cho Windows. Trên VPS thì dùng cron của máy chủ:

```bash
crontab -e
```

```cron
0 8 * * * docker exec domain-gateway python cli.py refresh >> /var/log/dg.log 2>&1
5 8 * * * docker exec domain-gateway python cli.py notify  >> /var/log/dg.log 2>&1
```

Tách `refresh` và `notify` ra hai dòng cách nhau 5 phút, đừng nối bằng `&&`: refresh chạy lâu
(BKNS 2 request/phút), mà `notify` cần dữ liệu đã mới. Nối chuỗi thì lỗi ở refresh sẽ nuốt
luôn notify.

Chạy thử ngay, đừng đợi 8h sáng mai:

```bash
docker exec domain-gateway python cli.py notify --dry-run
```

---

## Sao lưu

Volume `dg-data` giữ **cả SQLite lẫn `config.json`** — tức là cả token Telegram và Cloudflare.
Coi bản sao lưu như một file chứa mật khẩu.

Ghi ra **ngoài** thư mục repo — file này có token bên trong, để lẫn vào cây git là
sớm muộn cũng `git add -A` nhầm:

```bash
mkdir -p ~/dg-backup && docker run --rm -v dg-data:/data -v "$HOME/dg-backup":/sao alpine tar czf /sao/dg-$(date +%F).tar.gz -C /data .
```

```bash
chmod 600 ~/dg-backup/dg-*.tar.gz
```

Khôi phục:

```bash
docker compose down && docker run --rm -v dg-data:/data -v "$HOME/dg-backup":/sao alpine sh -c "rm -rf /data/* && tar xzf /sao/dg-2026-09-09.tar.gz -C /data" && docker compose up -d
```

File nhỏ (vài trăm KB), cứ chạy hằng tuần qua cron cũng được.

---

## Nâng cấp

```bash
git pull && docker compose up -d --build
```

Dữ liệu nằm ở volume nên không mất. Cột mới trong SQLite được `_nang_cap()` tự thêm lúc khởi
động — không phải chạy migration tay.

---

## Sự cố thường gặp

| Triệu chứng | Nguyên nhân | Xử lý |
|---|---|---|
| Chữ trong PDF vỡ hết | Thiếu font TTF trong image | Kiểm `docker exec domain-gateway ls /usr/share/fonts/truetype/dejavu/`. Trống thì build lại — `fonts-dejavu-core` trong Dockerfile chưa cài được |
| Ngày hết hạn **lệch đúng 1 ngày** | Container chạy giờ UTC | `docker exec domain-gateway date` phải ra giờ +7. Không thì thiếu `TZ` hoặc gói `tzdata` |
| Lưu token báo *"Không ghi được config.json"* | Ghi vào thư mục không có quyền | `docker exec domain-gateway printenv DG_CONFIG` phải ra `/app/data/config.json` |
| Thanh tiến độ tra cứu đứng im | Chạy nhiều gunicorn worker | `--workers 1` trong Dockerfile. Nhiều worker thì mỗi tiến trình giữ một `RefreshJob` riêng |
| Tên miền `.vn` không ra ngày | Hết hạn mức BKNS, hoặc `whois.vnnic.vn` timeout | Chờ hết ngày, hoặc xin key partner. VPS đặt ngoài VN đôi khi không gọi được `whois.vnnic.vn` — lúc đó BKNS là đường duy nhất |
| Trang tải được nhưng bảng trống, F12 thấy 404 | Đặt app dưới path con | Phải ở gốc (sub)domain — xem cảnh báo đầu bài |
| POST trả **403** *"Từ chối request chéo trang"* | Vào bằng origin khác với origin đã tải trang | Dùng đúng một địa chỉ. Đây là lớp chặn CSRF, không phải lỗi |
| `docker compose up` xong container tự tắt | Xem log | `docker compose logs --tail 50` |

---

## Giới hạn đã biết khi chạy trên VPS

- **Một worker duy nhất.** Không scale ngang được vì trạng thái tra cứu nằm trong bộ nhớ tiến
  trình. Với vài trăm tên miền thì thừa sức; muốn hàng nghìn thì phải đưa `RefreshJob` ra
  ngoài (Redis hoặc bảng trong SQLite).
- **SQLite, không phải Postgres.** Một tiến trình ghi tại một thời điểm. Đúng với kiểu dùng
  của app này (một người, ghi thưa), nhưng không hợp nếu nhiều người sửa cùng lúc.
- **Không có đăng nhập.** Đã nói ở đầu, nhắc lại vì đây là điều dễ quên nhất khi mọi thứ đã
  chạy ngon và bạn muốn "mở tạm ra cho tiện".
- **Key demo BKNS tính theo IP.** Chuyển VPS là hạn mức tính lại; ngược lại, nhiều app dùng
  chung một IP thì chia nhau 200 request/ngày.
