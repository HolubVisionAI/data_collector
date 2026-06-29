import os
import csv
from pathlib import Path
import re
import hashlib
import logging
import sys
from src.utils.utils import load_config

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
    
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# duplicate PDF pattern (_2.pdf, (3).pdf, ...)
# DUP_PDF_PATTERN = re.compile(r"(.+?)(?:_[2-9]|_1\d|_20|\(\d+\))\.pdf$", re.IGNORECASE)
DUP_PDF_PATTERN = re.compile(r"_(?:[2-9]|1[0-9]|20)\.pdf$", re.IGNORECASE)


def _normalize_file_type(value: str) -> str:
    return str(value or "").strip().lower().lstrip(".")


def _config_bool(value, default: bool = False) -> bool:
    if isinstance(value, list):
        value = value[0] if value else default
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def rename_if_too_long(root: str, filename: str, max_len: int = 50) -> str:
    name, ext = os.path.splitext(filename)
    if len(name) <= max_len:
        return filename
    # create truncated unique name
    prefix = name[:max_len - 10].rstrip()
    hash8 = hashlib.md5(name.encode()).hexdigest()[:8]
    new_name = f"{prefix}_{hash8}{ext}"
    new_path = os.path.join(root, new_name)
    # avoid collision
    count = 1
    while os.path.exists(new_path):
        new_name = f"{prefix}_{hash8}_{count}{ext}"
        new_path = os.path.join(root, new_name)
        count += 1
    os.rename(os.path.join(root, filename), new_path)
    logger.info(f"🔄 Renamed '{filename}' → '{new_name}'")
    return new_name


def process_category(
        category_dir: str,
        ext: str,
        only_count: bool = False,
        allowed_types: set[str] | None = None,
        remove_non_target: bool = True,
        remove_duplicates: bool = False,
) -> tuple[int, int, int, int]:
    """
    Walks a category directory, removes non-target files, duplicate PDFs, renames long filenames,
    and returns (file_count, total_size_bytes, removed_duplicates, removed_non_target).
    
    If only_count is True, skips all destructive operations (removing, renaming) and just counts files.
    """
    ext = _normalize_file_type(ext)
    allowed_types = {_normalize_file_type(item) for item in (allowed_types or {ext}) if _normalize_file_type(item)}
    allowed_extensions = {f".{item}" for item in allowed_types}

    file_count = 0
    total_size = 0
    removed_dup = 0
    removed_non = 0
    
    if only_count:
        logger.info(f"   📊 Counting only (no file modifications)")
    
    for root, _, files in os.walk(category_dir):
        for filename in files:
            file_path = os.path.join(root, filename)
            _, file_ext = os.path.splitext(filename)
            file_ext = file_ext.lower()
            
            if ext != 'video':
                # target extensions: ext, .csv, .xlsx
                if file_ext not in {f'.{ext}', '.xlsx'}:
                    if file_ext in allowed_extensions:
                        logger.debug("Preserving configured non-category file: %s", file_path)
                        continue

                    if remove_non_target and not only_count:
                        try:
                            os.remove(file_path)
                            removed_non += 1
                            logger.info(f"🗑️ Removed non-target: {file_path}")
                        except Exception as e:
                            logger.warning(f"Failed removing non-target {file_path}: {e}")
                    continue

                # remove duplicate PDFs
                if remove_duplicates and file_ext == f'.{ext}' and DUP_PDF_PATTERN.search(filename):
                    if not only_count:
                        try:
                            os.remove(file_path)
                            removed_dup += 1
                            logger.info(f"🗑️ Removed duplicate: {file_path}")
                        except Exception as e:
                            logger.warning(f"Failed removing duplicate {file_path}: {e}")
                    continue

            # rename if too long and target file (only if not only_count)
            if not only_count:
                new_name = rename_if_too_long(root, filename)
                filename = new_name
                file_path = os.path.join(root, filename)

            # accumulate
            try:
                size = os.path.getsize(file_path)
                total_size += size
                file_count += 1
            except Exception as e:
                logger.warning(f"Skipping size/count {file_path}: {e}")
    return file_count, total_size, removed_dup, removed_non


def scan_folder_single(
        root_path: str,
        langs: list[str],
        types: list[str],
        only_count: bool = False,
        remove_non_target: bool = True,
        remove_duplicates: bool = False,
) -> dict[str, dict]:
    results = {}
    if not os.path.isdir(root_path):
        logger.warning(f"Invalid scan path: {root_path}")
        return results

    normalized_types = [_normalize_file_type(file_type) for file_type in types]
    normalized_types = [file_type for file_type in normalized_types if file_type]
    allowed_types = set(normalized_types)

    for lang in langs:
        lang_path = os.path.join(root_path, lang)
        if not os.path.isdir(lang_path):
            continue
        for file_type in normalized_types:
            category_path = os.path.join(lang_path, file_type)
            if not os.path.isdir(category_path):
                continue
            key = f"{lang}/{file_type}"
            logger.info(f"Processing category: {key}")

            # If the category directory contains immediate subdirectories, summarize per-subfolder
            try:
                children = [d for d in os.listdir(category_path) if os.path.isdir(os.path.join(category_path, d))]
            except Exception:
                children = []

            if children:
                # summarize each immediate subfolder
                for child in sorted(children):
                    sub_path = os.path.join(category_path, child)
                    count, size_bytes, dup_rm, non_rm = process_category(
                        sub_path,
                        file_type,
                        only_count,
                        allowed_types=allowed_types,
                        remove_non_target=remove_non_target,
                        remove_duplicates=remove_duplicates,
                    )
                    if count > 0:
                        results[f"{lang}/{file_type}/{child}"] = {
                            'count': count,
                            'size_gb': round(size_bytes / (1024 ** 3), 2),
                            'removed_duplicates': dup_rm,
                            'removed_non_target': non_rm
                        }
                        # log per-subfolder summary immediately
                        logger.info("%s/%s/%s -> count=%s, size=%.2f GB", lang, file_type, child, count, round(size_bytes / (1024 ** 3), 2))
            else:
                # no immediate subfolders: fall back to whole category
                count, size_bytes, dup_rm, non_rm = process_category(
                    category_path,
                    file_type,
                    only_count,
                    allowed_types=allowed_types,
                    remove_non_target=remove_non_target,
                    remove_duplicates=remove_duplicates,
                )
                if count > 0:
                    results[key] = {
                        'count': count,
                        'size_gb': round(size_bytes / (1024 ** 3), 2),
                        'removed_duplicates': dup_rm,
                        'removed_non_target': non_rm
                    }
                    logger.info("%s -> count=%s, size=%.2f GB", key, count, round(size_bytes / (1024 ** 3), 2))
    return results


def format_and_save(all_results: dict[str, dict[str, dict]], output_csv: str = "summary.csv"):
    # compute totals while writing
    total_count = 0
    total_bytes = 0
    total_removed_dup = 0
    total_removed_non = 0
    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Folder", "Path", "Count", "Capacity (GB)", "Removed Dups", "Removed Others"])
        for root_path, summary in all_results.items():
            for path, data in summary.items():
                writer.writerow([
                    root_path,
                    path,
                    data['count'],
                    data['size_gb'],
                    data['removed_duplicates'],
                    data['removed_non_target']
                ])
                total_count += data.get('count', 0)
                total_bytes += int(data.get('size_gb', 0) * (1024 ** 3))
                total_removed_dup += data.get('removed_duplicates', 0)
                total_removed_non += data.get('removed_non_target', 0)

        # write totals footer
        total_gb = round(total_bytes / (1024 ** 3), 2)
        writer.writerow([])
        writer.writerow(["TOTAL", "", total_count, total_gb, total_removed_dup, total_removed_non])
    logger.info(f"✅ Summary written to {output_csv}")
    logger.info("Total files across all roots: %s, Total capacity: %s GB", total_count, total_gb)


if __name__ == '__main__':
    try:
        cfg = load_config().get('scan_folder', {})
        input_dirs = cfg.get('INPUT_DIR', [])
        langs = cfg.get('LANG', [])
        types = cfg.get('FILE_TYPE', [])
        only_count = cfg.get('ONLY_COUNT', False)
        remove_non_target = _config_bool(cfg.get('REMOVE_NON_TARGET', True), True)
        remove_duplicates = _config_bool(cfg.get('REMOVE_DUPLICATES', False), False)
        
        # Handle both list and boolean values for ONLY_COUNT
        only_count = _config_bool(only_count, False)

        if only_count:
            logger.info("⚠️  ONLY_COUNT mode: Counting files only, no modifications will be made")

        logger.info("REMOVE_NON_TARGET=%s, REMOVE_DUPLICATES=%s", remove_non_target, remove_duplicates)

        all_summaries = {}
        total_roots = len(input_dirs)
        for idx, root in enumerate(input_dirs, start=1):
            percent = idx / total_roots * 100 if total_roots else 100
            logger.info("Scanning root %s/%s (%.1f%%): %s", idx, total_roots, percent, root)
            logger.info(f"🔍 Scanning: {root}")
            summary = scan_folder_single(
                root,
                langs,
                types,
                only_count,
                remove_non_target=remove_non_target,
                remove_duplicates=remove_duplicates,
            )
            all_summaries[root] = summary
        format_and_save(all_summaries)

        # Aggregate and log a concise summary for the run
        def _log_aggregate_summary(all_results: dict[str, dict[str, dict]]):
            total_files = 0
            total_bytes = 0
            total_removed_dup = 0
            total_removed_non = 0
            roots_with_data = 0
            per_root_summary = {}

            for root_path, summary in all_results.items():
                root_files = 0
                root_bytes = 0
                root_dup = 0
                root_non = 0
                for path, data in summary.items():
                    root_files += data.get('count', 0)
                    root_bytes += int(data.get('size_gb', 0) * (1024 ** 3))
                    root_dup += data.get('removed_duplicates', 0)
                    root_non += data.get('removed_non_target', 0)
                if root_files > 0:
                    roots_with_data += 1
                total_files += root_files
                total_bytes += root_bytes
                total_removed_dup += root_dup
                total_removed_non += root_non
                per_root_summary[root_path] = (root_files, round(root_bytes / (1024 ** 3), 2), root_dup, root_non)

            def _human_gb(bytes_val):
                return f"{round(bytes_val / (1024 ** 3), 2)} GB"

            logger.info("\n===== Scan Summary =====")
            logger.info("Roots scanned: %s (with data: %s)", len(all_results), roots_with_data)
            logger.info("Total files: %s", total_files)
            logger.info("Total capacity: %s", _human_gb(total_bytes))
            logger.info("Total removed duplicates: %s", total_removed_dup)
            logger.info("Total removed non-target: %s", total_removed_non)

            # Show per-root top-level summary (quiet if many roots)
            if len(per_root_summary) <= 20:
                logger.info("\nPer-root breakdown:")
                for root_path, (cnt, gb, dup, non) in per_root_summary.items():
                    logger.info("- %s: files=%s, size=%s, removed_dup=%s, removed_non=%s", root_path, cnt, f"{gb} GB", dup, non)
            else:
                logger.info("Per-root breakdown omitted (more than 20 roots).")
            logger.info("===== End Summary =====\n")

        _log_aggregate_summary(all_summaries)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
