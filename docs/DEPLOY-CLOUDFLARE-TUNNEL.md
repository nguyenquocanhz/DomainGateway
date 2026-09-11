# Đưa Domain Gateway ra subdomain qua Cloudflare Tunnel + Access

Hướng dẫn cho Ubuntu 22.04 / 24.04 hoặc Debian 12, tên miền đã dùng DNS của Cloudflare.
Mất khoảng 20 phút. Kết quả: app chạy ở `https://ten-cua-ban.example.com`, có đăng nhập,
và **VPS không mở thêm một cổng nào** ngoài SSH.

Đây là cách thứ tư, song song với ba cách trong [DEPLOY-VPS.md](DEPLOY-VPS.md). Phần dựng
Docker và chạy container ở tài liệu đó vẫn áp dụng nguyên vẹn — bài này chỉ thay phần
"Truy cập".

---

## Vì sao chọn cách này

**App không có đăng nhập.** Đó là câu đầu tiên của DEPLOY-VPS.md và cũng là lý do của cả
bài này. Ai gọi được app đều xoá được tên miền và đọc được token trong trang Cài đặt. Nên
lớp xác thực bắt buộc phải do hạ tầng lo.

So với nginx + Basic Auth:

| | nginx + Basic Auth | Cloudflare Tunnel + Access |
|---|---|---|
| Cổng mở ra Internet | 80 và 443 | **không có cái nào** |
| Chứng chỉ TLS | certbot, nhớ gia hạn | Cloudflare lo |
| Đăng nhập | một mật khẩu dùng chung | từng email, thu hồi riêng được |
| Log ai vào lúc nào | không | có |
| Lộ IP gốc VPS | có | không |
| Phụ thuộc | không | tên miền phải dùng DNS Cloudflare |

Đánh đổi thật sự là dòng cuối: cách này buộc tên miền phải nằm ở Cloudflare. Nếu không,
quay lại đường nginx trong DEPLOY-VPS.md — nó vẫn đúng.

---

## Cần có trước

- Tên miền đã dùng nameserver của Cloudflare (`dig +short NS ten-mien.com` phải ra
  `*.ns.cloudflare.com`).
- Docker và container app đã chạy, nghe ở `127.0.0.1:8787` — xem
  [DEPLOY-VPS.md](DEPLOY-VPS.md) mục 1 đến 5.
- **Không** cần mở 80/443, **không** cần nginx, **không** cần certbot. Nếu đã lỡ mở thì
  đóng lại: `ufw delete allow 80,443/tcp`.

---

## Kiến trúc

```
Trình duyệt
    │  https://ten-cua-ban.example.com
    ▼
Cloudflare edge ── TLS, và Access chặn ngay ở đây
    │
    │  kết nối do VPS CHỦ ĐỘNG mở ra, không phải Cloudflare gọi vào
    ▼
VPS  (ufw: chỉ mở 22)
 ├─ container cloudflared ──┐  mạng nội bộ compose
 └─ container domain-gateway ◄┘  http://app:8787
```

Cloudflare không bao giờ gọi vào VPS. `cloudflared` mở kết nối đi ra, mọi request đi ngược
qua kết nối đó. Vì thế không có cổng nào để quét, và IP VPS không xuất hiện ở đâu cả.

---

## 1. Thêm service `cloudflared` vào compose

Tạo `docker-compose.override.yml` **cạnh** `docker-compose.yml`:

```yaml
services:
  cloudflared:
    # Ghim tag. :latest tự nhảy version, một bản lỗi là tunnel chết sau lần pull.
    image: cloudflare/cloudflared:2026.9.0
    container_name: domain-gateway-tunnel
    restart: unless-stopped
    # Token đi qua biến môi trường, không qua command: viết trong command thì token
    # hiện trong `docker inspect` và trong `ps` của mọi user trên máy.
    # Dấu :? để compose báo lỗi ngay nếu .env thiếu, thay vì chạy rồi mới hỏng.
    environment:
      TUNNEL_TOKEN: ${TUNNEL_TOKEN:?thieu TUNNEL_TOKEN trong .env}
    command: tunnel --no-autoupdate run
    depends_on:
      - app
```

**Vì sao file override chứ không sửa thẳng `docker-compose.yml`:**

- `docker compose` tự nạp file này, nên mọi lệnh trong tài liệu chạy y nguyên — không phải
  nhớ thêm `-f` ở mỗi lần gõ.
- `git pull` lúc nâng cấp không đụng độ, vì file gốc không bị sửa.
- Ai clone repo về mà không dùng Cloudflare thì vẫn chạy được bản thường.

File này đã nằm trong `.gitignore`: nó là cấu hình riêng của một máy cụ thể.

---

## 2. Tạo tunnel

Dashboard <https://one.dash.cloudflare.com> → **Networks** → **Tunnels** →
**Create a tunnel** → **Cloudflared** → đặt tên (ví dụ `domain-gateway-vps`) → **Save**.

Màn hình tiếp theo hiện lệnh cài cho từng hệ điều hành. Không cần chạy lệnh đó — chỉ copy
**token**, là chuỗi dài phía sau `--token`.

Thêm vào `.env` (file đã `chmod 600` từ bước deploy):

```bash
echo "TUNNEL_TOKEN=<dán-token-vào-đây>" >> .env
```

Chạy connector:

```bash
docker compose up -d && docker compose logs -f cloudflared
```

Log phải có `Registered tunnel connection` (thường 4 dòng, bốn kết nối tới bốn điểm khác
nhau). Dashboard đổi tunnel sang **HEALTHY**.

> Dòng cảnh báo `failed to sufficiently increase receive buffer size` là bình thường và
> không làm hỏng gì. Muốn hết thì đặt `net.core.rmem_max=7500000` trong `/etc/sysctl.conf`.

---

## 3. Thêm route

Dashboard → tunnel vừa tạo → tab **Routes** → **Add route**:

| Ô | Giá trị |
|---|---|
| Subdomain | tên bạn muốn, ví dụ `domain` |
| Domain | tên miền của bạn |
| Path | **để trống** |
| Type | `HTTP` |
| URL | `app:8787` |

Hai chỗ dễ sai, cả hai đều im lặng:

- **URL là `app:8787`, không phải `localhost:8787`.** `cloudflared` chạy trong container
  riêng; với nó `localhost` là chính nó, không phải container app. Điền sai thì trình
  duyệt nhận 502 và log cloudflared báo `connection refused`.
- **Path phải trống.** App bắt buộc nằm ở gốc (sub)domain: 16 lời gọi API trong `app.js`
  là path tuyệt đối `/api/...`. Đặt path con thì trang tải xong nhưng bảng trống và F12
  đầy 404.

Cloudflare tự tạo bản ghi CNAME trỏ về `<tunnel-id>.cfargotunnel.com`.

---

## 4. Bật Access — đừng bỏ bước này

Xong bước 3 là app đã ra Internet **mà chưa có đăng nhập nào**. Đừng rời máy giữa bước 3
và 4. Phải dừng thì:

```bash
docker compose stop cloudflared
```

Hostname không phải thứ bí mật: mọi tên miền có chứng chỉ TLS đều nằm trong log
Certificate Transparency công khai.

Dashboard → **Access controls** → **Applications** → **Add an application** →
**Self-hosted**:

| Ô | Giá trị |
|---|---|
| Application name | `Domain Gateway` |
| Session Duration | **24 hours** |
| Public hostname | đúng subdomain + domain ở bước 3, path để trống |

Ở **Login methods**, bật **One-time PIN** — Cloudflare gửi mã 6 số về email, không cần
dựng nhà cung cấp đăng nhập nào.

Tạo policy:

| Ô | Giá trị |
|---|---|
| Policy name | `Chu so huu` |
| Action | **Allow** |
| Include → Selector | **Emails** |
| Value | email của bạn |

Access mặc định **từ chối**: không khớp policy Allow nào thì không vào được, nên không cần
thêm policy Deny.

> Session 24 giờ không phải con số tuỳ tiện — xem mục sự cố ở cuối bài.

### Bật kiểm token ngay tại connector

Dashboard → tunnel → route → bật **Protect with Access**, chọn application vừa tạo.
`cloudflared` sẽ tự kiểm JWT trước khi chuyển request xuống app. Lớp thứ hai, phòng khi
cấu hình phía edge bị sửa nhầm.

---

## 5. Kiểm tra

Từ một máy bất kỳ, **chưa đăng nhập**:

```bash
curl -sI https://ten-cua-ban.example.com | head -3
```

Phải ra **302** kèm `location:` trỏ về `*.cloudflareaccess.com`.

**Ra 200 là Access chưa có tác dụng.** Dừng lại, kiểm application đã trỏ đúng hostname
chưa, đừng dùng tiếp.

Rồi mở trình duyệt ở cửa sổ ẩn danh: nhập email → nhận mã 6 số → vào được giao diện. Thử
thêm một tên miền rồi xoá đi — thao tác ghi phải chạy, không ra lỗi 403 *"Từ chối request
chéo trang"*.

---

## Sự cố thường gặp

| Triệu chứng | Nguyên nhân | Xử lý |
|---|---|---|
| **Bấm nút không phản hồi**, F12 báo bị CSP chặn | Phiên Access hết hạn giữa lúc trang đang mở. Fetch `/api/...` bị chuyển hướng sang `cloudflareaccess.com`, mà CSP của trang là `connect-src 'self'` nên trình duyệt chặn — không hiện màn hình đăng nhập, chỉ im lặng hỏng | **F5**. Đặt Session Duration dài (24h) để hiếm gặp. Đừng nới CSP của app để lách |
| `curl -sI` trả **200** thay vì 302 | Application chưa gắn đúng hostname | Kiểm lại hostname trong Access application |
| Trình duyệt báo **502** | Route điền `localhost:8787` | Sửa URL thành `app:8787` |
| Trang tải được nhưng **bảng trống**, F12 đầy 404 | Route có Path | Xoá Path đi, app phải ở gốc subdomain |
| Tunnel HEALTHY nhưng hostname trả **1033** | Connector chết hoặc token sai | `docker compose logs cloudflared` |
| `docker compose up` báo **thiếu TUNNEL_TOKEN** | `.env` chưa có dòng đó | Đúng như thiết kế — thêm token vào `.env` |
| Connector lạ xuất hiện trong dashboard | Token bị lộ | Xoá tunnel, tạo tunnel mới. Token không xoay vòng riêng lẻ được |

---

## Những chỗ cách này **không** giải quyết

- **Access không thay được việc app thiếu đăng nhập.** Ai vào được bằng email trong
  allowlist là có toàn quyền: xoá tên miền, đọc token ở trang Cài đặt. Allowlist chỉ nên
  có người thật sự sở hữu hệ thống.
- **Tunnel token mạnh ngang chìa khoá.** Ai có nó dựng được connector trỏ vào tunnel của
  bạn. Giữ `.env` ở `chmod 600`, đừng commit — `.gitignore` và `.dockerignore` đều đã chặn.
- **Phụ thuộc Cloudflare.** Cloudflare hỏng thì app không vào được từ Internet. SSH tunnel
  tới `127.0.0.1:8787` vẫn là đường vào dự phòng, luôn luôn.
