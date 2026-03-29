import os
import glob
import re
import sys

def switch_runner(target_runner):
    print(f"🔄 Đang chuyển đổi toàn bộ workflows sang: {target_runner}...")
    
    # Path to the user's local clone of GKI_KernelSU_SUSFS
    repo_dir = r"D:\project\GKI_KernelSU_SUSFS\.github\workflows"
    
    if not os.path.exists(repo_dir):
        print(f"❌ Không tìm thấy thư mục: {repo_dir}")
        return

    yml_files = glob.glob(os.path.join(repo_dir, "*.yml"))
    if not yml_files:
        print("❌ Không tìm thấy file .yml nào.")
        return

    count = 0
    for file_path in yml_files:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Regex tìm 'runs-on: xxx' nhưng bỏ qua các job có tên 'setup-...' (thường phải chạy trên github để tính toán)
        # Vì Regex khó an toàn tuyệt đối, ta thay thế cơ bản 
        if target_runner == "self-hosted":
            # Đổi từ ubuntu-latest sang self-hosted
            new_content = re.sub(r'runs-on:\s*ubuntu-latest', 'runs-on: self-hosted', content)
            
            # Khôi phục riêng setup job (setup-build-kernels) về ubuntu-latest (bắt buộc server github làm)
            new_content = re.sub(r'(setup-build-kernels[\s\S]*?runs-on:\s*)self-hosted', r'\1ubuntu-latest', new_content)
            new_content = re.sub(r'(setup-build-kernels-[a-zA-Z0-9-]+[\s\S]*?runs-on:\s*)self-hosted', r'\1ubuntu-latest', new_content)

        else:
            # Đổi self-hosted thành ubuntu-latest
            new_content = re.sub(r'runs-on:\s*self-hosted', 'runs-on: ubuntu-latest', content)

        if new_content != content:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"  ✅ Đã vá: {os.path.basename(file_path)}")
            count += 1
            
    print(f"🎉 Hoàn tất! {count} file đã được đổi sang chạy trên: {target_runner}")
    print("👉 Hãy cd vào D:\\project\\GKI_KernelSU_SUSFS và 'git commit', 'git push' nhé.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("💡 Cách dùng: python switch_runner.py [github | vps]")
        print("   - github: Chạy trên server gốc miễn phí của GitHub (ubuntu-latest)")
        print("   - vps: Chạy trên server riêng siêu mạnh của bạn (self-hosted)")
        sys.exit(1)
        
    choice = sys.argv[1].lower().strip()
    if choice == "vps" or choice == "self-hosted":
        switch_runner("self-hosted")
    elif choice == "github" or choice == "ubuntu-latest":
        switch_runner("ubuntu-latest")
    else:
        print("❌ Lựa chọn không hợp lệ. Chọn 'github' hoặc 'vps'.")
