import os
import sys

sys.path.insert(0, r"d:\project\build-kernel")
sys.path.insert(0, r"d:\project\build-kernel\bot")
import sync_and_patch

repo_dir = r"d:\project\GKI_KernelSU_SUSFS\.github\workflows"

kernels = [
    "kernel-a12-5-10.yml",
    "kernel-a13-5-15.yml",
    "kernel-a14-6-1.yml",
    "kernel-a15-6-6.yml",
    "kernel-a16-6-12.yml"
]

for f in kernels:
    p = os.path.join(repo_dir, f)
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as file:
            content = file.read()
        new_content = sync_and_patch.patch_kernel_yml(content)
        with open(p, "w", encoding="utf-8") as file:
            file.write(new_content)
        print(f"✅ Patched {f}")

p_build = os.path.join(repo_dir, "build.yml")
if os.path.exists(p_build):
    with open(p_build, "r", encoding="utf-8") as file:
        content = file.read()
    new_content = sync_and_patch.patch_build_yml(content)
    with open(p_build, "w", encoding="utf-8") as file:
        file.write(new_content)
    print("✅ Patched build.yml")

p_main = os.path.join(repo_dir, "main.yml")
if os.path.exists(p_main):
    with open(p_main, "r", encoding="utf-8") as file:
        content = file.read()
    new_content = sync_and_patch.patch_main_yml(content)
    with open(p_main, "w", encoding="utf-8") as file:
        file.write(new_content)
    print("✅ Patched main.yml")

print("All done!")
