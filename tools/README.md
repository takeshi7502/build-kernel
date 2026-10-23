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

## Kiểm tra workflow trên local clone

```bash
python tools/local_patch.py /path/to/GKI_KernelSU_SUSFS
```

Chỉ kiểm tra hợp đồng workflow hiện đại; không sửa file, không thay runner. Cờ `--apply` cũ bị từ chối. Bộ vá cũ đã ngừng vì có thể phá matrix động và chuyển job sang self-hosted ngoài ý muốn. Merge upstream và sửa conflict tại repo build bằng Git.

## Cập nhật danh mục phiên bản cho bot

```bash
python tools/update_gki_manifest.py /path/to/GKI_KernelSU_SUSFS
python tools/update_gki_manifest.py /path/to/GKI_KernelSU_SUSFS --apply
```

Đọc clone workflow đã rà soát, giữ revision của matrix và bổ sung bản mới từ catalog. Mặc định in JSON; `--apply` chỉ cập nhật `bot/gki_manifest.json` để review/commit/deploy cùng bot. Không dùng `.env`, không gọi API, không sửa workflow. Xem [hợp đồng GKI mới](../docs/gki-build-contract.md).

## Đổi loại runner

```bash
python tools/switch_runner.py github /path/to/GKI_KernelSU_SUSFS
python tools/switch_runner.py vps /path/to/GKI_KernelSU_SUSFS --apply
```

`github` dùng `ubuntu-24.04`, `vps` dùng `self-hosted`. Chỉ thay nhãn đơn tương ứng; giữ job `setup-*`/`prepare-*`, nhãn tùy biến, danh sách nhãn và biểu thức. Không sửa workflow bằng regex. Đây là thao tác riêng, không tự chạy khi setup bot.

## Kiểm tra fork qua GitHub API (chỉ đọc)

```bash
python tools/sync_and_patch.py
```

Đọc cấu hình GitHub từ `.env` gốc qua `bot.config`, chỉ GET workflow trên nhánh được cấu hình và kiểm tra contract. Không merge upstream, không PUT file, không commit hay đổi runner. `--apply` đã ngừng và sẽ báo lỗi trước khi đọc cấu hình/kết nối mạng; `--skip-sync` chỉ được giữ tương thích CLI và không làm thay đổi hành vi kiểm tra.

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
