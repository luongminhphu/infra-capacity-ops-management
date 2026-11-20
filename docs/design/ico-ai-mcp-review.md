# Rà soát module AI và MCP server — hiện trạng, hạn chế, đề xuất

Ngày rà soát: 20/11/2026.

Cách làm:
- Đọc code `mcp_server.py`, `mcp_config.py`, `observability.py`, `ai_platform.py`, `ask_ico.py`, `ai_tasks.py` / `ai_data.py` /
  `ai_advanced.py`, `shared/ai_transport.py`.
- Chạy app ở chế độ demo, tạo lưu lượng như người dùng thật (20 endpoint × 3 lượt, 2 báo cáo PDF, đồng bộ mọi nguồn).
- Gọi `/mcp` như một AI client: `initialize`, `tools/list`, đủ 8 tool, gọi sai tool.
- Đối chiếu với checklist của skill `mcp-builder` (anthropics/skills, Apache-2.0): đặt tên, annotation, `outputSchema`,
  phân trang, lỗi có hướng dẫn, đánh giá (evaluation).

Log production chưa đọc được từ phiên này: không có URL / token MCP của môi trường thật. Hướng dẫn đọc ở cuối tài liệu (mục 6).

---

## 1. Cơ chế vận hành hiện tại (tóm tắt)

| Thành phần | Làm gì | Lưu ở đâu |
|---|---|---|
| `observability.CaptureHandler` | Bắt mọi log, che secret (`redact`) | INFO+: vòng nhớ 5.000 dòng **trong RAM từng pod**; WARNING+: bảng `app_logs` (giữ `OBS_RETENTION_DAYS`) |
| `TimingMiddleware` + SQL timing | Thời gian request theo route, thời gian DB, số câu SQL | Gom 5 phút → `perf_buckets`; request chậm / SQL chậm **trong RAM từng pod** |
| `mcp_server` | JSON-RPC Streamable HTTP, không trạng thái, token `ico_mcp_…` (chỉ admin) | Mỗi lần gọi tool ghi `audit_logs`; giới hạn tần suất trong RAM |
| 8 tool MCP | `app_overview`, `search_logs`, `error_summary`, `endpoint_performance`, `performance_trend`, `slow_requests`, `slow_queries`, `sync_history` | Chỉ đọc; che host / IP (`MCP_MASK_HOSTS`) |
| `ai_platform.run()` | Chính sách → che tên → cache → gọi API → ghi `ai_runs` (không lưu nội dung) → cảnh báo 50 USD | `ai_runs`, `ai_cache` |
| Hỏi ICO (`ask_ico`) | Vòng tool-use ≤ 5 lượt với 9 công cụ chỉ đọc theo quyền người hỏi | — |

Hai luồng AI hiện **tách rời**:
- MCP: AI bên ngoài đọc sức khoẻ app.
- `ai_platform`: app gọi Claude.

MCP không thấy gì về `ai_platform` (lỗi, độ trễ, bị chặn, chi phí). Hỏi ICO cũng không thấy các module mới (Phần cứng, Hệ thống & Owner, IPAM, Rà soát).

---

## 2. Bằng chứng từ log và số đo (demo, 20/11/2026)

| # | Quan sát (qua tool MCP) | Ý nghĩa |
|---|---|---|
| E1 | `error_summary` (2 giờ): 40 dòng WARNING; **39 dòng là của `weasyprint`**. 36 dòng là “Using fontTools instead of HarfBuzz-Subset…” (mỗi font, mỗi PDF), 3 dòng là `word-break:break-word` không hợp lệ (`report_backup.py:435`). | Bảng `app_logs` bị nhiễu: lỗi thật chìm. `Dockerfile` không cài `libharfbuzz-subset0`, nên production gặp y hệt. |
| E2 | `search_logs` INFO: 60 dòng gần nhất **đa số là `fontTools.subset`** (“glyf pruned”, “GDEF subsetted”…). | Mỗi PDF đẩy hàng chục dòng rác vào vòng 5.000 dòng. Log của scheduler / collector bị đẩy ra sớm. |
| E3 | `slow_requests`: `/api/backup/report/pdf` mất 5,7–6,0 s, `/api/reports/pdf` mất 3,4–3,7 s; DB chỉ 1–2 ms. | Thời gian nằm ở WeasyPrint. Cắt font bằng fontTools chậm hơn HarfBuzz (liên quan E1). |
| E4 | `POST /api/sources/sync-all`: **683 câu SQL** trong một request (146 ms DB ở demo). | Đồng bộ chạy ngay trong HTTP request; với vCenter thật request sẽ kéo dài, có thể vượt timeout của App Proxy. |
| E5 | `sync_history` chỉ có vCenter + NetBackup. | Không thấy Pure / NetApp, Dell OME, GestióIP, Proxmox / Nutanix, dựng sổ CI, gán owner, rà soát, cảnh báo. Dòng tổng kết `job=poll … cmdb_ms=… owners_add=…` chỉ là text INFO trong RAM. |
| E6 | `app_overview` p95 = 5.000 ms, p99 = 6.028 ms. | Phân vị lấy theo **cận trên của ô histogram**: thô ở khoảng 1–10 s. |
| E7 | Gọi tool không tồn tại → lỗi giao thức `-32602`. Tool lỗi chỉ trả “Tool error: TypeError”. | Trái khuyến nghị MCP: lỗi tool nên nằm trong `result` với `isError` và gợi ý cách sửa. |
| E8 | `resources/list` → `-32601`. Tool không có `outputSchema` / `structuredContent`; annotation thiếu `idempotentHint` / `destructiveHint`; tên tool không có tiền tố. | Client dùng nhiều MCP server dễ nhầm tên (`search_logs` trùng với server khác); client không tự kiểm được kiểu dữ liệu trả về. |

---

## 3. Hạn chế theo nhóm

### 3.1 Log và quan sát
1. **Nhiễu log thư viện** (E1, E2): không đặt mức log cho `fontTools`, `weasyprint`.
2. **Không nối được log ↔ request ↔ job**: `request_id` / `job-…` có trong định dạng log stdout nhưng **không lưu vào `app_logs`**.
   AI không lần được “lỗi này thuộc request / vòng đồng bộ nào”.
3. **Dữ liệu theo pod, không biết pod nào**: vòng INFO, request chậm, SQL chậm và giới hạn tần suất MCP nằm trong RAM. Request MCP rơi
   vào pod bất kỳ, thường không phải pod leader đang chạy job nền, nên log INFO của job **không đọc được**. `app_logs` cũng không có cột pod.
4. **Không có sổ lần chạy job thống nhất** (E5): mỗi nguồn ghi một kiểu (`snapshot_runs`, `backup_sync_runs`, `*_source_state`), job dựng
   CI / owner / rà soát / cảnh báo không ghi gì ngoài một dòng INFO.
5. Phân vị thô (E6). `error_summary` tốn 2 truy vấn mỗi nhóm (N+1). `contains` không escape `%` / `_`.

### 3.2 MCP server (đối chiếu checklist `mcp-builder`)
1. Tên tool không có tiền tố dịch vụ (`app_overview` → nên là `ico_app_overview`).
2. Thiếu `outputSchema` + `structuredContent`; annotation chưa đủ (`idempotentHint: true`, `destructiveHint: false`).
3. Không phân trang (`offset`, `has_more`, `total_count`) — chỉ có `limit`; nội dung lớn bị cắt cứng ở 120.000 ký tự.
4. Lỗi tool không có hướng dẫn (E7).
5. **Phạm vi hẹp**: chỉ log + hiệu năng. Không có:
   - trạng thái nguồn dữ liệu (đã có sẵn `source_status.py`);
   - độ tươi dữ liệu;
   - lần chạy job;
   - sức khoẻ module AI;
   - kiểm tra cấu hình (PUBLIC_URL, SMTP, cookie Secure, leader, HarfBuzz).

   AI client phải đoán từ log text.
6. Chỉ 2 prompt (`health_review`, `error_triage`). Thiếu kịch bản cho đồng bộ lỗi, module AI, hồi quy sau triển khai.
7. Chưa có **bộ đánh giá** (evaluation): không đo được AI client dùng MCP có trả lời đúng không, và không phát hiện thoái lui khi đổi tool.

### 3.3 Module AI (`ai_platform`, Hỏi ICO)
1. **MCP không thấy AI**: lỗi / bị chặn / độ trễ / chi phí của `ai_runs` chỉ xem được ở Cài đặt → AI & MCP.
2. **Che tên chưa phủ nguồn mới**: `Masker` chỉ biết tên host, VM, hostname khách, client backup. Chưa che:
   - tên datastore, mảng / volume storage;
   - tên thiết bị Dell OME, hostname IPAM;
   - **email owner / backup / support** (dữ liệu cá nhân) nếu đi vào dữ kiện.
3. **Hỏi ICO thiếu công cụ cho module mới**:
   - Phần cứng (OME: trạng thái, bảo hành, ổ dự báo hỏng);
   - Hệ thống & Owner (VM của hệ thống X, owner của VM);
   - kỳ rà soát;
   - trạng thái nguồn;
   - tra IP (IPAM).

   Danh sách `source` của `viec_can_lam` lệch giữa schema và code:
   - schema thiếu `hardware`, `review`;
   - code thiếu `asset`, `hardware`, nên hỏi theo `asset` bị bỏ lọc và trả **mọi** việc.

   Nên lấy cả hai từ `inbox.SOURCE_LABELS`.
4. `ai_runs` không có `request_id` → không nối được với log lỗi. Không có đánh giá chất lượng câu trả lời (👍 / 👎) và không có bộ câu hỏi mẫu
   để kiểm thử khi đổi model / prompt (`PROMPT_VERSION` dùng chung mọi tác vụ).
5. Giữ chỗ ngân sách (`_reserved`) theo từng process: nhiều pod gọi cùng lúc có thể vượt hạn mức một chút (tối đa số pod × 2 USD).

### 3.4 Hiệu năng liên quan
- PDF 3,5–6 s (E3), phần lớn do cắt font.
- “Đồng bộ ngay” chạy trong request (E4).

---

## 4. Đề xuất cải tiến (theo thứ tự ưu tiên)

### Đợt A — Dọn log, nối vết (rủi ro thấp, làm ngay)
| Việc | Chi tiết | Kết quả mong đợi |
|---|---|---|
| A1 | `Dockerfile` thêm `libharfbuzz-subset0`. `main.py` đặt `fontTools` = WARNING, `weasyprint` = ERROR (warning CSS vẫn bắt được bằng test). Sửa `word-break:break-word` → `overflow-wrap:anywhere`. | `app_logs` bớt ~90 % dòng rác (E1). Vòng INFO giữ được log nghiệp vụ. PDF nhanh hơn (đo lại E3). |
| A2 | `app_logs` thêm cột `request_id`, `pod` (additive, nullable); `CaptureHandler` lấy từ context + `HOSTNAME`. | Lần được lỗi ↔ request ↔ job, biết pod nào. |
| A3 | Sổ chạy job `job_runs` lưu: job, họ nguồn, khoá, pod, thời điểm, thời lượng, trạng thái, bộ đếm JSON, lỗi đã che. Ghi ở mọi `_sync_*_task`, `cmdb.build_all`, owner rules, rà soát, cảnh báo, retention. Giữ 90 ngày. | Thấy đủ mọi luồng nền (E5); nền cho tool MCP mới. |
| A4 | Sửa nhỏ: `error_summary` 1 truy vấn; escape `%` / `_` cho `contains`; thêm ô histogram 1.500 / 2.000 / 3.500 / 7.500 ms. | Số đo chính xác hơn, truy vấn ít hơn. |

### Đợt B — MCP đúng chuẩn và tích hợp đủ luồng
| Việc | Chi tiết |
|---|---|
| B1 | Đổi tên tool sang `ico_*`, giữ tên cũ làm bí danh một bản phát hành. Annotation đủ 4 cờ. `outputSchema` + `structuredContent` (vẫn kèm text). |
| B2 | Phân trang `offset` / `has_more` / `next_offset` / `total_count` cho `search_logs`, `error_summary`, `sync_history`. Lỗi tool trả `isError` kèm gợi ý (“dùng since_minutes ≤ 43.200”, “tool hợp lệ: …”). |
| B3 | Tool mới, chỉ đọc, số tổng hợp, đã che (xem mục 4.1). |
| B4 | Prompt mới: `sync_triage` (nguồn lỗi / chậm → nguyên nhân → bước sửa), `ai_module_review` (lỗi, bị chặn, chi phí, độ trễ theo tác vụ), `release_regression` (so số đo trước / sau đổi `appVersion`). |
| B5 | Dữ liệu theo pod: mọi output ghi `pod`; `slow_requests` / `slow_queries` gửi định kỳ vào DB (mẫu tối đa 200 / 5 phút) để mọi pod cùng thấy. Giới hạn tần suất MCP giữ theo pod (đã ghi rõ trong tài liệu). |
| B6 | Bộ đánh giá 10 câu hỏi trên dữ liệu demo theo `mcp-builder` (`docs/design/ico-mcp-eval.xml`). Chạy tay bằng `scripts/evaluation.py` khi đổi tool / model. Test pytest kiểm `tools/list` khớp schema, annotation đủ, mọi tool chạy được trên DB demo. |

#### 4.1 Tool MCP mới đề xuất (B3)
| Tool | Nguồn | Trả về |
|---|---|---|
| `ico_source_status` | `source_status.status_all` | Mọi nguồn (vCenter, Proxmox / Nutanix, NetBackup, Pure / NetApp, OME, IPAM): trạng thái ok / error / stale / never, lần OK cuối, lỗi đã che |
| `ico_job_runs` | `job_runs` (A3) | Lần chạy job theo họ / trạng thái, thời lượng p50 / p95, bộ đếm |
| `ico_data_freshness` | snapshot mới nhất, `cmdb` stats, độ phủ owner | Tuổi dữ liệu mỗi nguồn, số CI / retired, % VM có owner |
| `ico_ai_usage` | `ai_runs` | Theo tác vụ: số lần gọi / cache / bị chặn / lỗi, độ trễ p50 / p95, chi phí tháng / hạn mức, lỗi gần nhất (chỉ thông điệp an toàn, **không** nội dung) |
| `ico_config_check` | Settings | Cờ cấu hình không bí mật: PUBLIC_URL, cookie Secure, SMTP có / không, scheduler + leader, HarfBuzz có / không, tính năng bật, `MCP_MASK_HOSTS` |
| `ico_trace_request` | `app_logs` (A2) + `perf` | Mọi log của một `request_id` / `job-…` |

### Đợt C — Module AI
| Việc | Chi tiết |
|---|---|
| C1 | Che tên phủ nguồn mới: thêm datastore, storage, thiết bị OME, hostname IPAM, tên hệ thống. Thêm che **email** (`<email:…>`) mặc định. Test: dữ kiện chứa email / datastore → không lọt ra payload. |
| C2 | Hỏi ICO thêm công cụ: `phan_cung` (OME), `he_thong` (hệ thống & owner theo quyền), `trang_thai_nguon`, `tra_ip` (IPAM + CI). `viec_can_lam` bổ sung `hardware`, `review`. Đi qua cùng `Toolbox`, cùng quyền. |
| C3 | `ai_runs` thêm `request_id`, `prompt_version` theo tác vụ, `feedback` (👍 / 👎 từ giao diện); báo cáo tỷ lệ hài lòng theo tác vụ ở Cài đặt → AI & MCP và qua `ico_ai_usage`. |
| C4 | Giữ chỗ ngân sách qua DB (một dòng `UPDATE … SET reserved = reserved + x WHERE reserved + spent + x <= budget`) thay cho biến trong process. |

### Đợt D — Hiệu năng đi kèm
- D1: đo lại PDF sau A1. Nếu vẫn > 3 s: cache PDF theo (phạm vi, phiên bản dữ liệu), hoặc dựng nền và đưa vào Kho báo cáo.
- D2: “Đồng bộ ngay” chạy nền, trả `202` + mã lần chạy (`job_runs`). Giao diện theo dõi tiến độ. Tránh request dài qua App Proxy.

---

## 5. Thứ tự làm và cách nghiệm thu
| Đợt | PR | Nghiệm thu |
|---|---|---|
| A | 1 PR | Demo tạo 2 PDF → `error_summary` không còn nhóm `weasyprint`. `search_logs` INFO không còn `fontTools`. Log WARNING có `request_id`, `pod`. `job_runs` đủ mọi họ nguồn + job dựng CI / owner / rà soát. |
| B | 1 PR | `tools/list` có tiền tố, annotation, `outputSchema`. 6 tool mới chạy trên demo. Bộ đánh giá 10 câu đạt ≥ 8/10 với model mặc định. Test schema MCP xanh. |
| C | 1 PR | Test che email / datastore. Hỏi ICO trả lời được “Owner của hệ thống X?”, “Máy chủ nào sắp hết bảo hành?”, “IP a.b.c.d là gì?” trên demo. |
| D | 1 PR | PDF backup demo < 3 s hoặc lấy từ cache. Đồng bộ ngay trả `202`. |

Quy tắc giữ nguyên trong mọi đợt:
- **chỉ đọc**;
- che host / IP / email trước khi ra ngoài;
- không trả secret;
- token MCP chỉ cho admin;
- mọi tác vụ AI qua `ai_platform.run()`.

---

## 6. Đọc log production bằng MCP (cho người vận hành)
1. Cài đặt → AI & MCP: bật MCP, tạo token (hạn ngắn, vd 7 ngày).
2. Trong Claude Code trên máy quản trị, chạy lệnh dưới, rồi gọi prompt `health_review` hoặc `error_triage`:

   ```bash
   claude mcp add --transport http ico https://<PUBLIC_URL>/mcp --header "Authorization: Bearer <token>"
   ```

   Đừng dán token vào chat.
3. Kết quả (lỗi theo nhóm, endpoint chậm, lần đồng bộ lỗi) dùng để chọn thứ tự đợt A–D. Thu hồi token khi xong.
