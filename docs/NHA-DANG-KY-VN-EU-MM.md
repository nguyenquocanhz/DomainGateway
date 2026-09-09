# Tổng hợp nơi đăng ký tên miền: Việt Nam · EU · Myanmar

> Dữ liệu máy đọc được của tài liệu này nằm ở [`assets/registrars.json`](../assets/registrars.json)
> và hiển thị trong Domain Gateway ở mục **Tra cứu → Danh bạ nhà đăng ký**.
>
> Giá và chính sách thay đổi thường xuyên. Chỉ những con số ghi rõ "đã kiểm chứng
> 08/09/2026" là do gọi API lấy trực tiếp; còn lại hãy xem trên trang chính thức.

---

## 1. Việt Nam — `.vn` và các đuôi cấp 2

### Cơ quan quản lý

| | |
|---|---|
| Registry | **VNNIC** – Trung tâm Internet Việt Nam |
| Website | <https://vnnic.vn> |
| Tra cứu chính thức | <https://tracuutenmien.gov.vn> (có reCAPTCHA, không gọi API tự động được) |
| WHOIS:43 | `whois.vnnic.vn` |

VNNIC **không bán trực tiếp** — mọi giao dịch phải qua nhà đăng ký được cấp phép.

### Các đuôi

`.vn` · `.com.vn` · `.net.vn` · `.org.vn` · `.edu.vn` · `.gov.vn` · `.biz.vn` · `.info.vn` ·
`.pro.vn` · `.name.vn` · `.health.vn` · `.io.vn` · `.id.vn` · `.ai.vn`

`.io.vn` và `.id.vn` là nhóm đuôi giá rẻ, phổ biến với lập trình viên và dự án cá nhân —
đây là nhóm TLD do VNNIC quản lý.

### Điều kiện

Tên miền `.vn` bắt buộc định danh chủ thể: cá nhân dùng CCCD/hộ chiếu, tổ chức dùng giấy
phép kinh doanh, và phải khai báo với cơ quan quản lý nhà nước sau khi đăng ký. Đây là khác
biệt lớn so với gTLD quốc tế — **không** đăng ký ẩn danh được, và WHOIS công khai vẫn hiện
tên tổ chức (thông tin cá nhân thì được ẩn).

### Nhà đăng ký

| Nhà đăng ký | Website | API công khai |
|---|---|---|
| iNET | <https://inet.vn> | Không — có trang tra cứu <https://whois.inet.vn> |
| GMO-Z.com RUNSYSTEM | <https://z.com/vn> | Không |
| TENTEN | <https://tenten.vn> | Không |
| PA Việt Nam | <https://www.pavietnam.vn> | Có API cho đại lý |
| Mắt Bão | <https://www.matbao.net> | Có API cho đại lý |
| Nhân Hòa | <https://nhanhoa.com> | Không |
| **BKNS** | <https://www.bkns.vn> | **Có** — REST API WHOIS, xem <https://whois.bkns.vn/vi/docs> |
| Viettel IDC | <https://viettelidc.com.vn> | Không |
| VNPT | <https://domain.vnpt.vn> | Không |
| FPT Telecom | <https://fpt.vn> | Không |
| Vietnix | <https://vietnix.vn> | Không |
| AZDIGI | <https://azdigi.com> | Không |
| Long Vân | <https://longvan.net> | Không |
| Vinahost | <https://vinahost.vn> | Không |
| HostVN | <https://hostvn.net> | Không |
| Tinohost | <https://tinohost.com> | Không |
| Digistar | <https://digistar.vn> | Không |
| CMC Telecom | <https://cmctelecom.vn> | Không |

**Đáng chú ý:** BKNS là nhà đăng ký duy nhất trong danh sách công bố REST API WHOIS mở, có
cả key demo. Đây là lý do Domain Gateway dùng BKNS làm nguồn cho `.vn`.

Giá `.vn` do nhà nước quy định nên chênh lệch giữa các nhà đăng ký chủ yếu ở phí dịch vụ
kèm theo (SSL, hosting, hỗ trợ), không phải ở giá tên miền.

---

## 2. Liên minh châu Âu — `.eu`

### Cơ quan quản lý

| | |
|---|---|
| Registry | **EURid** |
| Website | <https://eurid.eu> |
| WHOIS web | <https://whois.eurid.eu> |
| WHOIS:43 | `whois.eu` |

### Các đuôi

`.eu` · `.ею` (chữ Kirin) · `.ευ` (chữ Hy Lạp)

### Điều kiện — quan trọng

Chủ thể phải thuộc **một trong ba** trường hợp:

1. Cư trú tại EU/EEA, **hoặc**
2. Là công dân EU cư trú bất kỳ đâu trên thế giới (áp dụng từ 2019), **hoặc**
3. Là tổ chức được thành lập trong EU/EEA.

Người Việt Nam không có quốc tịch EU và không cư trú tại EU thì **không đủ điều kiện** đăng
ký trực tiếp. Đường vòng duy nhất là dịch vụ trustee / local presence có phí thường niên, do
các registrar như EuroDNS, Web Solutions, Netim cung cấp — bạn thuê một pháp nhân EU đứng
tên hộ. Cân nhắc kỹ: quyền sở hữu pháp lý khi đó không hoàn toàn nằm trong tay bạn.

### Hạn chế kỹ thuật

EURid **không công bố ngày hết hạn** qua WHOIS công khai, và `.eu` cũng không có trong
bootstrap RDAP của `rdap.org` (đã thử `europa.eu` → `404`). Nghĩa là với `.eu` bạn phải:

- nhập ngày hết hạn thủ công vào Domain Gateway (menu ⋯ → *Đặt ngày hết hạn tay*), hoặc
- lấy từ bảng điều khiển / API của chính registrar.

### Nhà đăng ký

| Nhà đăng ký | Quốc gia | API |
|---|---|---|
| Gandi | Pháp | <https://api.gandi.net/docs/> — REST đầy đủ, có sandbox |
| OVHcloud | Pháp | <https://api.ovh.com/> — mạnh, quản lý cả VPS/DNS |
| IONOS | Đức | <https://developer.hosting.ionos.com/> |
| Hetzner | Đức | DNS API công khai; domain qua konsoleH |
| EuroDNS | Luxembourg | API cho đại lý — chuyên ccTLD châu Âu, có trustee |
| united-domains | Đức | API cho đại lý |
| InternetX | Đức | AutoDNS API — nền tảng bán buôn |
| Key-Systems (CentralNic) | Đức | API bán buôn |
| Netim | Pháp | REST + EPP, hỗ trợ >1000 TLD |
| Openprovider | Hà Lan | <https://docs.openprovider.com/> — mô hình membership |
| Realtime Register | Hà Lan | REST bán buôn |
| TransIP | Hà Lan | <https://api.transip.nl/> |
| Combell | Bỉ | Có API |
| Hostinger | Litva | Hạn chế |
| Namecheap | Mỹ (có bán `.eu`) | XML API |
| Porkbun | Mỹ (có bán `.eu`) | JSON API v3 |

**Giá tham khảo (đã kiểm chứng 08/09/2026 qua API Porkbun):** `.eu` — đăng ký 5,88 USD,
gia hạn 5,88 USD. Đây là một trong số ít TLD mà giá gia hạn bằng giá đăng ký.

---

## 3. Myanmar — `.com.mm`

### Cơ quan quản lý

| | |
|---|---|
| Registry | **MMNIC** — Myanmar Posts and Telecommunications (MPT), Bộ Giao thông và Truyền thông |
| Website | <https://www.registry.gov.mm> · <https://www.ptd.gov.mm/nic.aspx> |
| WHOIS:43 | `whois.registry.gov.mm` — **hoạt động tốt**, đã kiểm chứng 08/09/2026 |

Website registry đôi khi không truy cập được từ ngoài Myanmar, nhưng WHOIS:43 thì phản hồi
bình thường và trả đầy đủ ngày hết hạn.

### Các đuôi

**Không đăng ký được ở cấp `.mm`** — chỉ cấp 3:

| Đuôi | Ai được đăng ký |
|---|---|
| `.com.mm` | Doanh nghiệp — phổ biến nhất |
| `.net.mm` | Nhà cung cấp dịch vụ mạng |
| `.org.mm` | Tổ chức |
| `.per.mm` | Cá nhân |
| `.edu.mm` | Chỉ cơ sở giáo dục |
| `.gov.mm` | Chỉ cơ quan nhà nước |

### Điều kiện

Cần pháp nhân đăng ký tại Myanmar. Người/tổ chức nước ngoài phải mua kèm dịch vụ
**trustee / local representative** (phụ phí thường niên).

### Cảnh báo chi phí

`.mm` đắt hơn gTLD rất nhiều — thường **100–400 USD/năm** tuỳ registrar và tuỳ có kèm
trustee hay không, cộng thời gian duyệt hồ sơ có thể mất nhiều ngày. Không registrar giá rẻ
nào (Porkbun, Namecheap, Cloudflare) bán `.mm`; đã kiểm chứng: bảng giá 907 TLD của Porkbun
**không** có `.mm` (và cũng không có `.vn`).

So sánh giá giữa các nơi: <https://tld-list.com/tld/mm>

### Nhà đăng ký

| Nhà đăng ký | Loại | Trustee |
|---|---|---|
| Nominus | Corporate registrar quốc tế | Có |
| Marcaria | Corporate registrar quốc tế | Có |
| Web Solutions | Registrar EU chuyên ccTLD khó | Có |
| AsiaRegister | Registrar khu vực châu Á | Có |
| AtakDomain | Registrar quốc tế | — |
| Let's Domains | Registrar quốc tế | — |
| EuroDNS | Registrar EU | Có |
| Com Laude / MarkMonitor / CSC | Bảo vệ thương hiệu doanh nghiệp lớn | Có |

---

## 4. Quốc tế — gTLD

| Nhà đăng ký | API | Ghi chú |
|---|---|---|
| **Cloudflare Registrar** | <https://developers.cloudflare.com/api/> | Bán đúng giá gốc, không markup. Bắt buộc dùng DNS Cloudflare. **Không** bán `.vn`/`.eu` |
| **Porkbun** | <https://porkbun.com/api/json/v3/documentation> | API v3 miễn phí, tự bật trong Account Settings. WHOIS privacy + SSL miễn phí |
| **Namecheap** | <https://www.namecheap.com/support/api/intro/> | API XML, cần whitelist IP + đạt điều kiện tài khoản |
| **Spaceship** | <https://docs.spaceship.dev/> | Cùng tập đoàn Namecheap, API REST hiện đại hơn |
| **Dynadot** | <https://www.dynadot.com/domain/api3.html> | |
| **NameSilo** | <https://www.namesilo.com/api-reference> | WHOIS privacy miễn phí trọn đời |
| **GoDaddy** | <https://developer.godaddy.com/> | ⚠️ API kiểm tra tên miền chỉ mở cho tài khoản **50+ domain** kể từ 05/2024 |
| Sav | Có API | |
| Gandi | <https://api.gandi.net/docs/> | |

### Bẫy giá gia hạn

Số liệu lấy từ API Porkbun ngày 08/09/2026 (USD):

| TLD | Năm đầu | Gia hạn | Chênh |
|---|---|---|---|
| `.com` | 11,08 | 11,08 | 1× |
| `.eu` | 5,88 | 5,88 | 1× |
| `.site` | 1,96 | 28,84 | **≈15×** |
| `.store` | 2,57 | 43,77 | **≈17×** |

Đang giữ tên miền `.site` hoặc `.store`? Khi tới hạn, chi phí gia
hạn sẽ cao hơn nhiều so với lúc mua — nên quyết định giữ hay bỏ **trước** ngày hết hạn, chứ
không phải khi nhận email nhắc. Đây chính là loại quyết định mà cột "Còn lại" trong Domain
Gateway sinh ra để phục vụ.

---

## 5. Chọn nơi mua theo nhu cầu

| Nhu cầu | Nên chọn |
|---|---|
| gTLD cho dự án cá nhân, muốn rẻ và có API | Porkbun hoặc Cloudflare Registrar |
| Đã dùng DNS Cloudflare, muốn giá gốc | Cloudflare Registrar (chuyển về sau khi mở khoá) |
| `.vn` cho cá nhân/dự án nhỏ | iNET, TENTEN, GMO-Z.com — nhóm `.io.vn`/`.id.vn` giá rẻ |
| `.vn` cho doanh nghiệp cần hỗ trợ | PA Việt Nam, Mắt Bão, Nhân Hòa, Viettel IDC |
| `.vn` + cần API tự động hoá | BKNS |
| `.eu` và đủ điều kiện cư trú/quốc tịch | Porkbun (rẻ nhất), Gandi hoặc OVHcloud (API tốt) |
| `.eu` nhưng **không** đủ điều kiện | EuroDNS / Netim / Web Solutions kèm trustee |
| `.com.mm` | Nominus, Marcaria, Web Solutions — luôn hỏi rõ phí trustee |
| Nhiều TLD hiếm, quản lý số lượng lớn | Netim, Openprovider, InternetX (bán buôn) |
