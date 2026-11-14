---
name: capacity-math
description: Dùng khi sửa hoặc thêm phép tính capacity / compliance / what-if / quy hoạch / sizing — backend/app/capacity.py, planning.py, sizing.py, sizing_advisory.py, licensing/, history.py và frontend/js/core.js. Covers overcommit, HT factor, status thresholds, N+1, quarterly growth projection, finding codes (FNV-1a).
---

# Toán capacity của ICO

Mọi con số trong dashboard và báo cáo dùng để **ra quyết định mua sắm**, nên sai số là lỗi
nghiêm trọng. `backend/app/capacity.py` là bản Python của `frontend/js/core.js` và **phải cho
kết quả giống hệt** — sửa một bên thì sửa bên kia trong cùng thay đổi.

## Định nghĩa

Chỉ tính VM **đang bật** (`isPoweredOn`) cho CPU/RAM; storage tính trên mọi VM.

```
logicalCores   = Σ host.cores × max(1, htFactor)          # htFactor mặc định 2.0
cpuOvercommit  = Σ vm.vcpu  / logicalCores
ramOvercommit  = Σ vm.ramGB / Σ host.ramGB
cpuPct         = cpuOvercommit / cpuOvercommitMax × 100     # mặc định max = 6.0
ramPct         = ramOvercommit / ramOvercommitMax × 100     # mặc định max = 1.0
```

`cpuPct` / `ramPct` là **% của ngưỡng overcommit cho phép**, không phải % sử dụng thực tế
(quickStats chỉ dùng cho phát hiện VM idle). Mẫu số bằng 0 → `NaN` → trạng thái `NA`, không
được chia ra `Infinity` hay báo lỗi.

## Ngưỡng trạng thái (`get_status_for_pct(pct, warn, high)`)

| Điều kiện | text | cls |
|---|---|---|
| không hữu hạn | NA | `na` |
| pct > 100 | VƯỢT NGƯỠNG | `crit` |
| pct ≥ high | CẢNH BÁO | `bad` |
| pct ≥ warn | THEO DÕI | `warn` |
| còn lại | AN TOÀN | `ok` |

CPU/RAM dùng `warn=80, high=100` (pct đúng 100 → CẢNH BÁO). Datastore dùng `ds_warn=80,
ds_high=90`. `combine_status` lấy mức xấu nhất theo thứ tự `crit > bad > warn > ok > na`
(bỏ qua `na` khi có trạng thái khác). Storage chỉ được đánh giá khi `policy.include_storage`
bật (mặc định tắt vì SAN dùng chung + thick provisioning cho số liệu sai lệch).

## Dự phóng & quy hoạch (`planning.py`, `capacity.py`)

- Tăng trưởng **lãi kép theo quý**: `grow(v, g%, m) = v × (1 + g/100)^(m/3)`.
- Số tháng tới ngưỡng: `months = log(target/current) / log(1 + g/100) × 3`; `g ≤ 0` → `None`
  (không bao giờ chạm); `current ≥ target` → `0`.
- Kịch bản mặc định `(5, 10, 20)` %/quý = Thấp / Cơ sở / Cao. Có lịch sử ≥ 30 ngày thì thêm
  kịch bản "Xu hướng" từ dữ liệu thực.
- `_nodes_needed`: số node thêm nhỏ nhất `k` sao cho **cả CPU và RAM** đều thoả
  (1) `demand / ((cap + k·node) × ocMax) ≤ target%` và
  (2) **N+1**: mất host lớn nhất (node mới có thể là host lớn nhất) vẫn `≤ 100%`.
  Cluster < 2 host không bao giờ đạt N+1. Phương án nâng RAM tính theo bội số
  `RAM_STEP_GB = 64`.

## Mã đối soát (finding code)

`PREFIX-XXXXX`: prefix theo loại (`HCPU, HRAM, CCPU, CRAM, IDLE, POFF, OVP`, khác → `FND`)
+ 5 ký tự hex đầu của FNV-1a 32-bit trên chuỗi UTF-8 `"type|vcenterKey|cluster|entity"`.
Mã phải **ổn định** — cùng một vấn đề có cùng mã trên web, PDF Vận hành và PDF Kỹ thuật.
Không đổi thứ tự trường, cách nối hay prefix nếu không có lý do nghiệp vụ; đổi thì đổi cả
`findingCode()` trong `core.js`.

## Ví dụ kiểm tra (dùng làm test)

1. 2 host × 32 core, HT 2 → 128 logical; VM bật tổng 384 vCPU → overcommit 3.0;
   max 6.0 → `cpuPct = 50` → AN TOÀN.
   RAM 2 × 512 = 1024 GB, VM bật 900 GB → 0.879; max 1.0 → `ramPct ≈ 87.9` → THEO DÕI.
2. `_months_to_target(60, 80, 10)` = log(80/60)/log(1.1) × 3 ≈ **9.06 tháng**.
3. Cluster 3 host × 32 core (HT 2 → 64 logical/host, 192 tổng), demand 900 vCPU, ocMax 6,
   target 80 %: bình thường 900/1152 = 78.1 % (đạt) nhưng N+1 900/768 = 117 % (không đạt)
   → cần **k = 1** node 32 core (N+1: 900/1152 = 78.1 %).

## Quy trình khi sửa

1. Viết test với số liệu cụ thể như trên **trước**, trong `backend/tests/`.
2. Sửa `capacity.py` / `planning.py`; sửa hàm tương ứng trong `frontend/js/core.js`.
3. Kiểm tra mọi nơi tiêu thụ: `report_pdf.py`, `report_planning.py`, `excel_*`, `views/*.js`.
4. `python -m pytest tests -q` trong `backend/`.
