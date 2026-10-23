# Bot và source build GKI mới — 24/10/2026

## Triển khai theo thứ tự

1. Đảm bảo repo `GKI_KernelSU_SUSFS` đã tích hợp upstream `ed9ca42` được review, commit và push lên nhánh bot dùng. Lần sửa bot này không tự commit/push hoặc thay đổi repo build.
2. Commit/push repo bot sau khi review. Giữ `GITHUB_OWNER`, `GKI_REPO`, `GKI_DEFAULT_BRANCH` trỏ đúng fork/nhánh đã cập nhật; `GKI_WORKFLOWS` vẫn trỏ workflow tổng `main.yml`. Không cần biến môi trường mới, không đổi token.
3. Trên VPS, cập nhật source và build lại dashboard bằng `bash run.sh deploy`, rồi kiểm tra `bash run.sh status` và `bash run.sh logs`. Các lệnh này chỉ là hướng dẫn, chưa được chạy lên VPS trong lần sửa source.
4. Dùng `/gki` kiểm tra một phiên bản trước khi build nhiều bản. Chỉ compile/flash trên thiết bị phù hợp Android/KMI/patch level; các test offline không xác nhận kernel boot được.

Bot mới gửi input native. Nếu repo build trên GitHub còn bản cũ, GitHub có thể từ chối input; không cập nhật bot trước workflow. Các lần gửi đã thành công hoặc kết quả HTTP không chắc chắn không bị tự gửi lại.

## Menu thay đổi gì?

- Biến thể: BakaSU, SukiSU, SukiSU(40726), SukiSU(40548), KittiSU, Official. Bỏ MKSU chưa có phần triển khai. ReSukiSU trong job cũ được chuẩn hóa thành BakaSU khi gửi lại một job chưa dispatch.
- Bỏ menu Stable/Dev vì workflow tự chọn branch hoặc commit cố định; không hiển thị lựa chọn không còn tác dụng.
- Chế độ: **KSU + SUSFS**, **chỉ KSU**, hoặc **GKI thuần không KSU/SUSFS**. `/gki` giữ mặc định chỉ KSU như trước; `/build` mặc định bật cả KSU và SUSFS.
- Thêm NoMount, networking, NTSync, patch CVE tùy chọn và lựa chọn artifact đầy đủ/AnyKernel3. `/gki` còn có xuất patch SUSFS; chỉ hiện khi chọn BakaSU/SukiSU và bật SUSFS.
- CVE patch và các tính năng mới tắt mặc định. Có nút chọn patch không có nghĩa hiệu quả bảo mật hoặc khả năng boot đã được kiểm chứng.
- KPM chỉ có bật thông thường/tắt; không quảng cáo chế độ patched mà upstream chưa hỗ trợ. Official/GKI thuần tự tắt KPM; GKI thuần tắt cả NoMount và xuất patch SUSFS.
- NTSync chỉ dùng khi Droidspaces bật, tự tắt khi tắt Droidspaces. Android 16 chỉ có bật/tắt Droidspaces, không lướt qua các lựa chọn slot không áp dụng; ZRAM không hiển thị trên 6.12.
- OnePlus 8E chỉ áp dụng dòng 6.6/6.12; yêu cầu một phiên bản qua Actions, không bật đồng thời Release/xuất patch vì workflow tổng chưa nhận `supp_op`.
- Tên version kiểm tra trước khi xếp hàng: tối đa 64 ký tự, chữ ASCII, số, `.`, `_`, `+`, `-`.

## Phiên bản và workflow

`bot/gki_manifest.json` là source đi cùng deployment, không phải state runtime. Nó chứa matrix gốc cùng các sublevel có trong catalog của repo build. Những bản có trong matrix giữ ngày/revision gốc, đặc biệt Android 12; những sublevel bổ sung lấy ngày mới nhất và revision `r1`, giống resolver của fork. Không cần clone repo kernel trên VPS chạy bot.

Menu đã có các bản mới trong source vừa tích hợp. 6.12 không có `X` vì upstream chưa cho build LTS. Khi chọn tất cả sublevel của một dòng, bot gửi danh sách rõ ràng, không gửi chuỗi rỗng làm workflow chỉ build matrix mặc định cũ. Chọn cả `X` và số không còn gây lỗi sắp xếp.

- Một dòng + một sublevel + Actions → `kernel-custom.yml`.
- Release/Pre-Release hoặc xuất patch SUSFS → `main.yml`, kể cả chỉ chọn một bản. Không âm thầm bỏ yêu cầu Release như trước.
- Nhiều sublevel → `main.yml`; `supp_op` không được lặng lẽ bỏ đi, bot yêu cầu chọn lại cấu hình hỗ trợ.
- `/build` vẫn xếp mỗi phiên bản thành một job custom riêng. Droidspaces/KPM dùng đúng kiểu lựa chọn, không bị đổi thành boolean chung; Re-Kernel và các tính năng mới cũng được chuyển tiếp.

## Job cũ và dashboard

Job đang chạy/đã gửi GitHub không bị thay đổi hay dispatch lần nữa. Trước lần gửi đầu tiên hoặc rebuild, job GKI chờ trong queue được kiểm tra bằng hợp đồng mới: chuẩn hóa ReSukiSU/KPM/SUSFS, bỏ input branch cũ và định tuyến lại nếu cần. MKSU, 6.12 LTS hoặc metadata không hợp lệ bị dừng trước HTTP và hoàn lượt key nếu đã giữ; không tự chuyển sang kernel khác.

Dashboard, catalog và thông báo dùng cùng cách hiểu `ksu_mode`/KPM, kể cả chuỗi `"false"`. Kernel thuần được lưu dưới mục `GKI`, không ghi đè link BakaSU/SukiSU. Các trường cấu hình mới được ghi vào metadata, NoMount/networking/NTSync/CVE được hiển thị trên card dashboard. Lịch sử/link cũ không bị đổi tên hàng loạt.

Khôi phục chỉ từ tên artifact không đủ suy ra chế độ KSU/tất cả tính năng vì upstream vẫn dùng cùng tiền tố biến thể cho kernel thuần. Ưu tiên backup state/catalog, không coi recovery từ artifact là bản sao đầy đủ cấu hình.

## Cập nhật catalog lần sau

Thực hiện sau khi đã rà soát/cập nhật clone workflow:

```powershell
python -m pip install -r tools/requirements.txt
python tools/update_gki_manifest.py D:\projectt\GKI_KernelSU_SUSFS
python tools/update_gki_manifest.py D:\projectt\GKI_KernelSU_SUSFS --apply
```

Lệnh đầu in bản JSON dự kiến, `--apply` chỉ ghi `bot/gki_manifest.json`. Review diff, kiểm thử rồi commit/deploy bot. Không chạy lệnh này trên source upstream chưa tích hợp hợp đồng bot. Công cụ không cần đọc `.env`, không gọi API và không sửa repo build. Catalog bot không tự nhảy theo upstream chưa review.

`tools/local_patch.py` và `tools/sync_and_patch.py` hiện **chỉ kiểm tra**, giữ nguyên source và runner. Cờ `--apply` cũ bị từ chối rõ ràng để tránh phá matrix động hoặc tự ép self-hosted. Muốn cập nhật upstream, merge bằng Git tại repo build và giải quyết conflict; muốn đổi runner có công cụ riêng với preview mặc định.

## Kiểm tra

Test local kiểm tra các payload/choice/type với YAML thực tế của fork, mọi phiên bản menu với resolver kernel, revision, chế độ, Release/export, callback và job cũ. Không gọi Bot API/GitHub thật. Thư mục `tests/` tiếp tục được gitignore theo chính sách repo; manifest và module hợp đồng là source cần commit.
