# Công cụ bảo trì tùy chọn

Bot thường không cần chạy công cụ trong thư mục này. Thực thi từ gốc repository để ví dụ đường dẫn tương đối dễ hiểu. Công cụ workflow cần dependency riêng:

```bash
python -m pip install -r tools/requirements.txt
```

## Backup state và catalog

Không cần dependency riêng của các tool workflow bên dưới; dùng dependency runtime của bot:

```bash
venv/bin/python tools/manage_backups.py create
venv/bin/python tools/manage_backups.py list
venv/bin/python tools/manage_backups.py verify /path/to/backup.zip
```

Các lệnh không gọi API remote. `create` lấy snapshot qua storage lock và tạo ZIP đã kiểm tra tại `BACKUP_DIR`; có thể chạy trong lúc bot hoạt động trên Linux. `verify` không ghi/giải nén. Backup chứa dữ liệu riêng tư; xem [vận hành](../docs/reliability.md) trước khi chia sẻ/khôi phục. `bash run.sh backup` và `bash run.sh backups` là cách gọi nhanh trên VPS.

## Patch workflow trên local clone

```bash
python tools/local_patch.py /path/to/GKI_KernelSU_SUSFS
python tools/local_patch.py /path/to/GKI_KernelSU_SUSFS --apply
```

Mặc định in diff; `--apply` ghi file. Patch bổ sung input `sub_levels`, tạo ma trận lọc phiên bản, bỏ concurrency ở workflow tổng và chuyển job thực thi sang `self-hosted`. Xem kỹ diff, vì đây là thay đổi hành vi build của repository kernel, không phải bước setup bot. Công cụ không commit/push.

## Đổi loại runner

```bash
python tools/switch_runner.py github /path/to/GKI_KernelSU_SUSFS
python tools/switch_runner.py vps /path/to/GKI_KernelSU_SUSFS --apply
```

`github` dùng `ubuntu-latest`, `vps` dùng `self-hosted`. Chỉ thay nhãn đơn tương ứng; giữ job `setup-*`, nhãn tùy biến, danh sách nhãn và biểu thức. Không sửa workflow bằng regex.

## Đồng bộ fork và patch qua GitHub API

```bash
python tools/sync_and_patch.py
python tools/sync_and_patch.py --apply
python tools/sync_and_patch.py --apply --skip-sync
```

Đọc cấu hình GitHub từ `.env` gốc qua `bot.config`. Chế độ mặc định chỉ đọc workflow hiện tại và báo file sẽ đổi; chưa merge upstream. `--apply` merge upstream rồi ghi patch, tạo commit trực tiếp vào branch được cấu hình. `--skip-sync` chỉ patch. Nếu fork đã diverge, công cụ dừng để xử lý merge thủ công; không force-reset branch. Các lệnh ghi không tự rollback commit trước đó nếu một file sau gặp lỗi.

## Khôi phục link dashboard

```bash
python tools/recover_links.py android12 5.10 web/data/android12/5.10.json
python tools/recover_links.py android12 5.10 web/data/android12/5.10.json --apply
```

Dùng `GITHUB_OWNER`, `GKI_REPO`, `GITHUB_TOKEN` từ `.env`, hoặc chỉ định `--owner`/`--repo`. Chỉ xét 100 run dispatch thành công gần nhất, bỏ artifact hết hạn và giữ run mới nhất cho mỗi phiên bản/variant. Mặc định xem trước; `--apply` cập nhật file local. Tạm dừng bot nếu bảo trì cùng catalog để tránh ghi đè thay đổi của nhau.

## Khôi phục job từ catalog có sẵn

`tools/recover_jobs.py` khôi phục record job từ catalog dashboard vào state local được cấu hình bởi `STORAGE_PATH`. Công cụ mặc định xem trước; chỉ ghi khi có `--apply`, qua lớp storage và backup local, không thay toàn bộ document MongoDB trực tiếp như bản cũ.

```bash
python tools/recover_jobs.py --help
python tools/recover_jobs.py
python tools/recover_jobs.py --apply
```

Dừng bot và backup state trước khi ghi dữ liệu khôi phục. Catalog chỉ chứa một phần thông tin của các build thành công, nên không thể khôi phục toàn bộ state/key/quyền quản trị đã mất.

## Cài self-hosted runner riêng

Không cần mục này nếu workflow dùng runner của GitHub. Lấy version và registration token ở Settings → Actions → Runners của repository kernel:

```bash
bash tools/setup_runners.sh install OWNER/REPO VERSION /srv/kernel-runners
bash tools/setup_runners.sh remove /srv/kernel-runners/runner-XXXXXXXX
```

Script hỗ trợ Linux x86_64. Mỗi lần `install` tạo một thư mục runner mới và hỏi token kín; không thay thế runner có sẵn. `remove` yêu cầu removal token riêng, chỉ hủy đăng ký runner tại đường dẫn chỉ định và giữ lại thư mục local. Không quét/xóa runner offline trên repository. Đây là máy biên dịch kernel nên tài nguyên phải tính theo workload build, độc lập với tài nguyên bot.
