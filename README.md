# Infra Capacity Ops Management

Ứng dụng theo dõi capacity, compliance, mô phỏng what-if cho hạ tầng ảo hoá và **giám sát backup
Veritas NetBackup**, hỗ trợ **truy vấn live tới 4 vCenter và 4 NetBackup primary server (tài khoản chỉ đọc)** song song với import CSV/JSON thủ công, lưu lịch sử theo thời
gian bằng PostgreSQL, và giao diện **điều hướng theo luồng công việc** (sidebar trái, HSC design system).

### Cấu trúc giao diện (information architecture)

Mọi tính năng cũ được giữ nguyên nhưng gom lại theo luồng công việc thực tế; mỗi trang có địa chỉ
riêng (`#/<trang>`, giữ được khi F5 / chia sẻ link) và thanh **Phạm vi** trên cùng chỉ hiện các bộ lọc
mà trang đó dùng (vCenter / cluster / host).

| Nhóm | Trang | Nội dung (trước đây nằm ở đâu) |
|---|---|---|
| Tổng quan | **Điều hành** | Tóm tắt, KPI, xu hướng, nhận định & danh mục quyết định cần phê duyệt (Executive View cũ). |
| | **Capacity** | KPI CPU/RAM/Storage, biểu đồ utilization, overcommit, bảng cluster (tab Dashboard cũ). |
| Giám sát | **Compliance** | Findings host/cluster/VM với mã đối soát; bấm mã ở trang Điều hành sẽ nhảy sang đây và lọc sẵn. |
| | **Xu hướng & biến động** | Biến động CPU/RAM theo snapshot và danh sách VM thêm/bớt/đổi cấu hình (trước nằm cuối Dashboard). |
| Inventory | **Host & VM** | Bảng host, bảng VM có lọc cluster riêng, xuất Excel (tab Inventory cũ). |
| | **ESXi & phần cứng**, **Hệ điều hành guest** | Hai tab đầu của License & Inventory cũ; bảng VM có cột IP address (guest.ipAddress, IP chính do VMware Tools báo về) và hostname guest trong tooltip / file Excel. |
| Quy hoạch | **What-if capacity**, **Sizing máy chủ**, **Quy hoạch tài nguyên** | What-if simulation; sizing host vật lý; form giả định + PDF quy hoạch (trước là dropdown trong header). |
| License | **Đánh giá license**, **What-if license**, **Snapshot license** | Ba tab còn lại của License & Inventory cũ, kèm thanh chọn phạm vi cluster cho đợt mua. |
| Backup | **Tổng quan backup**, **Client & RPO**, **Job backup**, **Độ phủ VM**, **Storage & policy**, **Nguồn & ngưỡng** | Giám sát Veritas NetBackup qua REST API chỉ đọc — xem mục *Backup* bên dưới. |
| Báo cáo & cài đặt | **Trung tâm báo cáo** | Mọi báo cáo xuất tập trung, nhóm theo chức năng (lọc nhanh bằng chip): **Capacity & vận hành** (PDF Vận hành / Kỹ thuật, Excel host & VM, CSV tổng hợp), **Quy hoạch & Sizing** (PDF quy hoạch, Sizing PDF đầy đủ / rút gọn / Excel, mẫu nhập liệu), **License & Inventory** (PDF tổng quan — hiện tại hoặc từ snapshot, Excel đánh giá / ESXi / hệ điều hành), **Backup** (PDF hiện trạng — có / không phụ lục, Excel thống kê; chọn server và kỳ). Mỗi báo cáo có “Tuỳ chọn” riêng (Storage, cửa sổ xu hướng, giả định quy hoạch, hướng đề xuất sizing…) được ghi nhớ; “Chọn nhiều” để xuất nhiều báo cáo một lần. Khung bên phải: phạm vi cluster / host, loại finding, logo. Nút xuất ở từng trang vẫn giữ. |
| | **Cài đặt** | Tiêu chí đánh giá (Storage), ngưỡng (idle, overcommit, HT) có nút **Lưu ngưỡng** lên server, trạng thái vCenter, import CSV/JSON, sample data, reset. |

Giao diện dùng HSC design system: dark theme mặc định (nền đen, bề mặt `#14171A`, brand `#2681FF`),
light theme chuyển bằng nút ở chân sidebar; font Inter, số dùng tabular-nums, màu trạng thái
AN TOÀN/THEO DÕI/CẢNH BÁO/VƯỢT NGƯỠNG nhất quán giữa web và PDF. Dưới 1024px sidebar thu thành
ngăn kéo (drawer).

### Xuất báo cáo PDF (khổ A4)

Hai nút **Xuất PDF · Vận hành** và **Xuất PDF · Kỹ thuật** ở header tải báo cáo PDF khổ
A4 (in được trực tiếp) từ `GET /api/reports/pdf?view=executive|technical&vcenter=<key|all>`
(yêu cầu đăng nhập), theo scope vCenter đang chọn trên thanh lọc:

- **Báo cáo Vận hành** (`view=executive`) — **2 trang** (tối đa 3), dành cho góc nhìn quản trị:
  ít chữ, nhiều biểu đồ, đủ ý để ra quyết định; phần giải thích chi tiết để ở Báo cáo Kỹ thuật.
  Trách nhiệm xử lý quy về System Owner của từng cluster:
  - *Trang 1*: tiêu đề gọn (dòng eyebrow ghi phạm vi + ngày; không có đoạn mô tả hay dòng meta
    Phạm vi/Ngày/Trạng thái/Đồng bộ), ngay dưới là hộp **Tổng kết — vấn đề & quyết định đề nghị**
    (trạng thái + 1 câu kết luận + danh sách đánh số, mỗi dòng là *pain point* → quyết định đề
    nghị · thời điểm, ví dụ "1/3 cluster không chịu được mất 1 host (DR-CLUSTER) → Duyệt host dự
    phòng N+1 cho 1 cluster · Trong 1 quý"; + 5 ô số liệu: dư địa, số tháng tới ngưỡng, dự phòng
    N+1, VM thu hồi được, số điểm nghiêm trọng), gauge CPU/RAM (và Storage nếu bật) cạnh 4 KPI,
    biểu đồ **Bao lâu nữa chạm ngưỡng** (3 kịch bản +5/+10/+20 %/quý, mốc 1–2–3 năm) cạnh biểu đồ
    **Mức dùng theo cluster**, và bảng **Vấn đề cần quyết định** (tiêu đề = vấn đề/pain point,
    1 dòng *Rủi ro*, 1 dòng *Đề xuất* → ưu tiên → thời điểm → đề nghị phê duyệt → ô ký; tối đa 6
    dòng). Toàn bộ câu chữ viết ngắn, nêu thẳng vấn đề và quyết định cần lấy, không mô tả kỹ thuật.
  - *Trang 2*: biểu đồ **Xu hướng** CPU/RAM theo cluster + 4 ô biến động (CPU, RAM, số VM, số
    host), bảng cluster cần quan tâm (có cột N+1), **Điểm cần System Owner xử lý** gồm donut
    finding theo mức độ, biểu đồ nghiêm trọng theo cluster và theo loại, top-6 điểm nghiêm trọng có
    Mã, và một dòng tham chiếu sang Báo cáo Kỹ thuật.
  - Các biểu đồ so sánh/rủi ro theo cluster, donut VM/finding, bảng lịch sử theo cluster và danh
    sách cảnh báo/thông tin không nằm trong Báo cáo Vận hành — xem Báo cáo Kỹ thuật.
- **Báo cáo Kỹ thuật** — cùng bố cục mở đầu với Báo cáo Vận hành: tiêu đề gọn (eyebrow ghi
  phạm vi + ngày, không có đoạn mô tả hay dòng meta/chính sách), rồi hộp **Tổng kết khuyến nghị**
  giống hệt (cùng kết luận, cùng danh sách khuyến nghị và 5 ô số liệu) cộng thêm một hàng 5 ô
  kỹ thuật: Hosts/VMs, CPU overcommit và RAM overcommit thực tế so với ngưỡng (kèm hệ số HT),
  số findings critical/warning/info, chính sách idle + Storage. Phần còn lại là bảng overcommit
  theo cluster, chi tiết từng host, từng datastore, và toàn bộ compliance findings (sắp xếp theo
  severity). Dài hơn, dành cho đội kỹ thuật.

### Báo cáo Quy hoạch tài nguyên (Resource Planning)

Nút **Xuất PDF · Quy hoạch** mở bảng giả định rồi tạo `view=planning` (3–4 trang) để làm căn cứ
đề xuất mở rộng hạ tầng. Tham số (đều là query param của `GET /api/reports/pdf`):
`horizon` (12–60 tháng, mặc định 36), `growth` (các kịch bản %/quý, mặc định `5,10,20` =
Thấp/Cơ sở/Cao), `node_cores` + `node_ram` (spec node mới; để trống = host lớn nhất của từng
cluster), `lead` (tháng mua sắm + triển khai, mặc định 3), `target` (% ngưỡng overcommit coi là
đủ năng lực, mặc định 80). Theo phạm vi vCenter/cluster đang chọn như các báo cáo khác.

- Logic (`backend/app/planning.py`): dự phóng vCPU/RAM **đã cấp phát** cho VM đang bật theo lãi
  kép quý; với mỗi cluster × kịch bản × tháng, tìm số node nhỏ nhất sao cho mức dùng ≤ `target`
  % ngưỡng **và** vẫn ≤ 100% khi mất 1 host lớn nhất (N+1). Nếu có lịch sử snapshot ≥ 30 ngày,
  thêm kịch bản "Xu hướng N tháng" từ tăng trưởng thực tế (toàn phạm vi và từng cluster).
  Khi chỉ RAM là ràng buộc, báo cáo nêu thêm phương án B nâng RAM (bội số 64 GB/host).
- Bố cục (`backend/app/report_planning.py`): cùng mở đầu với hai báo cáo kia — hộp **Tổng kết đề
  xuất** (số node theo kịch bản cơ sở, thời điểm cần/đặt hàng, ràng buộc CPU hay RAM, danh sách
  đề nghị phê duyệt theo cluster), rồi Hiện trạng & giả định, Dự phóng nhu cầu so với năng lực
  (toàn phạm vi + từng cluster, trục chặn ở 200%), Lộ trình bổ sung node theo mốc 6/12/…
  tháng kèm biểu đồ, Đề xuất theo cluster (phương án A thêm node / B nâng RAM).

### What-if Simulation — Upgrade Host (nâng cấp host hiện hữu)

Ngoài Add Host / Add VM / Resize VM / Decommission, kịch bản **Upgrade Host** mô phỏng nâng cấp tại
chỗ (thêm DIMM, đổi CPU) cho các host đang tick trong cluster mục tiêu. Quy tắc riêng cho CPU
cores và RAM: *Giữ nguyên / Đặt thành / Cộng thêm / Tăng theo %*; bảng host xem trước cấu hình
sau nâng cấp. Kết quả gồm before/after như các kịch bản khác, bảng Δ từng host, tổng cores/GB thêm
quy đổi ra "tương đương bao nhiêu host trung bình" để so với phương án mua host mới, và kiểm tra
N+1 sau nâng cấp (mất host lớn nhất mới) — vì nâng cấp tại chỗ không tăng số host nên không cải
thiện dự phòng N+1. Template nhanh **Host upgrade** (RAM ×2 toàn bộ host) cũng dùng logic này
(`getEffectiveData({ upgradeHosts })`).

Kịch bản **Upgrade + Add Host** gộp hai bước trong một kịch bản: (1) quy tắc nâng cấp cho host đang
tick, (2) danh sách host mới (nhiều dòng), chạy một lần ra một kết quả before/after chung. Bảng
chi tiết liệt kê cả host nâng cấp (Δ cores/RAM) lẫn host mới (nhãn "mới"), dòng tổng hợp tách
riêng phần năng lực đến từ nâng cấp và từ host mới, số host trước → sau, và N+1 tính trên cụm
sau thay đổi (mất host lớn nhất, kể cả host mới). Template **Upgrade + expansion** = RAM ×2 host
hiện hữu + 1 host mới bằng host lớn nhất sau nâng cấp.

### Sizing máy chủ (HW sizing từ danh sách VM của dự án)

Bài toán: team Dev gửi danh sách VM cho một ứng dụng/hệ thống mới (tên, số lượng, vCPU, RAM, Disk);
team Infra cần đề xuất số lượng và cấu hình máy chủ vật lý. Trang **Sizing máy chủ** (nhóm Quy hoạch):

- **Nhập liệu**: bảng sửa trực tiếp (Enter ở dòng cuối tạo dòng mới), hoặc **Import Excel/CSV**
  (`POST /api/sizing/import`, đọc `.xlsx`/`.csv`, nhận tiêu đề tiếng Việt lẫn tiếng Anh: Tên VM / VM name,
  Số lượng / Qty, vCPU / CPU, RAM / Memory, Disk / Storage, Ghi chú / Note; tự quy đổi "2 TB", "512 GB").
  Nút **Tải file mẫu** (`GET /api/sizing/template`) và **Nạp ví dụ** để thử nhanh. Danh sách, giả định và
  cấu hình ứng viên lưu trong trình duyệt (localStorage) nên không mất khi tải lại trang.
- **Giả định sizing** (cột trái): overcommit CPU mục tiêu (vCPU : pCPU logic, mặc định 3x), hệ số HT,
  overcommit RAM (mặc định 1.0), mức dùng tối đa mỗi host (80 %), dự phòng tăng trưởng (20 %), dự phòng
  hỏng hóc N+x, số host tối thiểu, phần ESXi chiếm (cores/RAM mỗi host), lưu trữ SAN dùng chung hoặc
  đĩa cục bộ/vSAN (có hệ số bảo vệ FTT/RAID) và overhead lưu trữ.
- **Cấu hình host ứng viên**: nhiều dòng (sockets × cores/socket, RAM, đĩa cục bộ nếu vSAN, đơn giá
  tuỳ chọn). Mặc định 2×16C/512 GB, 2×24C/768 GB, 2×32C/1 TB.
- **Kết quả** (`POST /api/sizing/calc`, logic trong `backend/app/sizing.py`): với mỗi cấu hình — số host
  theo CPU, theo RAM, +dự phòng → tổng host; ràng buộc chính; tổng cores/RAM; overcommit CPU/RAM sau khi
  lắp; % năng lực khi mất đúng số host dự phòng (đạt N+x hay chưa); số VM cỡ trung bình của dự án còn
  thêm được; ngân sách ước tính nếu có đơn giá. Phương án **Đề xuất** = ít host nhất → ngân sách thấp nhất
  → hiệu suất dùng cao nhất. Ô số liệu nêu nhu cầu trước/sau tăng trưởng và **dung lượng SAN cần cấp**
  (đã gồm overhead). Khối **Hạ tầng hiện hữu có hấp thụ được không?** đưa toàn bộ nhu cầu vào từng
  cluster đang có (dữ liệu vCenter hiện tại), tính % ngưỡng trước → sau và N+1, kết luận có thể chưa
  cần mua host hay bắt buộc bổ sung.
- **Phân tích tư vấn** (`backend/app/sizing_advisory.py`, trả về trong khối `analysis` của `/api/sizing/calc`):
  - *Workload*: phân bố VM theo T-shirt size (S/M/L/XL/XXL), tỷ lệ GB RAM / vCPU → đặc tính compute /
    cân bằng / memory-intensive, nhóm VM chiếm tài nguyên lớn nhất (Pareto 80% RAM), VM lớn nhất, nhận diện
    workload CSDL/middleware nhạy độ trễ.
  - *Thác nước nhu cầu*: yêu cầu → +tăng trưởng → headroom → năng lực lắp đặt (CPU và RAM).
  - *Mỗi cấu hình*: kiểm tra NUMA (VM vượt cores/RAM của một socket, VM không vừa host), cân bằng CPU/RAM
    (% tài nguyên bị "kẹt" và RAM/cores cân bằng nên chọn), license theo core (tối thiểu 16 core/socket),
    TCO = phần cứng + license × số năm, chi phí / VM / tháng, / vCPU, dư địa tăng trưởng thêm, **bảng điểm
    0–100** trên 7 trục (số host, chi phí, hiệu suất, cân bằng, chịu lỗi, mở rộng, NUMA).
  - *Độ nhạy*: số host của từng cấu hình khi đổi dự phòng tăng trưởng / overcommit CPU / mức dùng tối đa.
  - *Dự báo nhiều năm*: số host cần theo năm với tăng trưởng hằng năm, năm cần mở rộng.
  - *Rủi ro & khuyến nghị* (mức Nghiêm trọng / Cảnh báo / Lưu ý / Đạt kèm hành động), mức sẵn sàng triển khai
    và *lộ trình triển khai* 6 giai đoạn.
  - **Ngưỡng tư vấn cấu hình được** (cột trái, mục **Ngưỡng tư vấn (nâng cao)**; API: `params.advisory`,
    model `AdvisoryThresholds` trong `sizing_advisory.py`): ngưỡng GB RAM/vCPU phân loại workload, % tài nguyên
    thừa, độ chênh coi là cân bằng, tải tối thiểu để xét cân bằng, overcommit CPU tối đa cho CSDL, overcommit
    CPU/RAM bị cảnh báo, dư địa tăng trưởng thấp, mức tải khi mất host đạt điểm tối đa, % TCO để gợi ý phương
    án rẻ hơn, số host gợi ý chia / cảnh báo cluster lớn, và danh sách từ khoá nhận diện VM CSDL. Mỗi đối tác /
    khách hàng có thể gửi bộ ngưỡng riêng theo từng yêu cầu; trường bỏ trống dùng mặc định
    (`GET /api/sizing/defaults` trả về giá trị mặc định + nhãn `advisoryMeta`). Giá trị lưu trên trình duyệt,
    có nút **Khôi phục mặc định**; PDF liệt kê ngưỡng đã áp dụng ở mục Giả định & phương pháp (đánh dấu * giá
    trị đã đổi), Excel ghi ở sheet Tóm tắt.
  - Tham số mới (cột trái, nhóm **Tư vấn & chi phí**): `optimize_for` (`hosts` = ít host nhất, `cost` = TCO
    thấp nhất), `annual_growth_pct`, `horizon_years`, `license_price_per_core`, `license_min_cores_per_socket`.
  Trên web, kết quả có thêm 9 biểu đồ Chart.js (phân bố VM, top nhóm VM, thác nước RAM, số host theo ràng
  buộc, radar đa tiêu chí, phân bổ năng lực cluster, dự báo theo năm, độ nhạy chọn được, TCO), danh sách rủi ro
  và lộ trình.
- **Xuất Excel dạng dashboard** (`POST /api/sizing/export`, `backend/app/excel_sizing.py`): sheet **Dashboard**
  (dải tiêu đề kèm logo, mức sẵn sàng, 6 ô KPI, kết luận chính, 7 biểu đồ Excel gốc — số máy chủ theo phương án,
  phân bố kích thước VM, phân bổ năng lực, dự báo theo năm, nhóm VM lớn nhất, độ nhạy, TCO — và rủi ro chính), tiếp
  theo là các sheet chi tiết dạng Excel Table có lọc, tô màu điều kiện và data bar: So sánh phương án (+ bảng điểm),
  Rủi ro & khuyến nghị (+ lộ trình), Phân tích workload, Độ nhạy & dự báo, Giả định & ngưỡng, Danh sách VM (cột tổng
  là công thức); dữ liệu nguồn của biểu đồ ở sheet cuối "Dữ liệu biểu đồ". Đã thiết lập in A4 vừa khổ ngang.
- **Logo riêng cho báo cáo Sizing**: trong hộp Xuất PDF, mục **Logo trên báo cáo** → *Chọn logo…* (PNG/JPEG/SVG/WEBP,
  ≤ 2MB) để dùng logo khác cho PDF và Excel (`logoDataUrl`); không chọn hoặc bấm *Dùng logo mặc định* → dùng logo trong
  Cài đặt báo cáo. Logo đã chọn được nhớ trên trình duyệt. Excel không nhúng được SVG.
- **Giọng văn báo cáo đầy đủ**: viết theo góc nhìn đơn vị tư vấn tích hợp giải pháp (SI) gửi khách hàng — "chúng tôi
  khuyến nghị / triển khai", "Quý khách phê duyệt", hạn chế thuật ngữ và giải thích ngắn khi cần. Báo cáo tư vấn
  rút gọn (`detail=brief`) dùng cùng văn phong.
- **Báo cáo đầy đủ** (mặc định, `detail=full`, `backend/app/report_sizing_full.py`): trang bìa (thông tin tài liệu — khách hàng,
  đầu mối, dự án, mã tài liệu, phiên bản, ngày lập, người lập, người duyệt — mục lục có số trang, lịch sử phiên bản, ký duyệt), rồi
  1 Tóm tắt (một bộ KPI duy nhất, lý do chọn phương án) · 2 Phạm vi & dữ liệu đầu vào (nguồn, ngày nhận, trong/ngoài phạm vi, quy ước) ·
  3 Nhu cầu tài nguyên · 4 So sánh phương án (bảng điểm, không dùng radar) · 5 Cấu hình đề xuất (thông số cụm, BOM sơ bộ, kiến trúc
  logic, phân bổ năng lực) · 6 Khả năng mở rộng · 7 Rủi ro & khuyến nghị · 8 Kế hoạch triển khai · 9 Giả định & cách tính (bảng giả
  định + bảng tính từng bước) · Phụ lục A Danh sách máy ảo · B Thuật ngữ. Đầu trang:
  "<đơn vị tư vấn> · Đề xuất cấu hình máy chủ – <khách hàng>"; một lề duy nhất khai báo trong `@page`; số theo định dạng Việt Nam;
  dung lượng theo TiB (1 TiB = 1.024 GB). Trường mới của payload: `customerContact`, `version`, `versionNote`, `approvedBy`,
  `inputSource`, `inputDate`, `outOfScope`; tham số `license_edition`, `suggest_variants`.
- **Nền tảng ảo hoá** (`params.platform`: `vmware` · `nutanix` · `hyperv` · `proxmox` · `generic`, danh mục trong
  `backend/app/platforms.py`): phép tính sizing không đổi; tên gọi trong báo cáo (hypervisor, quản trị tập trung, HA, cân bằng tải,
  di chuyển VM, lưu trữ HCI), sơ đồ kiến trúc logic, BOM, kế hoạch triển khai và nhận định lấy theo nền tảng. License tính theo
  đơn vị của từng nền tảng (VMware: core, tối thiểu 16 core/CPU; Windows Server: tối thiểu 8 core/CPU và 16 core/host; Proxmox:
  CPU socket). Chọn nền tảng trên giao diện tự điền loại license, số core tối thiểu và phần tài nguyên dành cho nền tảng (Nutanix
  CVM, Ceph). `generic` dùng khái niệm chung khi chưa chốt nền tảng; Phụ lục B có bảng tên gọi tương ứng giữa các nền tảng.
- **TCO nhiều năm** (mặc định 5 năm, nhóm *Tư vấn & chi phí*): năm 1 mua phần cứng (đơn giá / host của từng cấu hình), bảo hành
  kèm theo phần cứng `warranty_included_years` năm (mặc định 3, đã gồm trong giá); các năm còn lại nhập **chi phí gia hạn bảo hành
  từng năm** — theo số tiền / host / năm hoặc % giá host / năm (`warranty_ext_mode`, `warranty_ext_values`); license tính theo từng
  năm. Kết quả `advisory.costs.yearly` (phần cứng, bảo hành, license, tổng năm, lũy kế); năm chưa nhập giá gia hạn được ghi chú.
  Bảng TCO trình bày dạng ma trận: cột = Năm 1 … Năm N + Tổng N năm; hàng = Phần cứng, License, Dịch vụ bảo hành HW, Tổng theo
  năm, Lũy kế (web, PDF và sheet Excel — Excel dùng công thức cho tổng và lũy kế).
  Hiển thị: bảng + biểu đồ theo năm trên web, mục **Chi phí & TCO** trong PDF (bật/tắt được), sheet **Chi phí TCO** trong Excel.
  Phương án tham khảo chưa có giá phần cứng không đưa vào so sánh TCO.
- **Tuỳ chỉnh báo cáo sau khi tính** (khung "Tuỳ chỉnh báo cáo" hiện sau nút *Tính sizing*, lưu trên trình duyệt):
  - *Chỉnh nhanh giả định*: thanh trượt tỷ lệ chia sẻ CPU/RAM, mức dùng tối đa, dự phòng / tăng trưởng hằng năm, N+x — tính lại khi
    thả tay, đồng bộ với cột Giả định.
  - *Phương án & số host*: chọn tay phương án đề xuất (kể cả phương án tham khảo), tăng số host so với tính toán, ghi lý do
    (`params.pick_profile` = "<socket>x<core/CPU>x<RAM GB>", `params.host_override`). Áp dụng cho màn hình, PDF và Excel; báo cáo ghi
    "Người lập điều chỉnh: …" và tính lại mọi số liệu (mất host, dư địa, dự báo, BOM, license).
  - *Mục in*: bật/tắt từng mục và mục con (Bảng điểm, TCO, BOM, Kiến trúc logic, Phân bổ năng lực, phụ lục…); số mục, mục lục và
    chữ cái phụ lục tự đánh lại.
  - *Nội dung*: sửa câu kết luận, thêm ghi chú của người lập, ẩn nhận định, thêm khuyến nghị riêng (số đếm và mức sẵn sàng tính
    theo nội dung thực in). Gửi trong `reportOptions` của `/api/sizing/pdf` (chỉ áp dụng cho báo cáo đầy đủ).
- **Quy tắc chọn phương án**: ít host nhất → không sát ngưỡng (mất host vẫn dưới mức tối đa, còn dư địa) → chi phí thấp nhất (TCO nếu mọi
  phương án có giá, nếu không thì ngân sách phần cứng, nếu không thì số core license) → điểm tổng hợp cao nhất. Phương án đạt
  N+1 nhưng tải khi mất host chạm mức tối đa được đánh dấu "Sát ngưỡng". Hệ thống tự thêm **phương án tham khảo** (không tham gia
  xếp hạng): ít core hơn khi CPU dư nhiều (giảm license), thêm RAM khi phương án được chọn sát ngưỡng.
- **Nhận định theo đặc thù workload**: nhận diện CSDL (gồm PI Data Archive / historian), Domain Controller (quy tắc tách host),
  máy chủ backup chạy chung cụm, rủi ro vSAN 3 host / dự trữ thấp / HA cần lưu trữ dùng chung; tỷ lệ chia sẻ CPU trình bày theo
  vCPU : core vật lý; ngưỡng CPU tính theo vCPU/core (`db_cpu_per_core_max`, `high_cpu_per_core_warn`).
- **Hướng tới dịch vụ độc lập**: `sizing.py` + `sizing_advisory.py` là Python thuần (không DB, không I/O),
  `report_sizing*.py` chỉ cần WeasyPrint — có thể tách thành micro-service/CLI riêng cho đối tác, khách hàng
  mà giữ nguyên API (`/calc`, `/pdf`, `/export`). Test: `cd backend && python -m pytest tests`.
- **Báo cáo tư vấn rút gọn** (`POST /api/sizing/pdf` với `detail=brief`, cùng template `report_sizing_full.py` ở chế độ
  `compact`): cùng định dạng trang với báo cáo đầy đủ (header / footer, lề, số kiểu Việt Nam, thuật ngữ theo nền tảng), trang 1 là khối
  tiêu đề + thông tin tài liệu thay cho trang bìa; gồm Tóm tắt, So sánh phương án (bảng), **Cấu hình đề xuất & BOM cơ bản** (thông số
  chính, BOM sơ bộ và **Kết luận BOM**: tổng CPU / core / RAM, ổ dữ liệu, cổng mạng, license, bảo hành, mức đáp ứng khi mất host,
  TCO ước tính), Chi phí & TCO (bảng), Rủi ro & khuyến nghị chính (nghiêm trọng / cảnh báo), Giả định chính và Phụ lục danh sách VM.
  Bật/tắt mục trong khung Tuỳ chỉnh báo cáo áp dụng cho cả hai bản. Tên file kết thúc bằng `_rut_gon`. Kết luận BOM cũng có trong
  báo cáo đầy đủ.

### License & Inventory — Inventory & License Assessment

Nhóm trang **Inventory / License** (ESXi & phần cứng, Hệ điều hành guest, Đánh giá license, What-if license, Snapshot license) dùng lại kết nối vCenter read-only, collector theo lịch/thủ công
(mỗi vCenter đồng bộ và báo lỗi độc lập, credential chỉ nằm trong env/Secret), database, đăng nhập và bộ lọc
vCenter / cluster hiện có. Gồm 5 phần:

- **ESXi & phần cứng** — vendor/model, ESXi version/build, CPU model, **CPU socket**, **physical core**,
  **logical thread**, RAM, số VM, HA/DRS theo vCenter → cluster → host (drill-down), phân bố model/ESXi,
  xuất Excel. Collector lấy thêm `hardware.systemInfo`, `hardware.cpuInfo`, `config.product` của host và
  `configurationEx.dasConfig/drsConfig` của cluster; chỉ **physical core** được dùng để tính license
  (không phải vCPU, không phải thread).
- **Phân loại OS** — mỗi VM được xếp vào Windows Server / Windows Client / Linux / Appliance / Other-Unknown
  từ bằng chứng theo thứ tự ưu tiên *override thủ công → VMware Tools (`guest.guestFullName`) → cấu hình VM
  (`config.guestId`) → tên VM*, kèm **nguồn**, **độ tin cậy** (confirmed/high/medium/low) và OS version khi
  nhận diện được. Edition (Standard/Datacenter) không có trong vCenter API nên chỉ ghi khi người dùng
  **override** (có ghi chú bằng chứng, người và thời điểm). Bộ lọc theo phân loại/độ tin cậy/nguồn/cluster/tìm kiếm.
- **License assessment** — engine `backend/app/licensing/assess.py` tính theo **cluster** (HA/DRS/vMotion cho
  phép VM chạy trên mọi host):
  - Windows Server: core licensable mỗi host = max(physical core, 8 × socket, 16). **Standard**: số bộ
    license/host = ceil(số VM Windows Server trong cluster / 2), nhân với core licensable của từng host;
    **Datacenter**: core licensable mỗi host một lần, không giới hạn OSE. Đóng gói pack 2/16 core. VM
    Other/Unknown tạo **khoảng** nhu cầu [min; max] thay vì đoán. Windows Client không thuộc phạm vi.
  - VMware per-core (VCF/VVF/vSphere): max(physical core, 16 × socket) mỗi host, cộng mọi host trong
    phạm vi; tuỳ chọn rule per-CPU legacy (≤32 core/CPU). Tham số "core tối thiểu mỗi đơn hàng" mặc định 0
    vì không có trong tài liệu Broadcom hiện hành.
  - Kết quả: nhu cầu, entitlement, thiếu hụt, công thức, giả định, **danh sách thiếu dữ liệu** và so sánh
    chi phí Standard/Datacenter khi có đủ đơn giá. Trạng thái `ok / shortfall / insufficient_data /
    no_entitlement` — không kết luận compliance khi thiếu entitlement hoặc rule chưa active.
  - **Entitlement & đơn giá** do người dùng nhập (sản phẩm, đơn vị core/pack2/pack16/CPU, số lượng, đơn
    giá, SA, phạm vi vCenter/cluster, hạn). Không có giá mặc định. Tình trạng **Đang sở hữu** hoặc
    **Chưa có (chưa mua)**: dòng "Chưa có" xác nhận sản phẩm đó chưa có license — toàn bộ nhu cầu được tính
    là thiếu hụt phải mua, có thể nhập kèm đơn giá báo giá để ước chi phí.
  - **Phạm vi cluster cho đợt mua** — thanh chip *Phạm vi cluster* (theo `vcenterKey::cluster`) giới hạn
    đánh giá cho một số hệ thống (ví dụ production trước); các cluster còn lại được liệt kê rõ là "chưa
    tính". Lựa chọn lưu theo trình duyệt (`localStorage`); bộ lọc cluster toàn cục (nếu đang chọn) được ưu
    tiên. Snapshot, what-if, Excel và PDF đều ghi phạm vi (`scope.clusterKeys`, `excludedClusterKeys`,
    `partial`).
  - **Xuất PDF · Executive summary** — `backend/app/report_license.py` dùng cùng kết quả engine để xuất
    2–3 trang: kết luận & quyết định đề nghị, phạm vi, **tổng quan license theo cluster**, nhu cầu ·
    entitlement · thiếu hụt · chi phí, cơ sở tính toán (rule/version/nguồn, công thức, thiếu dữ liệu).
    Xuất từ phạm vi hiện tại (trang Đánh giá license, hoặc trực tiếp từ Trung tâm báo cáo) hoặc từ một snapshot đã lưu (nút PDF ở bảng Snapshot).
  - **Rule licensing** có version, URL nguồn, ghi chú, trạng thái `needs_verification / verified`, người
    và ngày xác minh. Rule không sửa đè: mỗi thay đổi tạo version mới; seed từ tài liệu công khai của
    Microsoft (core-based licensing, Product Terms, virtualization guidance) và Broadcom (KB 313548, VCF
    licensing model, vSphere Standard SPD) và luôn ở trạng thái "Chưa xác minh" cho tới khi được đối chiếu.
- **What-if license** — Thêm host / Giảm host / Nâng cấp host / Thêm VM / Chuyển workload: tính lại license
  (before → after → Δ, kèm chi phí nếu có đơn giá) **và** capacity (overcommit CPU/RAM theo chính sách, khả
  năng chịu lỗi N+1 qua `capacity.evaluate_ha_headroom`). Kịch bản vượt ngưỡng hoặc làm mất N+1 bị gắn
  **Không đề xuất** dù tiết kiệm license.
- **Snapshot** — lưu toàn bộ đầu vào (inventory đã phân loại, override, version rule + tham số,
  entitlement) cùng kết quả và hash; nút **Tái lập** chạy lại engine trên đầu vào đã lưu và so hash.

API: `/api/licensing/clusters`, `/inventory`, `/overrides`, `/rules` (+`/versions`, `/activate`, `/verify`),
`/entitlements`, `/assess`, `/assessments` (+`/{id}/recompute`), `/whatif`, `/report/pdf`
(`?clusters=vc::cluster,...` hoặc `?snapshot=<id>`). Tất cả yêu cầu đăng nhập
(ứng dụng hiện có một vai trò; chưa có phân quyền chi tiết theo chức năng).

### Inventory — lọc VM theo cluster & xuất Excel

Trang **Host & VM** có bộ lọc **cluster riêng cho bảng VM** (độc lập với bộ lọc chung trên thanh
công cụ; danh sách cluster tự cập nhật theo scope vCenter đang chọn) và hai nút **Xuất Excel**
cho bảng Host và bảng VM. File `.xlsx` xuất **đúng những dòng đang hiển thị** (đã áp dụng scope
vCenter, bộ lọc cluster/host/power/tìm kiếm và thứ tự sắp xếp), kèm sheet *Phạm vi* ghi scope,
bộ lọc và thời điểm xuất. Backend tạo file bằng `openpyxl` tại `POST /api/export/xlsx`
(yêu cầu đăng nhập; header cố định, autofilter, tối đa 100 000 dòng).

> Cache trình duyệt: `index.html` được backend render với tham số phiên bản
> (`js/core.js?v=<fingerprint>` tính từ nội dung JS/CSS) và mọi file tĩnh trả `Cache-Control:
> no-cache`, nên sau mỗi lần deploy image mới trình duyệt tự tải JS/CSS mới — không còn tình
> trạng giao diện mới nhưng logic cũ (ví dụ dropdown cluster trống).

### Backup — giám sát & báo cáo Veritas NetBackup

Nhóm trang **Backup** theo dõi hiện trạng backup qua **NetBackup REST API bằng tài khoản chỉ đọc**,
lưu lịch sử vào cùng database và làm báo cáo thống kê theo cùng cách với phần tài nguyên ảo hoá
(đồng bộ theo lịch + thủ công, mỗi nguồn báo lỗi độc lập, PDF A4 và Excel).

| Trang | Nội dung |
|---|---|
| **Tổng quan backup** | Kết luận (Đạt / Cần theo dõi / Cần xử lý), 6 KPI, biểu đồ job theo ngày (thành công / một phần / lỗi + TB), nguyên nhân lỗi, client cần xử lý, phát hiện & khuyến nghị. |
| **Client & RPO** | Mỗi client: job gần nhất, lần thành công gần nhất, số giờ đã qua, số lần lỗi liên tiếp, đánh giá RPO. |
| **Job backup** | Activity monitor đã lưu: lọc theo kết quả, loại job (backup / nhân bản DR / restore), client, policy, mã lỗi. |
| **Độ phủ VM** | **Phạm vi cần backup** (chọn nhiều vCenter / cluster / host, ví dụ chỉ production) và đối chiếu VM đang chạy trong phạm vi với client NetBackup (tên VM, hostname guest, IP): VM chưa có backup / có job nhưng lỗi, lọc theo vCenter / cluster. |
| **Storage & policy** | Disk pool: % dùng, xu hướng 30 ngày, số ngày tới ngưỡng high-water; policy: bật/tắt, lần chạy gần nhất, tỷ lệ thành công. |
| **Nguồn & ngưỡng** | Trạng thái kết nối từng primary server, hướng dẫn tài khoản chỉ đọc, ngưỡng giám sát (lưu server, dùng chung). |

Thanh đầu trang chọn **NetBackup server** và **kỳ thống kê** (24 giờ … 90 ngày), có nút
*Đồng bộ NetBackup*, *Xuất Excel* và *Báo cáo PDF*. Trung tâm báo cáo cũng có thẻ **Hiện trạng Backup**.

**Tiêu chí đánh giá (ngưỡng chỉnh được ở trang Nguồn & ngưỡng):**

1. **Tỷ lệ backup thành công** = (status 0 + status 1) / job đã kết thúc; mục tiêu 95 %, nghiêm trọng
   dưới 85 %. Chỉ đếm job ở cấp thực thi: job cha (discovery của VMware policy, multistream) không đếm
   trùng với job con; snapshot thành công đi kèm job backup con cũng không đếm hai lần.
2. **RPO theo client** — thời gian từ bản backup thành công gần nhất: cảnh báo > 26 giờ, nghiêm trọng
   > 72 giờ hoặc lỗi liên tiếp ≥ 2 lần; client có trong policy đang bật mà không có job → nghiêm trọng.
3. **Nguyên nhân lỗi** — mã lỗi NetBackup gom theo đầu mối xử lý: kết nối client / mạng, dữ liệu trên
   client, storage / media, snapshot / ảo hoá, cửa sổ backup / huỷ job, cấu hình policy (kèm hướng xử lý).
4. **Policy** — đang bật nhưng không chạy trong 48 giờ, tỷ lệ lỗi cao, policy đang tắt.
5. **Disk pool** — cảnh báo 80 %, nghiêm trọng 90 %, hoặc dự kiến chạm high-water trong 30 ngày
   (hồi quy tuyến tính 30 ngày lịch sử); volume DOWN → nghiêm trọng.
6. **Job chạy / chờ quá lâu** (12 giờ / 4 giờ), **tràn cửa sổ backup** (job đêm kết thúc sau 7:00).
7. **Bản sao DR** — tỷ lệ thành công job duplication / replication (SLP).
8. **Độ phủ VM** — VM đang chạy có bản backup thành công trong 7 ngày; loại trừ theo từ khoá tên
   (mặc định `vCLS,template,tmpl`), VM tắt không tính.
   **Phạm vi cần backup** (trang Độ phủ VM → *Chỉnh phạm vi*): chọn các vCenter / cluster / host phải
   có backup (có thể chọn nhiều cluster trên nhiều vCenter, lọc nhanh theo tên như `prod`). Chỉ VM trong
   phạm vi được tính độ phủ, đưa vào phát hiện và báo cáo PDF / Excel; VM ngoài phạm vi chỉ được đếm.
   Phạm vi lưu trên server (bảng `backup_scope_rules`), dùng chung cho mọi người dùng; chọn nguyên
   cluster / vCenter sẽ tự bao gồm host thêm sau này. Nên chọn theo cluster — chọn theo host sẽ bỏ sót
   VM khi DRS / vMotion chuyển VM sang host khác. API: `GET|PUT /api/backup/scope`.

**Lịch backup & tuân thủ** (trang *Backup → Lịch & tuân thủ*): mỗi đêm của mỗi policy được đối chiếu với lịch.
- **Đêm backup** tính từ 12:00 hôm đó đến trước 12:00 hôm sau (chỉnh được): job 20:00 → 04:00, job lỡ sang sau nửa đêm
  hay bản Full xong sáng thứ 7 đều thuộc đêm bắt đầu.
- **Nguồn lịch**, theo thứ tự ưu tiên:
  1. *Lịch thủ công / import Excel* (mẫu HSC-Backup-Management: tên hệ thống, Monday…Sunday, Full Backup, Start time,
     End Time, retention) — một hệ thống gom nhiều policy theo tên hoặc mẫu `*`.
  2. *Chế độ `netbackup` (mặc định)*: đọc từ schedule của policy qua API (`backupType`, Calendar / Frequency,
     `startWindow`, `includeDates`, retention): ngày Full, ngày Incremental, cửa sổ từng ngày; cửa sổ mở trước 12:00
     (vd 02:00) thuộc đêm hôm trước; Full “mỗi tuần, cửa sổ nhiều ngày” được kiểm tra là có Full trong 7 ngày.
     Policy không suy ra được lịch dùng khung cố định.
  3. *Khung cố định* (mặc định 20:00 → 04:00 hôm sau): mỗi đêm phải có backup thành công trong khung, không xét
     Full / Incremental. Chế độ `fixed` áp khung này cho mọi policy (policy chạy theo tuần sẽ bị báo thiếu các đêm khác).
- **Kết quả mỗi đêm:** Đạt · Một phần (status 1) · Trễ (xong sau giờ kết thúc + ân hạn 60 phút) · Sai loại (đêm Full
  không chạy Full) · Lỗi (còn client lỗi cuối đêm) · Thiếu (không có job khi cửa sổ đã đóng) · Đang chạy · Chưa đến lịch.
- **RPO client theo lịch** (hằng đêm ≈ 24 h + độ dài cửa sổ + ân hạn ≈ 33 h; theo tuần ≈ 175 h), cảnh báo
  “Policy không chạy” và “tràn cửa sổ” chung không áp cho policy đã có lịch; retention bản Full trên NetBackup được
  đối chiếu với lịch thủ công.
- Ngưỡng ở trang Nguồn & ngưỡng: `schedule_mode`, `fixed_window_start` / `fixed_window_end`, `schedule_grace_minutes`,
  `backup_day_cutoff_hour`. API: `GET|PUT /api/backup/schedules`, `POST /api/backup/schedules/import`.

**Báo cáo PDF “Hiện trạng Backup”**: tóm tắt điều hành → 1 kết quả theo đêm → 2 tuân thủ lịch → 3 nguyên nhân lỗi →
4 client cần xử lý → 5 độ phủ VM → 6 dung lượng lưu trữ → 7 policy → 8 phát hiện & khuyến nghị →
9 tiêu chí & nguồn dữ liệu → Phụ lục A job lỗi, Phụ lục B VM chưa backup.
**Excel “Thống kê backup”**: Tổng quan · Theo ngày (biểu đồ) · Lịch & tuân thủ · Nguyên nhân lỗi · Client & RPO · Policy ·
Storage · Độ phủ VM · Job (toàn bộ job trong kỳ) — mỗi bảng là Excel Table để lọc / pivot.

**Thu thập dữ liệu** (`backend/app/backup/`): mỗi lần đồng bộ đọc job từ lần trước trừ 48 giờ chồng
lấn (và từ job cũ nhất còn đang chạy) rồi **upsert theo (server, jobId)** — job thấy ACTIVE ở lần này sẽ
được cập nhật kết quả cuối ở lần sau; policy và disk pool lưu thành snapshot theo lần đồng bộ (đủ cho
xu hướng dung lượng). Lần đầu lấy `BACKUP_INITIAL_DAYS` ngày (mặc định 14); job cũ hơn
`BACKUP_RETENTION_DAYS` (400) bị dọn. API được gọi (chỉ GET):
`/netbackup/admin/jobs`, `/netbackup/config/policies` (+ chi tiết từng policy),
`/netbackup/storage/disk-pools`. Thiếu quyền Policy/Storage chỉ làm mất phần tương ứng (ghi cảnh báo),
không làm hỏng cả lần đồng bộ.

API backend: `GET /api/backup/servers`, `POST /api/backup/servers/{key}/sync`, `POST /api/backup/sync-all`,
`GET /api/backup/summary?server=&days=`, `GET /api/backup/jobs`, `GET|PUT /api/backup/thresholds`,
`GET /api/backup/report/pdf`, `GET /api/backup/report/xlsx`, `GET|PUT /api/backup/schedules`,
`POST /api/backup/schedules/import`.

### Mã đối soát (ticket code) — dẫn chứng cho từng khuyến nghị

Mỗi compliance finding có một **mã đối soát ổn định** dạng `HRAM-C4C34`, `IDLE-F916A`… (tiền tố
theo loại: `HCPU`/`HRAM` host vượt ngưỡng, `CCPU`/`CRAM` overcommit cluster, `IDLE` VM idle,
`POFF` VM tắt lâu, `OVP` VM over-provision; 5 ký tự hex là FNV-1a của
`loại|vCenter|cluster|đối tượng`). Mã được tính giống nhau ở backend (`capacity.finding_code`) và
web (`core.js findingCode`), nên cùng một vấn đề mang **cùng mã** ở Báo cáo Vận hành, Báo cáo
Kỹ thuật, trang Compliance và file CSV export; mã không đổi giữa các kỳ nếu vấn đề vẫn còn.

- Mỗi khuyến nghị trong Báo cáo Vận hành có dòng **"Đối soát"** liệt kê mã + đối tượng + chỉ số
  đo được (ví dụ "4 điểm cần System Owner xử lý" liệt kê đủ 4 mã; các danh sách dài như VM
  over-provision hiển thị 6 mã đầu và "+n mã khác"). Danh mục quyết định cũng ghi mã tương ứng.
- Bảng "Điểm nghiêm trọng cần System Owner xử lý" của Báo cáo Vận hành có cột **Mã** và liệt kê các điểm
  severity critical (tối đa 8, phần còn lại ghi "+n"); bảng "Compliance Findings đầy đủ" của Báo
  cáo Kỹ thuật có cùng cột Mã và đủ mọi severity.
- Trên web, bấm vào mã trong trang Điều hành sẽ chuyển sang trang Compliance và lọc theo
  mã đó; ô tìm kiếm Compliance cũng nhận mã đối soát.
- API: `GET /api/reports/brief` trả `recommendations[].refs {total, items[{code, entity, cluster,
  vcenterKey, typeLabel, metric, severity}]}` và `refNote`; findings có trường `code`.

### Tiêu chí đánh giá — bật/tắt Storage (môi trường SAN)

Với SAN Storage dùng chung, nhiều VM cấp phát **thick provisioning** nên tỷ lệ "đã dùng / cấp
phát" của datastore luôn tiệm cận 100% dù dung lượng SAN thực còn trống — đưa vào báo cáo sẽ gây
cảm giác thiếu an toàn không đúng thực tế. Vì vậy Storage **mặc định không được đánh giá**; CPU
và RAM luôn bật. Nút **Tiêu chí đánh giá** ở header cho phép bật lại Storage khi cần:

- Khi tắt: KPI Storage hiển thị "Không đánh giá", cột %Storage trong bảng cluster, biểu đồ
  datastore, các finding datastore và khối Storage trong what-if simulation đều được ẩn; PDF ghi
  rõ "Storage: không đánh giá trong báo cáo này" kèm giải thích cho người đọc.
- Lựa chọn được lưu vào cài đặt ứng dụng (`include_storage`, chung cho mọi người dùng) và gửi
  theo mỗi lần xuất PDF (`&storage=1|0`); giá trị khởi tạo lấy từ biến môi trường
  `INCLUDE_STORAGE` (mặc định `false`).
- API `GET /api/reports/brief?vcenter=<key|all>&storage=1|0` trả về bản tóm tắt vận hành
  dạng JSON (cùng logic với PDF) để Executive View hiển thị.

### Biến động tài nguyên theo thời gian (snapshot history)

Mỗi lần đồng bộ vCenter được lưu thành snapshot, nên ứng dụng có thể vẽ **biến động CPU/RAM theo
thời gian** với khoảng nhìn lại **3 / 6 / 12 tháng**. Đơn vị theo dõi **mặc định là cluster** (mỗi
cluster = một nhóm dự án độc lập, là cấp đề xuất bổ sung tài nguyên); nhóm theo vCenter là góc nhìn
phụ:

- Panel "Biến động tài nguyên CPU/RAM theo thời gian" xuất hiện ở cả trang **Điều hành** và trang
  **Dashboard** (Technical View): 4 ô KPI đầu kỳ → nay (CPU %, RAM %, số VM, số host), hai biểu
  đồ đường CPU/RAM (% ngưỡng overcommit, có đường ngưỡng 100% và đường "Toàn bộ"), bảng delta
  từng nhóm và 2–3 câu nhận định ngắn. Technical View có thêm hai bảng **VM thêm mới / VM đã
  loại bỏ** trong kỳ. Lựa chọn khoảng thời gian và cách nhóm được lưu trên trình duyệt
  (`vco.historyMonths`, `vco.historyGroupV2`).
- Panel trên trang Xu hướng & biến động **đi theo bộ lọc Cluster** của trang: chọn một cluster thì biểu đồ,
  bảng delta, nhận định và danh sách VM thêm/bỏ chỉ tính cho cluster đó; Executive View luôn hiển
  thị mọi cluster trong phạm vi vCenter đang chọn.
- Cùng số liệu được đưa vào PDF, luôn theo cluster và **theo đúng phạm vi cluster đã chọn khi xuất
  báo cáo** (không lấy toàn bộ): Báo cáo Vận hành có mục *"Biến động tài nguyên N tháng gần
  nhất"*; Báo cáo Kỹ thuật có mục tương tự và **Phụ lục A — VM thêm mới / loại bỏ** (vCenter,
  cluster, host, trạng thái, vCPU, RAM, ngày xuất hiện/thấy lần cuối; mục A.3–A.5 cho VM di
  chuyển/đổi tên/đổi cấu hình). Tham số
  `&months=3|6|12`, `&clusters=A,B` và `&hosts=h1,h2` của `GET /api/reports/pdf` nhận từ lựa chọn
  trên giao diện. Biểu đồ CPU/RAM luôn ở cấp cluster (đơn vị snapshot được tổng hợp); Phụ lục A
  theo cả cluster và host.
- **Phụ lục A đối chiếu theo định danh VM, loại false positive**: mỗi VM được nhận diện bằng
  `instanceUuid` của vCenter (cột `vm_snapshots.instance_uuid`, tự thêm khi khởi động qua
  `ensure_schema()`; import CSV nhận cột `InstanceUuid`/`VM UUID`/`UUID`); snapshot cũ chưa có UUID
  thì đối chiếu theo tên. *Thêm mới* = định danh chưa có ở **bất kỳ cluster/host nào của vCenter**
  tại đầu kỳ; *loại bỏ* = định danh không còn ở bất kỳ đâu trong vCenter hiện nay. Nhờ vậy các
  trường hợp không phát sinh/giảm tài nguyên **không** bị tính là thêm mới/loại bỏ mà được liệt kê
  riêng để đối soát:
  - **A.3 VM di chuyển**: vMotion giữa host (chỉ liệt kê khi lọc theo host) và migrate giữa cluster
    (từ → đến), kèm vCPU/RAM; bảng tổng hợp có cột "Chuyển vào / ra".
  - **Đổi tên cluster/host**: khi một cluster/host biến mất và cluster/host mới xuất hiện với ≥ 50 %
    host (hoặc VM) trùng nhau (Jaccard, `RENAME_MATCH_MIN`), tên cũ được gộp về tên hiện tại — biểu
    đồ lịch sử liên tục dưới tên mới, bảng tổng hợp ghi "trước: TÊN-CŨ", không tính thêm/bỏ.
  - **A.4 VM đổi tên**: cùng `instanceUuid` nhưng tên khác; khi thiếu UUID dùng heuristic cùng
    cluster + IP + vCPU + RAM (cột "Căn cứ" ghi rõ).
  - **A.5 VM đổi cấu hình vCPU/RAM** được tách riêng vì có làm thay đổi tài nguyên đã cấp
    (trước → nay, delta, tổng tăng/giảm).
  Dòng tóm tắt trong cả hai báo cáo và panel web nêu số trường hợp đã loại
  (`excludedFalsePositives`). Bảng "Tổng hợp theo cluster" có VM đầu kỳ → nay, thêm/bỏ kèm
  vCPU/RAM, chuyển vào/ra, đổi tên VM, đổi cấu hình; khi thu hẹp theo host có thêm bảng theo host.
  Dữ liệu demo (`seed_history`) dựng sẵn các kịch bản: 3 VM chuyển cluster A → B, 4 VM vMotion,
  2 VM đổi tên, 3 VM tăng cấu hình, cụm DR đổi tên từ `DR-CLUSTER-OLD`.
- API: `GET /api/dashboard/history?vcenter=<key|all>&months=3|6|12&group=cluster|vcenter[&clusters=A,B]`
  và `GET /api/dashboard/vm-changes?vcenter=<key|all>&months=3|6|12[&clusters=A,B][&hosts=h1,h2]`. Khi chỉ có 1
  vCenter trong phạm vi, nhãn cluster không kèm tên vCenter. % được tính theo ngưỡng
  overcommit **đang áp dụng** (một snapshot/ngày, lấy lần đồng bộ cuối trong ngày theo giờ VN);
  "thêm mới/loại bỏ" so sánh theo định danh VM (instanceUuid, dự phòng theo tên) giữa snapshot đầu
  kỳ và mới nhất của từng vCenter; phản hồi gồm `added`, `removed`, `moved`, `renamedVms`, `resized`,
  `renamedClusters`, `renamedHosts` và các tổng `*Totals`.
- Cần tối thiểu 2 ngày có snapshot trong khoảng đã chọn; khi chưa đủ, giao diện và PDF hiển thị
  trạng thái "chưa đủ dữ liệu" thay vì biểu đồ. Với môi trường demo có thể tạo lịch sử giả lập
  để xem trước: `cd backend && python -m app.seed_history --months 12 --step-days 7` (chỉ áp
  dụng cho vCenter có `VCENTER_n_DEMO=true`; vCenter thật tích lũy dần qua polling).

Render bằng [WeasyPrint](https://weasyprint.org/) (thuần Python/HTML/CSS, không cần Chromium) —
đã có sẵn system dependencies trong `backend/Dockerfile`. Font Việt dùng bộ Noto Sans để hiển
thị đầy đủ dấu tiếng Việt trong PDF.

## Kiến trúc

```
infra-capacity-ops-management/
├── backend/                  FastAPI (Python) — API, auth, polling scheduler, DB models
│   ├── app/
│   │   ├── main.py            entrypoint, mount static frontend
│   │   ├── config.py          env-based settings (DB, JWT, 4 vCenter blocks)
│   │   ├── security.py        bcrypt hashing + JWT cookie session
│   │   ├── models.py          SQLAlchemy models (User, SnapshotRun, Host/VM rows, AppSetting)
│   │   ├── capacity.py        capacity model + compliance checks (Python port của core.js)
│   │   ├── vcenter/client.py  pyVmomi client — đọc host/VM/cluster/datastore qua vSphere API
│   │   ├── vcenter/demo_generator.py  sinh dữ liệu giả lập khi DEMO=true (không cần vCenter thật)
│   │   ├── collectors/scheduler.py    poll định kỳ vCenter + NetBackup, lưu lịch sử
│   │   ├── backup/            module Backup: NetBackup REST client, demo, collector, mã lỗi, phân tích
│   │   ├── report_backup.py / excel_backup.py   báo cáo PDF / Excel hiện trạng backup
│   │   ├── data_access.py     query lớp dữ liệu mới nhất / theo vCenter / lịch sử trend
│   │   ├── routers/           auth, vcenters, data, dashboard, compliance, imports, export, settings
│   │   └── seed.py            CLI tạo/đổi mật khẩu user đăng nhập
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                 HTML + vanilla JS/CSS (HSC Design System), không build step
│   ├── index.html
│   ├── css/styles.css
│   └── js/
│       ├── api.js             wrapper gọi API backend (cookie session)
│       ├── core.js             toàn bộ logic capacity/compliance/what-if (port từ bản single-file gốc)
│       ├── views/executive.js  render Executive View (KPI, trend chart, forecast, top-risk)
│       └── app.js              bootstrap, login, perspective toggle, live-data loader
├── docker-compose.yml         services: db (Postgres) + app (FastAPI phục vụ luôn frontend)
├── .env.example               toàn bộ biến môi trường cần cấu hình
└── README.md
```

Backend phục vụ **cả API lẫn frontend tĩnh** trên cùng một port — không cần CORS phức tạp, không
cần reverse proxy riêng cho frontend.

## Bắt đầu nhanh (Docker Compose)

```bash
cp .env.example .env
# Sửa .env: đặt mật khẩu Postgres, JWT_SECRET, ADMIN_PASSWORD, và 4 khối VCENTER_i_*
docker compose up -d --build
```

Mở `http://<server-ip>:8000` (hoặc cổng bạn đặt ở `APP_PORT`), đăng nhập bằng
`ADMIN_USERNAME` / `ADMIN_PASSWORD` đã đặt trong `.env`.

Muốn thử trước khi có vCenter thật: đặt `VCENTER_i_DEMO=true` cho các khối vCenter (bỏ trống
`HOST`/`PASSWORD`) — hệ thống sẽ tự sinh dữ liệu giả lập hợp lý để bạn xem qua giao diện.

### Đổi mật khẩu / tạo thêm user đăng nhập

```bash
docker compose exec app python -m app.seed --username <user> --password '<pass>' --display-name "Tên hiển thị"
```

## Tạo tài khoản read-only trên vCenter (bắt buộc trước khi trỏ vào vCenter thật)

Ứng dụng chỉ **đọc** dữ liệu (host, cluster, VM, datastore) — không tạo, sửa, xoá gì trên vSphere.
Tạo một tài khoản dùng chung cho cả 4 vCenter, ví dụ `infra.mon@vsphere.local`, và gán quyền theo
các bước sau trên **từng vCenter** (thực hiện trên vSphere Client, vCenter 8.x):

1. **Administration → Single Sign On → Users and Groups** — tạo user `infra.mon` trong domain
   `vsphere.local` (hoặc dùng tài khoản AD/LDAP đã có nếu vCenter đã join domain).
2. **Administration → Access Control → Roles** — tạo custom role, ví dụ `CapacityOpsReadOnly`,
   dựa trên role có sẵn **Read-Only**, và bật thêm các quyền sau (đã đủ để đọc CPU/RAM/datastore
   usage, không cần quyền ghi):
   - `Host.Inventory.*` (chỉ phần *View*, không cần Modify)
   - `Datastore.Browse datastore`
   - `Global.Settings` (để đọc thông tin phiên bản, tuỳ chọn)
   - Role gốc **Read-Only** đã bao gồm quyền xem VM, Host, Cluster, Datastore, Network — với hầu
     hết môi trường, **Read-Only mặc định là đủ** và không cần custom role.
3. **Administration → Access Control → Global Permissions** (khuyến nghị — áp dụng cho toàn bộ
   inventory kể cả cluster/datacenter tạo sau này) hoặc gán ở cấp **vCenter Server** trong
   **Hosts and Clusters**:
   - Add Permission → chọn user `infra.mon@vsphere.local` → Role = `Read-Only` (hoặc custom role
     ở bước 2) → tick **Propagate to children**.
4. Lặp lại bước 1–3 trên **cả 4 vCenter** với cùng username/password (đơn giản hoá vận hành) hoặc
   đặt mật khẩu khác nhau — cấu hình từng vCenter độc lập trong `.env` (`VCENTER_i_PASSWORD`).
5. Nếu vCenter dùng chứng chỉ tự ký (self-signed), để `VCENTER_i_VERIFY_SSL=false` (mặc định) —
   kết nối vẫn mã hoá TLS, chỉ bỏ qua việc xác thực chuỗi chứng chỉ. Đặt `true` nếu vCenter dùng
   chứng chỉ hợp lệ từ CA nội bộ/công cộng.

Sau khi cấu hình xong, kiểm tra nhanh bằng nút **"Đồng bộ ngay"** trên giao diện (góc trên bên
phải danh sách vCenter) — nếu có lỗi kết nối/permission, thông báo lỗi cụ thể sẽ hiện ngay tại chip
vCenter tương ứng.

## Tài khoản read-only cho NetBackup (module Backup)

App chỉ **đọc** (GET) — không khởi chạy, huỷ hay sửa job/policy. Các bước trên NetBackup 9.x / 10.x
(Web UI, tên menu có thể khác đôi chút theo phiên bản):

1. Tạo user dịch vụ (local hoặc AD/LDAP), ví dụ `svc_nbu_ro`.
2. **Security → RBAC → Add role**: tạo role tuỳ chỉnh (ví dụ `Infra-ReadOnly`) chỉ gồm quyền **View**
   cho: *Jobs*, *NetBackup policies* (Protection → Policies), *Storage* (disk pools, storage units).
   Không cấp quyền Create / Update / Delete / Manage / Restore. Gán role cho user ở bước 1.
3. **Security → API keys**: tạo API key cho user này (đặt hạn dùng theo chính sách) → điền vào
   `NETBACKUP_i_API_KEY` (Secret). Không dùng API key thì khai báo `NETBACKUP_i_USERNAME` /
   `NETBACKUP_i_PASSWORD` (`DOMAIN_TYPE`: `unixpwd`, `vx`, `NT`…; `DOMAIN_NAME` theo domain của user) —
   app đăng nhập `/netbackup/login`, lấy token, đọc dữ liệu rồi `/logout`.
4. Mở firewall từ app (pod / host chạy Docker) tới primary server cổng **1556/TCP** (HTTPS).
5. **TLS.** `NETBACKUP_i_VERIFY_SSL=false`: kết nối vẫn mã hoá nhưng không xác thực chứng chỉ.
   Nên bật `true` và khai báo CA của NetBackup ở `NETBACKUP_i_CA_CERT` (file PEM; hoặc
   `NETBACKUP_CA_CERT` dùng chung cho mọi server):
   - Lấy CA: chạy `./k8s/nbu-ca.sh <nbu-host> <namespace>` — script lấy CA gốc `nbatd` qua
     `/netbackup/security/cacert` (không cần đăng nhập) và CA trung gian `broker` từ chuỗi chứng chỉ,
     kiểm tra cả hostname, rồi tạo/cập nhật ConfigMap `ico-nbu-ca`. Đối chiếu fingerprint CA gốc in ra với
     `/usr/openv/netbackup/bin/nbcertcmd -displayCACertDetail` trên primary.
   - `NETBACKUP_i_HOST` phải trùng tên trong chứng chỉ của primary server (thường là FQDN), không dùng IP.
   - Docker Compose: mount file vào container (ví dụ `./certs/nbu-ca.pem:/etc/netbackup-ca/ca.pem:ro`)
     rồi đặt `NETBACKUP_1_CA_CERT=/etc/netbackup-ca/ca.pem`. Kubernetes: `k8s/04-app.yaml` đã mount
     ConfigMap `ico-nbu-ca` tại `/etc/netbackup-ca` (xem mục Triển khai trên Kubernetes).
   - CA nội bộ của NetBackup không có extension *keyUsage*; Python 3.13 mặc định bật chế độ X.509
     strict và từ chối CA này (`CA cert does not include key usage extension`). App dùng một SSL
     context riêng cho NetBackup (`nbu_ssl_context`) bỏ cờ strict nhưng vẫn kiểm tra chuỗi chứng chỉ
     và hostname; kết nối vCenter không bị ảnh hưởng.
6. `NETBACKUP_i_API_VERSION`: phiên bản API (media type `application/vnd.netbackup+json;version=…`).
   Mặc định `8.0` (NetBackup 10.0); NetBackup mới hơn vẫn nhận phiên bản cũ. Lỗi **406** → chỉnh giá trị
   này theo bảng phiên bản API của hãng.

Sau khi cấu hình: vào **Backup → Nguồn & ngưỡng**, bấm **Đồng bộ** — lỗi xác thực (401), thiếu quyền
(403), sai phiên bản API (406) hay không kết nối được sẽ hiện ngay ở cột Trạng thái. Chạy thử không cần
NetBackup thật: `NETBACKUP_1_DEMO=true` (dữ liệu giả lập; độ phủ VM đối chiếu với inventory vCenter
đã đồng bộ).

## Triển khai trên Kubernetes (Rancher / RKE)

Hệ thống đang chạy trên cụm **Rancher (RKE)**, namespace **`cattle-monitoring-system`** (namespace có
sẵn của cụm — manifest không tạo namespace). Thư mục `k8s/` khớp với triển khai này:

| File | Nội dung |
|---|---|
| `01-configmap.yaml` | `ico-config` — biến không nhạy cảm: polling, vCenter, NetBackup (`NETBACKUP_1_*` đã cấu hình). |
| `02-secret.example.yaml` | Mẫu `ico-secrets` — Postgres, JWT, admin, mật khẩu vCenter, user/mật khẩu NetBackup. Bản thật `02-secret.yaml` nằm trong `.gitignore`. |
| `03-postgres.yaml` | StatefulSet + Service headless `ico-postgres` (PVC 20Gi, StorageClass `longhorn` — cụm không có StorageClass mặc định). |
| `04-app.yaml` | Deployment `ico-app` (init `wait-postgres`, mount CA NetBackup từ ConfigMap `ico-nbu-ca`) + Service NodePort `30080`. |
| `nbu-ca.sh` | Lấy CA của NetBackup (gốc `nbatd` + trung gian `broker`), kiểm tra, tạo/cập nhật ConfigMap `ico-nbu-ca`. |
| `kustomization.yaml` | Áp dụng 01–04 vào `cattle-monitoring-system`. |

Image được GitHub Action `.github/workflows/build-push.yml` build và đẩy lên GHCR mỗi khi push lên
`main`: `ghcr.io/luongminhphu/infra-capacity-ops-management-app:latest` (package **Public**, không cần
Image Pull Secret). Deployment dùng `imagePullPolicy: Always`, nên `rollout restart` là kéo bản mới nhất.

Các lệnh dưới đây chạy trên máy có `kubectl` trỏ vào cụm (ví dụ `infra-rke-cp01`) hoặc **Kubectl Shell**
trong Rancher UI.

```bash
NS=cattle-monitoring-system
```

### Cập nhật phiên bản mới (thao tác thường dùng)

1. Merge vào `main`, đợi workflow **Build and push image to GHCR** chạy xong (tab Actions).
2. Lưu digest image đang chạy để rollback và backup DB:
   ```bash
   kubectl -n $NS get pod -l app=ico-app -o jsonpath='{.items[0].status.containerStatuses[0].imageID}{"\n"}' > ~/ico-app-old-digest.txt
   ```
   ```bash
   kubectl -n $NS exec sts/ico-postgres -- sh -c 'pg_dumpall -U "$POSTGRES_USER"' > ~/ico-db-backup-$(date +%F).sql
   ```
3. Kéo image mới và đợi pod sẵn sàng:
   ```bash
   kubectl -n $NS rollout restart deploy/ico-app && kubectl -n $NS rollout status deploy/ico-app
   ```
4. Rollback nếu cần:
   ```bash
   kubectl -n $NS set image deploy/ico-app app=$(cat ~/ico-app-old-digest.txt)
   ```

Bảng mới / cột mới trong DB được tạo tự động khi app khởi động (`ensure_schema`) — không có bước migrate riêng.

### Chuyển hệ cũ (`vco-*`, repo `vmware-capacity-ops-management`) sang `ico-*`

Thực hiện một lần khi đổi tên repo thành `infra-capacity-ops-management`. Hệ mới chạy song song trong cùng
namespace, dữ liệu được chuyển nguyên vẹn; hệ cũ chỉ bị xoá ở bước cuối, sau khi hệ mới đã chạy ổn.

| Hệ cũ | Hệ mới |
|---|---|
| `vco-app`, `vco-postgres`, `vco-config`, `vco-secrets`, `nbu-ca` | `ico-app`, `ico-postgres`, `ico-config`, `ico-secrets`, `ico-nbu-ca` |
| DB `vmware_capacity_ops`, user `vmware_app` | DB `infra_capacity_ops`, user `ico_app` (mật khẩu mới) |
| Image `…/vmware-capacity-ops-management-app` | Image `…/infra-capacity-ops-management-app` |

**0. Chuẩn bị**
- Đổi tên repo trên GitHub: **Settings → General → Repository name** → `infra-capacity-ops-management`.
- Chạy workflow **Build and push image to GHCR** (tab Actions → *Run workflow*, hoặc merge vào `main`) để có
  image tên mới. Package mới trên GHCR mặc định **Private**: vào package → **Package settings → Change
  visibility → Public** (tắt "Inherit access from source repository" nếu có).
- Tất cả lệnh dưới đây chạy trên máy có `kubectl` trỏ vào cụm:
  ```bash
  NS=cattle-monitoring-system
  ```

**1. Lưu cấu hình cũ (để rollback)**
```bash
mkdir -p ~/vco-backup && chmod 700 ~/vco-backup && for r in deploy/vco-app sts/vco-postgres svc/vco-app svc/vco-postgres cm/vco-config secret/vco-secrets cm/nbu-ca; do kubectl -n $NS get $r -o yaml > ~/vco-backup/$(echo $r | tr / _).yaml; done; ls ~/vco-backup
```

**2. Tạo `ico-config`, `ico-nbu-ca`, `ico-secrets` từ cấu hình đang chạy** (không in giá trị; Postgres
được đặt user / DB mới và mật khẩu ngẫu nhiên, các giá trị khác — JWT, admin, vCenter, NetBackup — giữ nguyên):
```bash
kubectl -n $NS get cm vco-config -o json | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps({'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'ico-config','namespace':d['metadata']['namespace']},'data':d.get('data',{})}))" | kubectl apply -f -
```
```bash
kubectl -n $NS get cm nbu-ca -o json | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps({'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'ico-nbu-ca','namespace':d['metadata']['namespace']},'data':d.get('data',{})}))" | kubectl apply -f -
```
```bash
kubectl -n $NS get secret vco-secrets -o json | python3 -c "
import base64, json, secrets, sys
d = json.load(sys.stdin)
data = {k: base64.b64decode(v).decode() for k, v in d.get('data', {}).items()}
pw = secrets.token_urlsafe(24)
data.update({'POSTGRES_USER': 'ico_app', 'POSTGRES_DB': 'infra_capacity_ops', 'POSTGRES_PASSWORD': pw,
             'DATABASE_URL': 'postgresql+psycopg2://ico_app:%s@ico-postgres:5432/infra_capacity_ops' % pw})
print(json.dumps({'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
                  'metadata': {'name': 'ico-secrets', 'namespace': d['metadata']['namespace']}, 'stringData': data}))
" | kubectl apply -f -
```

**3. Dừng app cũ và backup dữ liệu cuối cùng** (dừng trước để không còn dữ liệu ghi thêm)
```bash
kubectl -n $NS scale deploy/vco-app --replicas=0
```
```bash
kubectl -n $NS exec sts/vco-postgres -- sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-privileges' > ~/vco-backup/vco-db.dump && ls -lh ~/vco-backup/vco-db.dump
```
```bash
kubectl -n $NS exec sts/vco-postgres -- sh -c 'pg_dumpall -U "$POSTGRES_USER"' > ~/vco-backup/vco-db-all.sql
```

**4. Tạo Postgres mới và khôi phục dữ liệu** — áp dụng `k8s/03-postgres.yaml` (Rancher: **Import YAML**
ở góc trên bên phải, dán nội dung file, chọn namespace `cattle-monitoring-system`; hoặc `kubectl apply -f`), rồi:
```bash
kubectl -n $NS rollout status sts/ico-postgres --timeout=300s
```
```bash
kubectl -n $NS exec -i sts/ico-postgres -- sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges' < ~/vco-backup/vco-db.dump
```
Đối chiếu số dòng một vài bảng giữa DB cũ và mới (hai kết quả phải giống nhau):
```bash
Q="select 'snapshot_runs',count(*) from snapshot_runs union all select 'vm_snapshots',count(*) from vm_snapshots union all select 'backup_jobs',count(*) from backup_jobs union all select 'license_entitlements',count(*) from license_entitlements"
```
```bash
for s in vco-postgres ico-postgres; do echo "== $s"; kubectl -n $NS exec sts/$s -- sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc \"$Q\""; done
```

**5. Chạy app mới** — xoá Service cũ để giải phóng NodePort `30080`, rồi áp dụng `k8s/04-app.yaml`
(Import YAML hoặc `kubectl apply -f`):
```bash
kubectl -n $NS delete svc vco-app
```
```bash
kubectl -n $NS rollout status deploy/ico-app --timeout=300s && kubectl -n $NS logs deploy/ico-app -c app --tail=20
```
Mở `http://<IP-node>:30080` — đăng nhập bằng tài khoản cũ, kiểm tra lịch sử xu hướng, license, trang Backup
(phạm vi, ngưỡng), bấm **Đồng bộ ngay** và **Backup → Nguồn & ngưỡng → Đồng bộ**.

**Rollback** (trước bước 6): `kubectl -n $NS scale deploy/ico-app --replicas=0`,
`kubectl -n $NS delete svc ico-app`, `kubectl apply -f ~/vco-backup/svc_vco-app.yaml` (xoá trường
`resourceVersion`, `uid`, `clusterIP`, `clusterIPs` trong file trước khi apply), rồi
`kubectl -n $NS scale deploy/vco-app --replicas=1`.

**6. Xoá hệ cũ** — sau vài ngày hệ mới chạy ổn; giữ thư mục `~/vco-backup` ở nơi an toàn:
```bash
kubectl -n $NS delete deploy/vco-app sts/vco-postgres svc/vco-postgres cm/vco-config cm/nbu-ca secret/vco-secrets
```
```bash
kubectl -n $NS delete pvc pgdata-vco-postgres-0
```
Package GHCR cũ `vmware-capacity-ops-management-app` có thể xoá trong GitHub → Packages.

### Triển khai lại từ đầu (cụm mới hoặc khôi phục)

1. **Secret** — tạo từ mẫu rồi điền giá trị thật (mật khẩu Postgres, `JWT_SECRET`, `ADMIN_PASSWORD`,
   mật khẩu vCenter, mật khẩu `ita_collector_user` NetBackup):
   ```bash
   cp k8s/02-secret.example.yaml k8s/02-secret.yaml
   ```
   `DATABASE_URL` phải khớp đúng `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`.
2. **ConfigMap** — `k8s/01-configmap.yaml` đã có giá trị NetBackup đang dùng; kiểm tra lại khối vCenter
   (`VCENTER_i_KEY` / `NAME` / `HOST`) cho đúng môi trường.
3. **CA của NetBackup** (cần `openssl`, `curl`, `python3`; chạy từ máy kết nối được `nbu-primary.hsc.com.vn:1556`):
   ```bash
   ./k8s/nbu-ca.sh nbu-primary.hsc.com.vn cattle-monitoring-system
   ```
4. **Áp dụng** và theo dõi:
   ```bash
   kubectl apply -k k8s/
   ```
   ```bash
   kubectl -n $NS get pods -l 'app in (ico-app,ico-postgres)' -w
   ```
5. Truy cập `http://<IP-node>:30080`, đăng nhập `admin`, bấm **Đồng bộ ngay** (vCenter) và
   **Backup → Nguồn & ngưỡng → Đồng bộ** (NetBackup). Khôi phục dữ liệu cũ nếu cần:
   ```bash
   kubectl -n $NS exec -i sts/ico-postgres -- sh -c 'psql -U "$POSTGRES_USER" -d postgres' < ~/ico-db-backup-<ngày>.sql
   ```

Trước khi `kubectl apply -k` lên cụm đang chạy, xem trước khác biệt so với cấu hình thật để không ghi
đè giá trị đã chỉnh tay:
```bash
kubectl diff -k k8s/
```

### NetBackup trên cụm này

- Biến đang dùng: `NETBACKUP_1_HOST=nbu-primary.hsc.com.vn`, `PORT=1556`, `VERIFY_SSL=true`,
  `CA_CERT=/etc/netbackup-ca/ca.pem`, `API_VERSION=8.0`, đăng nhập bằng user `ita_collector_user`
  (`DOMAIN_TYPE` / `DOMAIN_NAME` trong ConfigMap, mật khẩu trong Secret). Chỉ dùng tên biến **có số**
  (`NETBACKUP_1_*`) — biến không số bị bỏ qua.
- Chứng chỉ NetBackup: leaf `nbu-primary.hsc.com.vn` ← trung gian `broker` ← gốc `nbatd` (hết hạn 2045).
  Khi NetBackup cấp lại CA, chạy lại `./k8s/nbu-ca.sh` rồi `rollout restart`.
- Đổi mật khẩu NetBackup (không lưu vào lịch sử shell):
  ```bash
  read -s -p "Mat khau ita_collector_user: " P; echo
  ```
  ```bash
  kubectl -n $NS patch secret ico-secrets --type merge -p "{\"stringData\":{\"NETBACKUP_1_PASSWORD\":\"$P\"}}" && unset P && kubectl -n $NS rollout restart deploy/ico-app
  ```
- Kiểm tra mạng tới NetBackup từ từng node (firewall nhìn IP node, không phải IP pod) — kết quả `401` là đạt:
  ```bash
  kubectl -n $NS run nbu-test --rm -i --restart=Never --image=curlimages/curl --overrides='{"spec":{"nodeName":"<node>"}}' -- curl -sk -o /dev/null -w "%{http_code}\n" --max-time 10 https://nbu-primary.hsc.com.vn:1556/netbackup/
  ```
- Không truyền mật khẩu qua `kubectl run` (lệnh và output được ghi vào log container).

### Lưu ý

- **Giữ `replicas: 1`** cho `ico-app` — polling vCenter / NetBackup chạy trong tiến trình app, không có
  khoá phối hợp giữa nhiều pod.
- Sửa ConfigMap / Secret xong phải `kubectl -n $NS rollout restart deploy/ico-app` (env không tự nạp lại).
- `cattle-monitoring-system` là namespace của Rancher Monitoring: **không gỡ / cài lại Rancher Monitoring**
  khi chưa backup DB, vì thao tác đó có thể xoá cả namespace. Khi chuyển app sang namespace riêng, đổi
  `namespace:` trong `kustomization.yaml` và các file 01–04, tạo lại Secret / `ico-nbu-ca` ở namespace mới,
  khôi phục DB từ bản `pg_dumpall`.
- PVC Postgres `Pending`: cụm không có StorageClass mặc định — `03-postgres.yaml` chỉ định `longhorn`.
  Nếu đổi sang StorageClass khác: `kubectl get storageclass`, sửa `storageClassName`, xoá StatefulSet và PVC
  đang Pending rồi áp dụng lại (trường này không sửa được trên PVC đã tạo).
- **Repo và package GHCR private:** cụm kéo image bằng Secret `ghcr-pull-secret` (đã khai báo trong
  `04-app.yaml`). Tạo PAT **classic** chỉ có quyền `read:packages` (GitHub → Settings → Developer settings →
  Personal access tokens → Tokens (classic)), rồi:
  ```bash
  read -s -p "GHCR token: " T; echo
  ```
  ```bash
  kubectl -n $NS create secret docker-registry ghcr-pull-secret --docker-server=ghcr.io --docker-username=luongminhphu --docker-password="$T" --dry-run=client -o yaml | kubectl apply -f - && unset T
  ```
  Token hết hạn → pod mới báo `ImagePullBackOff`: tạo token mới và chạy lại hai lệnh trên.

## Lưu ý về dữ liệu lịch sử & polling

- Mỗi lần đồng bộ (thủ công qua "Đồng bộ ngay"/"Sync all", hoặc tự động theo
  `POLL_INTERVAL_MINUTES`, mặc định 60 phút) tạo một `SnapshotRun` mới trong Postgres — đây là
  nguồn dữ liệu cho biểu đồ xu hướng (Executive View), panel "Biến động tài nguyên theo thời
  gian" (3/6/12 tháng) và dự báo "còn bao lâu thì hết công suất".
- Import CSV/JSON thủ công cũng được lưu thành `SnapshotRun` riêng (nguồn `import`) nên vẫn góp
  phần vào lịch sử xu hướng.
- `lastActivityDays`/`powerOffDays` của VM được tính dựa trên **lịch sử polling của chính ứng
  dụng** (vCenter API không trả trực tiếp "đã tắt bao lâu") — nghĩa là ngay sau khi triển khai lần
  đầu, các chỉ số này sẽ cần vài chu kỳ polling để chính xác dần theo thời gian thực tế.
- Dữ liệu import (CSV/JSON) và dữ liệu live từ vCenter là **hai tập dữ liệu làm việc loại trừ lẫn
  nhau tại một thời điểm** trên giao diện: chọn một vCenter (hoặc "Tất cả") sẽ tải dữ liệu live;
  tải file CSV/JSON sẽ chuyển sang chế độ xem dữ liệu import. Đây là hành vi có chủ đích (giữ
  nguyên từ bản single-file gốc), không phải lỗi.

## Bảo mật

- Đăng nhập cơ bản bằng username/password (bcrypt hash), phiên làm việc qua JWT lưu trong cookie
  `httpOnly` (không truy cập được từ JavaScript, giảm rủi ro XSS đánh cắp session).
- Đặt `COOKIE_SECURE=true` khi triển khai sau HTTPS (reverse proxy/TLS terminator) để cookie chỉ
  gửi qua kết nối mã hoá.
- Mật khẩu vCenter và `JWT_SECRET`/`ADMIN_PASSWORD` chỉ nằm trong `.env` (đã thêm vào
  `.gitignore`), không commit vào Git.
- Tài khoản vCenter chỉ cần quyền **Read-Only** — không có rủi ro ứng dụng vô tình thay đổi cấu
  hình hạ tầng.
- Tài khoản NetBackup chỉ cần role **View** (Jobs, Policies, Storage); API key / mật khẩu chỉ nằm
  trong `.env` / Secret, app chỉ gọi API GET (cộng `/login`, `/logout` khi không dùng API key).

## Kiểm thử đã thực hiện trong quá trình phát triển

Đã kiểm thử trực tiếp trong môi trường phát triển (không dùng Docker do sandbox không có Docker):
chạy backend bằng `uvicorn` + PostgreSQL cục bộ, 4 vCenter ở **chế độ DEMO** (dữ liệu giả lập, vì
sandbox phát triển không truy cập được vCenter thật của bạn) — xác nhận: đăng nhập, đồng bộ dữ liệu
4 "vCenter" demo (52 host / 406 VM tổng cộng), cả hai góc nhìn Executive/Technical render đúng số
liệu và biểu đồ, chuyển tab Dashboard/Compliance/Inventory/What-if hoạt động, chạy thử what-if
"Add Host" cho ra kết quả before/after chính xác.

Module Backup được kiểm thử với NetBackup **chế độ DEMO** (payload giả lập đúng cấu trúc REST API,
đi qua cùng bộ parser) và một nguồn trỏ tới địa chỉ không kết nối được để kiểm tra hiển thị lỗi;
`backend/tests/test_backup.py` kiểm tra parser, tiêu chí đánh giá, đồng bộ lặp không nhân bản job và
các API/báo cáo. Kết nối tới NetBackup thật (API key, RBAC, phiên bản API) cần xác nhận trên môi
trường của bạn.

**Chưa kiểm thử được** (cần môi trường thật của bạn để xác nhận): kết nối tới 4 vCenter 8.x thật
qua pyVmomi với tài khoản `infra.mon@vsphere.local`, chạy bằng `docker compose up` thật sự (viết
đúng theo chuẩn Compose nhưng không chạy được trong sandbox phát triển do không có Docker daemon).
Khuyến nghị bước đầu tiên sau khi nhận bàn giao: chạy `docker compose up -d --build`, đặt một
vCenter thật với `DEMO=false`, bấm "Đồng bộ ngay" và kiểm tra log (`docker compose logs -f app`)
nếu có lỗi kết nối/permission.
