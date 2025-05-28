import os
import yaml


def load_config(config_path="config.yaml"):
    if not os.path.exists(config_path):
        print(f"⚠️ Config file not found: {config_path}")
        print("🔧 Creating a blank config.yaml file...")

        default_yaml = """# Example config.yaml
root_paths:
  - parent path of language directory


valid_langs:
  - EN
  - CN
  - RU

valid_types:
  - PDF
  - MP4
"""
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(default_yaml)

        print("✅ Created config.yaml. Please edit it and re-run the script.")
        exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
