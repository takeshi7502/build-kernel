"""
Fix v4: Pass sub_levels through to build.yml and filter at step level there.
1. Add sub_levels input to build.yml
2. Add check step as first step in build.yml
3. Add if condition to all other steps in build.yml
4. Pass sub_levels from kernel-*.yml to build.yml
5. Remove broken job-level if from kernel-*.yml
"""
import config, aiohttp, asyncio, base64, sys, os, re
sys.stdout.reconfigure(encoding='utf-8')

OWNER = config.GITHUB_OWNER
REPO = config.GKI_REPO
TOKEN = config.GITHUB_TOKEN
BRANCH = config.GKI_DEFAULT_BRANCH
PATCH_DIR = os.path.join(os.path.dirname(__file__), "workflows_patch")

async def get_file(session, path):
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{path}?ref={BRANCH}"
    headers = {"Authorization": f"token {TOKEN}", "Accept": "application/vnd.github.v3+json"}
    async with session.get(url, headers=headers) as r:
        j = await r.json()
        content = base64.b64decode(j["content"]).decode()
        sha = j["sha"]
        return content, sha

async def put_file(session, path, content, sha, message):
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{path}"
    headers = {"Authorization": f"token {TOKEN}", "Accept": "application/vnd.github.v3+json"}
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "sha": sha,
        "branch": BRANCH
    }
    async with session.put(url, headers=headers, json=payload) as r:
        status = r.status
        if status in (200, 201):
            print(f"  ✅ {path}")
        else:
            j2 = await r.json()
            print(f"  ❌ {path}: {status} - {j2.get('message', '')}")
        return status

def fix_build_yml(content):
    """Add sub_levels input and check step to build.yml"""
    
    # 1. Add sub_levels input (after supp_op)
    old_supp = '''      supp_op:
        required: false
        type: boolean
        default: false'''
    new_supp = '''      supp_op:
        required: false
        type: boolean
        default: false
      sub_levels:
        description: "Comma-separated sub_levels to build (empty=all)"
        required: false
        type: string
        default: ""'''
    content = content.replace(old_supp, new_supp, 1)
    
    # 2. Add check step as FIRST step, and add if condition to all subsequent steps
    # Find "    steps:" and add check step after it
    check_step = '''      - name: Check sub_level filter
        id: check_sub
        run: |
          SUB_LEVELS="${{ inputs.sub_levels }}"
          CURRENT="${{ inputs.sub_level }}"
          if [ -z "$SUB_LEVELS" ]; then
            echo "Building sub_level $CURRENT (no filter applied)"
            echo "skip=false" >> $GITHUB_OUTPUT
          elif echo ",$SUB_LEVELS," | grep -q ",$CURRENT,"; then
            echo "Building sub_level $CURRENT (matched filter)"
            echo "skip=false" >> $GITHUB_OUTPUT
          else
            echo "Skipping sub_level $CURRENT (not in: $SUB_LEVELS)"
            echo "skip=true" >> $GITHUB_OUTPUT
          fi

'''
    
    if "Check sub_level filter" not in content:
        content = content.replace("    steps:\n", "    steps:\n" + check_step, 1)
    
    # 3. Add if condition to ALL steps after our check step
    lines = content.split("\n")
    new_lines = []
    past_check = False
    i = 0
    while i < len(lines):
        line = lines[i]
        
        if "id: check_sub" in line:
            past_check = True
        
        if past_check and line.strip().startswith("- name:") and "Check sub_level filter" not in line:
            indent = len(line) - len(line.lstrip())
            if_cond = " " * indent + "  if: steps.check_sub.outputs.skip != 'true'"
            
            new_lines.append(line)
            
            # Check if next line already has an if condition
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                if next_line.strip().startswith("if:"):
                    # Merge conditions
                    existing = next_line.strip()[3:].strip()
                    if existing.startswith("${{"):
                        existing = existing[3:]
                    if existing.endswith("}}"):
                        existing = existing[:-2]
                    existing = existing.strip()
                    merged = " " * indent + f"  if: ${{{{ steps.check_sub.outputs.skip != 'true' && ({existing}) }}}}"
                    new_lines.append(merged)
                    i += 2
                    continue
                else:
                    new_lines.append(if_cond)
                    i += 1
                    continue
        
        new_lines.append(line)
        i += 1
    
    return "\n".join(new_lines)

def fix_kernel_yml(content):
    """
    1. Remove broken job-level if
    2. Pass sub_levels to build.yml via with:
    """
    # Remove job-level if
    content = re.sub(
        r"    if: \$\{\{.*?contains.*?matrix\.sub_level.*?\}\}\n",
        "",
        content
    )
    
    # Add sub_levels to 'with:' block that calls build.yml
    # Find the 'with:' block after 'uses: ./.github/workflows/build.yml'
    # Add sub_levels as the last parameter
    # We need to add: sub_levels: ${{ inputs.sub_levels }}
    
    # Find the uses + with block and add sub_levels
    if "sub_levels: ${{ inputs.sub_levels }}" not in content:
        # Find enable_susfs line (last input in with:) and add after it
        # Try different possible last lines
        for last_param in ["enable_susfs:", "supp_op:"]:
            pattern = re.compile(r"(      " + last_param + r" .+\n)")
            match = pattern.search(content)
            if match:
                old = match.group(0)
                new = old.rstrip("\n") + "\n      sub_levels: ${{ inputs.sub_levels }}\n"
                content = content.replace(old, new, 1)
                break
    
    return content

async def main():
    os.makedirs(PATCH_DIR, exist_ok=True)
    
    async with aiohttp.ClientSession() as s:
        # 1. Fix build.yml
        print("Fixing build.yml...")
        content, sha = await get_file(s, ".github/workflows/build.yml")
        fixed = fix_build_yml(content)
        with open(os.path.join(PATCH_DIR, "build.yml"), "w", encoding="utf-8") as f:
            f.write(fixed)
        await put_file(s, ".github/workflows/build.yml", fixed, sha,
                      "feat: add sub_levels filtering at step level in build.yml")
        
        # 2. Fix kernel sub-workflows
        for fname in ["kernel-a12-5-10.yml", "kernel-a13-5-15.yml", "kernel-a14-6-1.yml", "kernel-a15-6-6.yml"]:
            print(f"Fixing {fname}...")
            path = f".github/workflows/{fname}"
            content, sha = await get_file(s, path)
            fixed = fix_kernel_yml(content)
            with open(os.path.join(PATCH_DIR, fname), "w", encoding="utf-8") as f:
                f.write(fixed)
            await put_file(s, path, fixed, sha,
                          f"fix: remove broken job-level if, pass sub_levels to build.yml in {fname}")
    
    print("\n✅ Done!")

asyncio.run(main())
