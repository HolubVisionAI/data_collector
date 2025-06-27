import os
import csv
import re
import hashlib
import logging
from src.utils.utils import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# duplicate PDF pattern (_2.pdf, (3).pdf, ...)
# DUP_PDF_PATTERN = re.compile(r"(.+?)(?:_[2-9]|_1\d|_20|\(\d+\))\.pdf$", re.IGNORECASE)
DUP_PDF_PATTERN = re.compile(r"_(?:[2-9]|1[0-9]|20)\.pdf$", re.IGNORECASE)

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


def process_category(category_dir: str, ext: str) -> tuple[int, int, int, int]:
    """
    Walks a category directory, removes non-target files, duplicate PDFs, renames long filenames,
    and returns (file_count, total_size_bytes, removed_duplicates, removed_non_target).
    """
    file_count = 0
    total_size = 0
    removed_dup = 0
    removed_non = 0
    for root, _, files in os.walk(category_dir):
        for filename in files:
            file_path = os.path.join(root, filename)
            _, file_ext = os.path.splitext(filename)
            file_ext = file_ext.lower()
            if ext != 'video':
                # target extensions: ext, .csv, .xlsx
                if file_ext not in {f'.{ext}', '.csv', '.xlsx'}:
                    try:
                        os.remove(file_path)
                        removed_non += 1
                        logger.info(f"🗑️ Removed non-target: {file_path}")
                    except Exception as e:
                        logger.warning(f"Failed removing non-target {file_path}: {e}")
                    continue

                # remove duplicate PDFs
                if file_ext == f'.{ext}' and DUP_PDF_PATTERN.search(filename):
                    try:
                        os.remove(file_path)
                        removed_dup += 1
                        logger.info(f"🗑️ Removed duplicate: {file_path}")
                    except Exception as e:
                        logger.warning(f"Failed removing duplicate {file_path}: {e}")
                    continue

            # rename if too long and target file]
            # if file_ext == f'.{ext}':
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


def scan_folder_single(root_path: str, langs: list[str], types: list[str]) -> dict[str, dict]:
    results = {}
    if not os.path.isdir(root_path):
        logger.warning(f"Invalid scan path: {root_path}")
        return results

    for lang in langs:
        lang_path = os.path.join(root_path, lang)
        if not os.path.isdir(lang_path):
            continue
        for file_type in types:
            category_path = os.path.join(lang_path, file_type)
            if not os.path.isdir(category_path):
                continue
            key = f"{lang}/{file_type}"
            logger.info(f"Processing category: {key}")
            count, size_bytes, dup_rm, non_rm = process_category(category_path, file_type)
            if count > 0:
                results[key] = {
                    'count': count,
                    'size_gb': round(size_bytes / (1024 ** 3), 2),
                    'removed_duplicates': dup_rm,
                    'removed_non_target': non_rm
                }
    return results


def format_and_save(all_results: dict[str, dict[str, dict]], output_csv: str = "summary.csv"):
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
    logger.info(f"✅ Summary written to {output_csv}")


if __name__ == '__main__':
    try:
        cfg = load_config().get('scan_folder', {})
        input_dirs = cfg.get('INPUT_DIR', [])
        langs = cfg.get('LANG', [])
        types = cfg.get('FILE_TYPE', [])

        all_summaries = {}
        for root in input_dirs:
            logger.info(f"🔍 Scanning: {root}")
            summary = scan_folder_single(root, langs, types)
            all_summaries[root] = summary
        format_and_save(all_summaries)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
