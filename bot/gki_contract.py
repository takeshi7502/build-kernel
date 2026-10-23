"""One contract for GKI menus, dispatch inputs and historical build labels.

No network or .env access. The bundled manifest is refreshed by the maintainer
from a reviewed build checkout, so a bot deployment never needs that checkout.
"""

import json
import re
from pathlib import Path

VARIANTS = ["BakaSU", "SukiSU", "SukiSU(40726)", "SukiSU(40548)", "KittiSU", "Official"]
MODE_FULL, MODE_NO_SUSFS, MODE_NO_KSU = "关闭", "禁用SUSFS", "禁用KSU"
MODES = {MODE_FULL: "KSU + SUSFS", MODE_NO_SUSFS: "Chỉ KSU", MODE_NO_KSU: "GKI thuần (không KSU)"}
MODE_CALLBACKS = {"full": MODE_FULL, "no_susfs": MODE_NO_SUSFS, "no_ksu": MODE_NO_KSU}
KPM_OPTIONS = ["disabled (关闭)", "enabled (开启)"]
DROIDSPACES_OPTIONS = ["off", "678", "123", "345"]
CUSTOM_WORKFLOW = "kernel-custom.yml"
SUPP_OP_TARGETS = {"build_a15_6_6", "build_a16_6_12"}

_manifest = json.loads(Path(__file__).with_name("gki_manifest.json").read_text(encoding="utf-8"))
TARGET_META = {
    key: (value["android"], value["kernel"]) for key, value in _manifest["targets"].items()
}
SUB_LEVEL_META = {
    key: {
        str(row["sub_level"]): (row["os_patch_level"], row.get("revision", "r1"))
        for row in value["builds"]
    }
    for key, value in _manifest["targets"].items()
}
SUB_LEVELS = {key: list(rows) for key, rows in SUB_LEVEL_META.items()}
BOOLEAN_FEATURES = (
    "use_zram",
    "use_bbg",
    "use_rekernel",
    "use_nomount",
    "use_networking",
    "cve_2026_43499_patch",
    "droidspaces_ntsync",
    "export_susfs_patches",
    "supp_op",
)
COMMON_FIELDS = {
    "kernelsu_variant",
    "ksu_mode",
    "version",
    "build_time",
    "use_kpm",
    "droidspaces",
    "artifact_upload_mode",
    *BOOLEAN_FEATURES,
}
MAIN_FIELDS = (COMMON_FIELDS - {"supp_op"}) | {
    "build_all",
    "release_type",
    "sub_levels",
    *TARGET_META,
}
CUSTOM_FIELDS = COMMON_FIELDS - {"export_susfs_patches"}


def enabled(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled", "enabled (开启)"}


def effective_mode(inputs):
    """Mirror build fork precedence; historical jobs keep their legacy semantics."""
    mode = inputs.get("ksu_mode")
    if mode == MODE_NO_KSU:
        return MODE_NO_KSU
    if enabled(inputs.get("cancel_susfs"), mode is None) or mode == MODE_NO_SUSFS:
        return MODE_NO_SUSFS
    return MODE_FULL


def susfs_enabled(inputs):
    return effective_mode(inputs) == MODE_FULL


def kpm_enabled(inputs):
    return effective_mode(inputs) != MODE_NO_KSU and enabled(inputs.get("use_kpm"))


def mode_label(inputs):
    return MODES[effective_mode(inputs)]


def normalize_inputs(inputs, target=""):
    values = dict(inputs)
    variant = str(values.get("kernelsu_variant", "BakaSU"))
    variant = "BakaSU" if variant == "ReSukiSU" else variant
    if variant not in VARIANTS:
        raise ValueError(f"KernelSU {variant} chưa được workflow hỗ trợ. Hãy chọn lại biến thể.")
    mode = values.get("ksu_mode")
    if mode is not None and mode not in MODES:
        raise ValueError("Chế độ KSU/SUSFS không hợp lệ.")
    values["kernelsu_variant"] = variant
    values["ksu_mode"] = effective_mode(values)
    values.pop("cancel_susfs", None)
    values.pop("kernelsu_branch", None)
    raw_kpm = values.get("use_kpm", False)
    if str(raw_kpm).startswith("patched"):
        raise ValueError("KPM patched chưa được upstream hỗ trợ; chọn bật thông thường hoặc tắt.")
    values["use_kpm"] = KPM_OPTIONS[int(enabled(raw_kpm))]
    for key in BOOLEAN_FEATURES:
        values[key] = enabled(values.get(key))
    values["droidspaces"] = str(values.get("droidspaces", "off"))
    if values["droidspaces"] not in DROIDSPACES_OPTIONS:
        raise ValueError("Tùy chọn Droidspaces không hợp lệ.")
    if values["droidspaces"] == "off":
        values["droidspaces_ntsync"] = False
    if values["ksu_mode"] == MODE_NO_KSU:
        values["use_kpm"], values["use_nomount"], values["export_susfs_patches"] = (
            KPM_OPTIONS[0],
            False,
            False,
        )
    if variant == "Official":
        values["use_kpm"] = KPM_OPTIONS[0]
    if values["ksu_mode"] != MODE_FULL or variant not in {"SukiSU", "BakaSU"}:
        values["export_susfs_patches"] = False
    if target == "build_a16_6_12":
        values["use_zram"] = False
    if target and target not in SUPP_OP_TARGETS:
        values["supp_op"] = False
    version = str(values.get("version", "")).strip()
    if len(version) > 64 or (version and not re.fullmatch(r"[A-Za-z0-9._+-]+", version)):
        raise ValueError("Tên kernel tối đa 64 ký tự, chỉ dùng chữ ASCII, số, . _ + -")
    values["version"] = version
    values.setdefault("build_time", "")
    artifact = values.get("artifact_upload_mode", "all")
    artifact = {"上传全部": "all", "仅上传 AnyKernel3.zip": "anykernel"}.get(artifact, artifact)
    if artifact not in {"all", "anykernel"}:
        raise ValueError("Chế độ artifact phải là all hoặc anykernel.")
    values["artifact_upload_mode"] = artifact
    return values


def custom_inputs(inputs, target, sub):
    if target not in TARGET_META or sub not in SUB_LEVEL_META[target]:
        raise ValueError(
            "Phiên bản kernel không còn được hỗ trợ; mở lại menu để chọn phiên bản mới."
        )
    values = normalize_inputs(inputs, target)
    _, kernel = TARGET_META[target]
    patch, revision = SUB_LEVEL_META[target][sub]
    return {
        **{key: values[key] for key in CUSTOM_FIELDS if key in values},
        "kernel_version": kernel,
        "sub_level": sub,
        "os_patch_level": patch,
        "revision": revision or "r1",
    }


def prepare_dispatch(inputs, main_workflow="main.yml"):
    targets = [
        target
        for target in TARGET_META
        if enabled(inputs.get(target)) or enabled(inputs.get("build_all"))
    ]
    if not targets:
        raise ValueError("Chưa chọn dòng kernel cần build.")
    values = normalize_inputs(inputs, targets[0] if len(targets) == 1 else "")
    raw_subs = str(values.get("sub_levels", "") or "").strip()
    if raw_subs and any(not sub.strip() for sub in raw_subs.split(",")):
        raise ValueError("Danh sách sublevel có phần tử trống; hãy chọn lại phiên bản.")
    subs = list(dict.fromkeys(sub.strip().upper() for sub in raw_subs.split(",") if sub.strip()))
    for target in targets:
        if any(sub not in SUB_LEVEL_META[target] for sub in subs):
            raise ValueError(
                f"Sublevel không hợp lệ cho {TARGET_META[target][1]}; hãy chọn lại phiên bản."
            )
    values["sub_levels"] = ",".join(subs)
    release = values.get("release_type", "Actions")
    if release not in {"Actions", "Pre-Release", "Release"}:
        raise ValueError("Loại release không hợp lệ.")
    # Custom has no release/export jobs. Never silently discard these requests.
    if (
        len(targets) == len(subs) == 1
        and release == "Actions"
        and not values["export_susfs_patches"]
    ):
        return values, CUSTOM_WORKFLOW, custom_inputs(values, targets[0], subs[0])
    if values["supp_op"]:
        raise ValueError(
            "OnePlus 8E chỉ hỗ trợ Actions với một phiên bản; tắt Release/xuất patch hoặc chọn một bản."
        )
    values["release_type"] = release
    values["build_all"] = enabled(inputs.get("build_all"))
    for target in TARGET_META:
        values[target] = enabled(inputs.get(target))
    return values, main_workflow, {key: values[key] for key in MAIN_FIELDS if key in values}


def upgrade_queued_job(job, main_workflow="main.yml"):
    """Validate/migrate only undispatched GKI jobs, never replay an accepted POST."""
    raw = job.get("dispatch_inputs", job.get("inputs", {}))
    if job.get("type") not in {"gki", "buildsave"} or not (
        "kernelsu_variant" in raw
        or "kernel_version" in raw
        or any(key in raw for key in TARGET_META)
    ):
        return None
    stored = job.get("inputs", raw)
    if job.get("type") == "gki" and any(key in stored for key in TARGET_META):
        values, filename, payload = prepare_dispatch(stored, main_workflow)
    else:
        target = next(
            (
                key
                for key, (_, kernel) in TARGET_META.items()
                if kernel == raw.get("kernel_version")
            ),
            "",
        )
        sub = str(raw.get("sub_level", "")).upper()
        payload = custom_inputs(raw, target, sub)
        if raw.get("os_patch_level", payload["os_patch_level"]) != payload["os_patch_level"]:
            raise ValueError(
                "Ngày phát hành trong hàng đợi không khớp catalog mới; chọn lại phiên bản."
            )
        if raw.get("revision"):
            revision = str(raw["revision"]).strip()
            if not re.fullmatch(r"r\d+", revision):
                raise ValueError("Revision trong hàng đợi không hợp lệ.")
            payload["revision"] = revision
        values, filename = payload, CUSTOM_WORKFLOW
    result = {"inputs": values, "dispatch_inputs": payload, "workflow_file": filename}
    if job.get("type") == "buildsave":
        result["bs_variant"] = values["kernelsu_variant"]
    return result


def cycle_feature(inputs, key, target=""):
    """Shared toggle behavior for /gki and /build; clear dependent options."""
    if key == "ksu_mode":
        modes = list(MODES)
        inputs["ksu_mode"] = modes[(modes.index(effective_mode(inputs)) + 1) % len(modes)]
        inputs.pop("cancel_susfs", None)
    elif key == "use_kpm":
        inputs[key] = KPM_OPTIONS[int(not kpm_enabled(inputs))]
    elif key == "droidspaces":
        current = inputs.get(key, "off")
        choices = ["off", "678"] if target == "build_a16_6_12" else DROIDSPACES_OPTIONS
        inputs[key] = (
            choices[(choices.index(current) + 1) % len(choices)] if current in choices else "off"
        )
    elif key == "artifact_upload_mode":
        inputs[key] = "anykernel" if inputs.get(key, "all") == "all" else "all"
    elif key in BOOLEAN_FEATURES:
        inputs[key] = not enabled(inputs.get(key))
    else:
        raise ValueError("Tùy chọn không hợp lệ.")
    inputs.update(normalize_inputs(inputs, target))
