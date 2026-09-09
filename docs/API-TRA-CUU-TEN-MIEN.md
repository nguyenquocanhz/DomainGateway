# Tài liệu API tra cứu tên miền

> Tổng hợp mọi cách lấy **ngày hết hạn / registrar / nameserver** của một tên miền bằng
> code, kèm giới hạn thực tế của từng nguồn. Toàn bộ endpoint trong tài liệu này đã được
> gọi thử trực tiếp; chỗ nào chưa kiểm chứng được đều có ghi rõ. Ví dụ dùng tên miền
> công khai (`iana.org`, `vnnic.vn`) để bạn copy ra chạy lại được ngay.

---

## 1. Chọn nguồn nào?

Không có một API nào phủ hết mọi TLD. Thứ tự nên dùng:

| Ưu tiên | Nguồn | Phủ TLD | Cần API key | Ghi chú |
|---|---|---|---|---|
| 1 | **RDAP** | toàn bộ gTLD (`.com .net .store .site .io`…) + nhiều ccTLD | Không | Chuẩn chính thức ICANN, JSON sạch, miễn phí |
| 2 | **WHOIS port 43** | gần như mọi TLD, kể cả `.mm` | Không | Text thô, mỗi registry một định dạng |
| 3 | **API Việt Nam (BKNS)** | `.vn .com.vn .io.vn .id.vn`… | Có (bản demo công khai) | Cần thiết vì `.vn` không có RDAP công khai |
| 4 | **API của registrar** | chỉ tên miền trong tài khoản của bạn | Có | Cho thêm `auto_renew`, giá, khoá chuyển nhượng |

**Nguyên tắc quan trọng:** RDAP và WHOIS trả dữ liệu từ *registry*, nên đọc được ngày hết
hạn của **bất kỳ tên miền nào**, kể cả mua ở nhà cung cấp không có API. Đây là lý do một
công cụ quản lý đa nhà cung cấp nên xây trên RDAP/WHOIS thay vì tích hợp từng registrar.

---

## 2. RDAP — nguồn chính (miễn phí, không key)

RDAP (Registration Data Access Protocol, RFC 7482–7484) là bản kế thừa của WHOIS: cùng dữ
liệu nhưng trả về JSON có cấu trúc.

### 2.1. Bootstrap

IANA công bố bảng ánh xạ TLD → máy chủ RDAP tại
`https://data.iana.org/rdap/dns.json`. Thay vì tự đọc bảng đó, dùng dịch vụ chuyển hướng:

```bash
curl -sL -H "Accept: application/rdap+json" https://rdap.org/domain/iana.org
```

`rdap.org` trả về `302` tới đúng máy chủ của registry (`.org` →
`https://rdap.publicinterestregistry.org/rdap/domain/...`), nhớ bật `-L` để đi theo redirect.

### 2.2. Cấu trúc phản hồi

```json
{
  "objectClassName": "domain",
  "ldhName": "iana.org",
  "status": ["server delete prohibited", "server transfer prohibited"],
  "events": [
    { "eventAction": "registration",  "eventDate": "1995-06-05T04:00:00.772Z" },
    { "eventAction": "expiration",    "eventDate": "2027-12-08T17:00:53Z" },
    { "eventAction": "last changed",  "eventDate": "2026-08-12T01:43:56.776Z" }
  ],
  "nameservers": [ { "ldhName": "ns.icann.org" }, { "ldhName": "a.iana-servers.net" } ],
  "secureDNS": { "delegationSigned": true },
  "entities": [
    {
      "roles": ["registrar"],
      "vcardArray": ["vcard", [["version",{},"text","4.0"],["fn",{},"text","CSC Corporate Domains, Inc."]]]
    }
  ]
}
```

Cách bóc dữ liệu:

| Cần lấy | Đường dẫn |
|---|---|
| Ngày hết hạn | `events[] where eventAction == "expiration"` → `eventDate` |
| Ngày đăng ký | `events[] where eventAction == "registration"` |
| Registrar | `entities[] where roles contains "registrar"` → `vcardArray[1]` → phần tử có `fn` → index 3 |
| Nameserver | `nameservers[].ldhName` |
| Khoá chuyển nhượng | `status[]` chứa `client transfer prohibited` |
| DNSSEC | `secureDNS.delegationSigned` |

`vcardArray` là mảng lồng nhau khá khó chịu: `["vcard", [ [tên_trường, {}, kiểu, giá_trị], ... ]]`.
Phải duyệt tìm phần tử có `[0] == "fn"` rồi lấy `[3]`.

### 2.3. Mã trạng thái

| Mã | Ý nghĩa |
|---|---|
| `200` | Có dữ liệu |
| `404` | Tên miền **chưa được đăng ký** (hoặc registry không hỗ trợ RDAP) |
| `429` | Bị giới hạn tần suất — chờ rồi thử lại |
| `503` | Máy chủ RDAP của registry đang lỗi — chuyển sang WHOIS:43 |

> `404` không phải lúc nào cũng nghĩa là tên miền còn trống. Với `.vn`, `rdap.org` trả `404`
> chỉ vì VNNIC không tham gia bootstrap. Luôn kiểm tra chéo trước khi kết luận "còn trống".

### 2.4. Giới hạn

- Mỗi registry tự đặt rate limit, thường vài chục request/phút cho một IP. Chưa gặp `429`
  khi tra 9 tên miền liên tiếp.
- Dữ liệu chủ thể bị che theo GDPR/ICANN — chỉ còn tên registrar và các mốc thời gian.
- Registry `.eu` (EURid) **không** có trong bootstrap của `rdap.org` (đã thử `europa.eu` → `404`).

Cài đặt trong repo: [`gateway/resolver.py`](../gateway/resolver.py) → `fetch_rdap()`.

---

## 3. WHOIS port 43 — phương án dự phòng

Giao thức thô: mở TCP tới cổng 43, gửi tên miền + `\r\n`, đọc hết text tới khi server đóng.

```bash
# Bước 1: hỏi IANA xem TLD này do máy chủ WHOIS nào phụ trách
echo "mm" | timeout 15 nc whois.iana.org 43 | grep -i "^whois:"
#   whois:        whois.registry.gov.mm

# Bước 2: hỏi chính máy chủ đó
echo "mpt.com.mm" | timeout 15 nc whois.registry.gov.mm 43
```

### 3.1. Đi tiếp một hop (referral)

Registry của gTLD chỉ trả dữ liệu tối thiểu kèm con trỏ:

```
Registrar WHOIS Server: whois.namecheap.com
```

Muốn đủ thông tin thì phải hỏi tiếp máy chủ đó. Trong repo, `fetch_whois43()` tự đi thêm
đúng **một** hop rồi ghép hai kết quả.

### 3.2. Bẫy khi parse — đã gặp thật

Mỗi registry đặt tên trường khác nhau, cần thử lần lượt nhiều biến thể:

| Trường | Các nhãn từng gặp |
|---|---|
| Hết hạn | `Registry Expiry Date`, `Registrar Registration Expiration Date`, `Expiration Date`, `Expiry Date`, `expires`, `paid-till`, `renewal date` |
| Đăng ký | `Creation Date`, `created`, `Registered on`, `Registration Time` |
| Registrar | `Registrar`, `Sponsoring Registrar`, `Registrar Name` |

**Lỗi kinh điển:** dùng `\s*` trong regex để bắt khoảng trắng sau dấu hai chấm.
`\s` khớp cả ký tự xuống dòng, nên với khối thụt lề kiểu EURid:

```
Registrar:
        Name: ClearMedia NV
```

regex `^\s*registrar\s*:\s*(.+?)\s*$` sẽ nuốt luôn dòng dưới và trả về
`"Name: ClearMedia NV"`. Phải dùng `[ \t]*` thay cho `\s*`, rồi xử lý khối thụt lề bằng một
regex riêng. Lỗi này từng tồn tại trong repo và đã được sửa ở
[`gateway/resolver.py`](../gateway/resolver.py) → `_whois_grab()` / `parse_whois()`.

### 3.3. Kết quả kiểm chứng ngày 08/09/2026

| TLD | Máy chủ | Kết quả |
|---|---|---|
| `.mm` | `whois.registry.gov.mm` | Đọc được đầy đủ (`mpt.com.mm` hết hạn 30/09/2026, registrar *Myanma Posts and Telecommunications*) |
| `.eu` | `whois.eu` | Kết nối được, có registrar — nhưng **EURid không công bố ngày hết hạn** qua WHOIS công khai |
| `.vn` | `whois.vnnic.vn` | **Timeout** từ mạng ngoài Việt Nam trong lần thử này |

Hệ quả thực tế: `.eu` phải nhập ngày hết hạn thủ công (Domain Gateway có sẵn tính năng
"đặt ngày hết hạn tay" + cờ `pinned`), hoặc lấy từ bảng điều khiển của registrar.

---

## 4. `.vn` — API WHOIS Việt Nam

### 4.1. VNNIC (nguồn gốc)

- Tra cứu web: <https://tracuutenmien.gov.vn> — có reCAPTCHA nên **không** gọi tự động được.
- WHOIS:43: `whois.vnnic.vn` — timeout trong lần thử ở trên.
- RDAP: `https://rdap.vnnic.vn/domain/{domain}` — timeout trong lần thử ở trên.

### 4.2. BKNS WHOIS API — nguồn đang dùng

Trung gian có REST API, nối thẳng dữ liệu VNNIC. Tài liệu: <https://whois.bkns.vn/vi/docs>

```bash
curl -s "https://whois.bkns.vn/api/v1/whois?domain=vnnic.vn" \
  -H "X-API-Key: $BKNS_API_KEY"
```

```json
{
  "domain": "vnnic.vn",
  "tld": "vn",
  "status": "registered",
  "cached": false,
  "data": {
    "registrant":   { "name": "Nguyen Van A" },
    "registrar":    { "name": "Công ty Cổ phần GMO-Z.com RUNSYSTEM" },
    "nameservers":  ["alice.ns.cloudflare.com", "bob.ns.cloudflare.com"],
    "dates":        { "created": "2024-12-16T16:21:37.000Z",
                      "updated": null,
                      "expiry":  "2026-12-16T16:21:37.000Z",
                      "renewalDeadline": null },
    "domainStatus": ["clientTransferProhibited"],
    "dnssec": false
  }
}
```

| Endpoint | Mô tả |
|---|---|
| `GET /api/v1/whois?domain=` | Tra cứu đầy đủ |
| `GET /api/v1/domain/check?domain=` | Kiểm tra còn trống hay không |
| `POST /api/v1/whois/bulk` | Tra hàng loạt, tối đa 20 tên miền (**không** dùng được với key demo) |

**Hạn mức theo tài liệu của BKNS:**

| Gói | Giới hạn |
|---|---|
| Demo (tự xin ở whois.bkns.vn/vi/docs) | 2 request/phút, 200 request/ngày mỗi IP |
| Public | 10 request/phút |
| Partner | 300 request/phút (liên hệ BKNS) |

Khi vượt hạn mức, API trả `429`. Xử lý đúng cách: đọc header `Retry-After` nếu có, lùi theo
cấp số nhân rồi thử lại — xem `fetch_bkns()`. Ngoài ra nên giãn nhịp gọi rộng hơn mức tối
thiểu (repo dùng hệ số an toàn 1,2×) vì cửa sổ trượt của máy chủ có thể tính khác client.

> Key demo là key công khai in trong tài liệu của BKNS, dùng để thử. Chạy thật thì xin key
> riêng — 200 request/ngày sẽ hết nhanh nếu bạn tra cứu lại nhiều lần.

### 4.3. Các trang WHOIS Việt Nam khác

`whois.inet.vn`, `vietnix.vn/whois`, `whois.matbao.net`… đều là giao diện web, **không công
bố API mở**. Muốn tự động hoá thì dùng BKNS hoặc xin quyền API từ chính nhà đăng ký của bạn.

---

## 5. API của các nhà đăng ký

Chỉ dùng được cho tên miền **trong tài khoản của bạn**, nhưng đổi lại có thêm trạng thái
`auto_renew`, giá gia hạn và thao tác khoá/mở chuyển nhượng.

### 5.1. Porkbun (API v3) — dễ dùng nhất

Base URL: `https://api.porkbun.com/api/json/v3`
Xác thực: header `X-API-Key` + `X-Secret-API-Key`, hoặc `apikey`/`secretapikey` trong body JSON.
Bật API trong Account Settings, key có dạng `pk1_…` / `sk1_…`.

```bash
# Kiểm tra kết nối — chạy được cả khi chưa có key, trả về IP của bạn
curl -s -X POST https://api.porkbun.com/api/json/v3/ping \
  -H "Content-Type: application/json" -d '{}'
# {"status":"SUCCESS","yourIp":"…","requestId":"…"}

# Bảng giá toàn bộ TLD — công khai, không cần key
curl -s https://api.porkbun.com/api/json/v3/pricing/get
```

Đã kiểm chứng: `pricing/get` trả về **907 TLD**. Vài mức giá tại thời điểm thử (USD):

| TLD | Đăng ký | Gia hạn |
|---|---|---|
| `.com` | 11,08 | 11,08 |
| `.eu` | 5,88 | 5,88 |
| `.site` | 1,96 | 28,84 |
| `.store` | 2,57 | 43,77 |
| `.vn`, `.mm` | *Porkbun không bán* | — |

> Chú ý bẫy giá: `.site` và `.store` rẻ năm đầu nhưng gia hạn gấp **15×**. Khi quản lý chi
> phí nên nhìn cột gia hạn, không nhìn cột đăng ký.

Các endpoint khác (theo tài liệu Porkbun, chưa gọi thử vì cần key):

| Endpoint | Phương thức |
|---|---|
| `/domain/checkDomain/{domain}` | POST — kiểm tra còn trống + giá |
| `/domain/listAll` | POST — liệt kê tên miền trong tài khoản, phân trang bằng `start` |
| `/dns/retrieve/{domain}` | POST — đọc bản ghi DNS |

Tài liệu: <https://porkbun.com/api/json/v3/documentation>

### 5.2. Namecheap

Endpoint: `https://api.namecheap.com/xml.response`
Sandbox: `https://api.sandbox.namecheap.com/xml.response`

Tham số bắt buộc cho mọi lệnh: `ApiUser`, `ApiKey`, `UserName`, `ClientIp`, `Command`.

```bash
curl -s "https://api.namecheap.com/xml.response\
?ApiUser=USER&ApiKey=KEY&UserName=USER&ClientIp=1.2.3.4\
&Command=namecheap.domains.check&DomainList=vidu.com,vidu.net"
```

Trả về XML (không phải JSON):

```xml
<DomainCheckResult Domain="vidu.com" Available="false" IsPremiumName="false"/>
```

Lệnh hay dùng: `namecheap.domains.check`, `namecheap.domains.getList`,
`namecheap.domains.getInfo`, `namecheap.users.getPricing`.

**Điều kiện dùng API (theo chính sách Namecheap):** phải whitelist IP gọi API, và tài khoản
cần đạt một trong các mốc về số tên miền đang quản lý / số dư / mức chi tiêu. Trang tài liệu
chặn truy cập tự động (`403`) nên hãy kiểm tra con số hiện hành tại
<https://www.namecheap.com/support/api/intro/>.

Trang WHOIS web của Namecheap hữu ích để đối chiếu bằng mắt:
`https://www.namecheap.com/domains/whois/result?domain=iana.org`
(Domain Gateway có sẵn menu "Mở WHOIS Namecheap" ở từng dòng.)

### 5.3. GoDaddy — đọc kỹ trước khi định dùng

```bash
curl -s "https://api.godaddy.com/v1/domains/available?domain=vidu.com&checkType=FAST" \
  -H "Authorization: sso-key {KEY}:{SECRET}"
```

> **Cảnh báo:** từ tháng 05/2024 GoDaddy siết quyền truy cập API. API kiểm tra tên miền
> (Availability) chỉ mở cho tài khoản có **từ 50 tên miền trở lên**; API quản lý/DNS yêu cầu
> tối thiểu 1 tên miền hoặc gói Discount Domain Club – Domain Pro. Thay đổi này áp dụng gần
> như không báo trước và đã làm hỏng rất nhiều tích hợp, đặc biệt là tự động gia hạn chứng
> chỉ Let's Encrypt qua DNS-01.
>
> Nếu bạn có dưới 50 tên miền ở GoDaddy: **đừng xây gì phụ thuộc vào API này**. Dùng
> RDAP/WHOIS để theo dõi hạn, còn thao tác thì làm thủ công trên bảng điều khiển.

Tài liệu: <https://developer.godaddy.com/>

### 5.4. Cloudflare

Nếu tên miền của bạn đang trỏ nameserver về Cloudflare thì hai API này hữu ích:

```bash
# Danh sách zone (tên miền đang dùng DNS của Cloudflare, mua ở bất kỳ đâu)
curl -s "https://api.cloudflare.com/client/v4/zones" \
  -H "Authorization: Bearer {API_TOKEN}"

# Tên miền đăng ký NGAY TẠI Cloudflare Registrar (có ngày hết hạn + auto-renew)
curl -s "https://api.cloudflare.com/client/v4/accounts/{account_id}/registrar/domains" \
  -H "Authorization: Bearer {API_TOKEN}"
```

Điểm cần phân biệt: `/zones` cho biết tên miền nào **dùng DNS** của Cloudflare, chứ không
cho biết ngày hết hạn — vì tên miền có thể mua ở Namecheap/GMO rồi chỉ trỏ NS sang. Chỉ
`/registrar/domains` mới có ngày hết hạn, và chỉ với tên miền mua tại Cloudflare.

Đây chính là lý do màn hình Domains của Cloudflare không giúp bạn quản lý hạn: nó chỉ thấy
phần DNS. Ngày hết hạn thật phải lấy từ registry qua RDAP/WHOIS.

---

## 6. Bảng tra nhanh: TLD nào dùng nguồn nào

| TLD | Nguồn tốt nhất | Đọc được ngày hết hạn? |
|---|---|---|
| `.com` `.net` `.org` | RDAP | Có |
| `.store` `.site` `.shop` `.xyz`… | RDAP | Có |
| `.io` `.co` `.me` | RDAP | Có |
| `.vn` `.com.vn` `.io.vn` `.id.vn` | BKNS API | Có |
| `.eu` | WHOIS:43 `whois.eu` | **Không** — EURid ẩn ngày hết hạn, phải nhập tay |
| `.com.mm` `.net.mm` `.org.mm` | WHOIS:43 `whois.registry.gov.mm` | Có |
| ccTLD khác | WHOIS:43 qua `whois.iana.org` | Tuỳ registry |

---

## 7. Bẫy nghiệp vụ khi quản lý hạn tên miền

### 7.1. Múi giờ

RDAP/WHOIS trả về giờ UTC. Với chủ thể ở Việt Nam (UTC+7), một tên miền hết hạn
`2026-10-15T23:59:59Z` sẽ rơi vào **16/10/2026** theo giờ Việt Nam. Nếu hiển thị chỗ này
theo UTC, chỗ kia theo giờ máy, số liệu sẽ lệch nhau một ngày. Domain Gateway lưu UTC và
quy đổi sang giờ địa phương ở **mọi** nơi hiển thị.

### 7.2. Ngày hết hạn ≠ ngày mất tên miền

Vòng đời chuẩn của gTLD sau ngày hết hạn:

| Giai đoạn | Thời lượng điển hình | Điều gì xảy ra |
|---|---|---|
| Auto-Renew Grace Period | 0–45 ngày | Vẫn gia hạn được với giá thường; website thường đã tắt |
| Redemption Grace Period | 30 ngày | Chỉ chuộc lại được, phí phạt thường 80–200 USD |
| Pending Delete | 5 ngày | Không làm gì được nữa |
| Released | — | Bất kỳ ai cũng đăng ký được |

Website **tắt ngay khi hết hạn**, không đợi hết grace period. Vì vậy mốc cảnh báo mặc định
30 ngày / 7 ngày là để bạn kịp gia hạn trước khi dịch vụ gián đoạn, chứ không phải trước khi
mất tên miền. ccTLD như `.vn` có quy định riêng, cần kiểm tra với nhà đăng ký.

### 7.3. `clientTransferProhibited` là bình thường

Đây là khoá chống chuyển nhượng trái phép mà đa số registrar bật mặc định — không phải sự
cố. Nhưng nếu định chuyển tên miền sang chỗ khác thì phải tự mở khoá trước.

### 7.4. Cache và hạn mức

Đừng tra cứu lại mỗi lần mở trang. Ngày hết hạn hầu như không đổi trong ngày; Domain Gateway
cache 12 giờ và chỉ tra lại tên miền đã quá hạn cache hoặc lần trước bị lỗi.

---

## 8. Mã nguồn tham chiếu trong repo

| Việc | File |
|---|---|
| RDAP client | [`gateway/resolver.py`](../gateway/resolver.py) → `fetch_rdap()` |
| BKNS client + retry `429` | `fetch_bkns()` |
| WHOIS:43 + tự khám phá server + referral | `whois_query()`, `discover_whois_server()`, `fetch_whois43()` |
| Parser WHOIS đa định dạng | `parse_whois()`, `_whois_grab()` |
| Chuỗi fallback theo TLD | `Resolver.lookup()` |
| Giới hạn tần suất | `RateLimiter` |
| Chuẩn hoá tên miền (URL, IDN, `www.`) | `normalize_domain()` |
