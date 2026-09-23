import yaml
from pathlib import Path
from types import SimpleNamespace

root_markers = ["config.yaml", ".git", "requirements.txt", "src"]

def _get_project_root() -> Path:
    """Climb up from the current file until a sentinel file is found."""
    
    current_path = Path(__file__).resolve()
    # Search for root markers
    
    for parent in [current_path, *current_path.parents]:
        if any((parent / marker).exists() for marker in root_markers):
            return parent
        
        # STOPPER: hit the OS root (e.g., C:\ or /)
        if parent == parent.parent:
            break
        
    return current_path.parent  # Fallbacks

def _find_config(root: Path) -> Path | None:
    """Find config yaml file in the project root."""
    for name in ("config.yaml", "config.yml"):
        path = root / name
        if path.exists():
            return path
    raise FileNotFoundError("No config yaml file found in the project root.")

def _load_config(config_path: Path | None = None) -> dict:
    """Load configuration parameters from config.yaml."""
    path = config_path or (CONFIG_PATH if 'CONFIG_PATH' in globals() else None)
    if path is None or not path.exists():
        return {}
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
            return cfg if isinstance(cfg, dict) else {}
    except Exception:
        cfg = {}
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and ":" in line:
                    k, v = line.split(":", 1)
                    v_clean = v.split("#")[0].strip()
                    try:
                        cfg[k.strip()] = int(v_clean)
                    except ValueError:
                        cfg[k.strip()] = v_clean
        return cfg

ROOT = _get_project_root()
CONFIG_PATH = _find_config(ROOT)
cfg_dir = _load_config(CONFIG_PATH)

#### constant configs #####
# Build dict with both lower and upper keys
dual_keys = {k: v for k, v in cfg_dir.items()}
dual_keys.update({k.upper(): v for k, v in cfg_dir.items()})

cfg = CFG = SimpleNamespace(**dual_keys)

#### dynamic configs with functions ####

RAW_DIR = ROOT / cfg_dir.get('raw_data_dir')
TEMP_DIR = ROOT / cfg_dir.get('temp_data_dir')
EXTRACT_DIR = ROOT / cfg_dir.get('extract_data_dir')
LAYOUT_DIR = ROOT / cfg_dir.get('layout_dir', 'docs/layout')
if not LAYOUT_DIR.exists() and (ROOT / "docs").exists():
    LAYOUT_DIR = ROOT / "docs"

MANIFEST_PATH = ROOT / cfg_dir.get('manifest_path')
LOG_DIR = ROOT / cfg_dir.get('log_dir')
