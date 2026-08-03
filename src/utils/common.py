from pathlib import Path
from src.utils import logger

root_markers = [".git", "requirements.txt"]

def get_project_root() -> Path:
    """Climb up from the current file until a sentinel file is found."""
    
    current_path = Path(__file__).resolve()
    # Search for root markers
    
    for parent in [current_path, *current_path.parents]:
        if any((parent / marker).exists() for marker in root_markers):
            return parent
        
        # STOPPER: hit the OS root (e.g., C:\ or /)
        if parent == parent.parent:
            break
        
    logger.logging.warning(
        f"PROJECT_ROOT_NOT_FOUND: root markers [{root_markers}] not found."
        f"Falling back to parent of {current_path}"        
    )
    return current_path.parent  # Fallbacks