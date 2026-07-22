"""PDF Engine — blueprint-driven PDCA executor modules.

All modules follow the same convention:
- Pure functions where possible
- State read/write via pdf_engine_shared functions (_load_state, _save_state)
- No direct CLI interaction (commands live in pdf-engine.py)
"""
