import os
import sys

# Thêm đường dẫn để import
sys.path.append(os.path.dirname(__file__))
from sync_and_patch import patch_build_yml, patch_main_yml, patch_kernel_yml

REPO_DIR = r"d:\project\GKI_KernelSU_SUSFS"

files_to_patch = {
    ".github/workflows/build.yml": patch_build_yml,
    ".github/workflows/main.yml": patch_main_yml,
    ".github/workflows/kernel-a12-5-10.yml": patch_kernel_yml,
    ".github/workflows/kernel-a13-5-15.yml": patch_kernel_yml,
    ".github/workflows/kernel-a14-6-1.yml": patch_kernel_yml,
    ".github/workflows/kernel-a15-6-6.yml": patch_kernel_yml,
    ".github/workflows/kernel-a16-6-12.yml": patch_kernel_yml,
}

def main():
    print(f"Bắt đầu patch các file trong {REPO_DIR} ...\n")
    for rel_path, patch_func in files_to_patch.items():
        full_path = os.path.join(REPO_DIR, rel_path.replace("/", os.sep))
        if not os.path.exists(full_path):
            print(f"⚠️ Bỏ qua: Không tìm thấy file {full_path}")
            continue
            
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        try:
            new_content = patch_func(content)
            if new_content != content:
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                print(f"✅ Đã vá thành công: {rel_path}")
            else:
                print(f"ℹ️ Đã có sẵn patch, không cần vá lại: {rel_path}")
        except Exception as e:
            print(f"❌ Lỗi khi vá {rel_path}: {e}")

if __name__ == "__main__":
    main()
