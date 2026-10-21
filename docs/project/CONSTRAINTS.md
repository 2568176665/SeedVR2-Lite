# SeedVR2 - Project Constraints

## Hard Constraints
- Application must run independently without ComfyUI environment
- **SeedVR2 model only supports NVIDIA CUDA GPU. CPU inference is NOT supported.**
- Development environment uses system Python 3.12+ (recommended) or a self-extracted WinPython 3.12 at the project root (`WPy64-312101/`, auto-configured via `scripts\setup_winpython.py`) — WinPython is an optional portable runtime, not mandatory
- All batch files must use pure ASCII English to avoid encoding issues in Windows cmd.exe
- Model loading must strictly follow workflow parameters to prevent memory overflow
- If memory exceeds 95% (`memory_threshold`, hard limit `_MEMORY_THRESHOLD`), immediately terminate the model (the 90% ratio in `gpu_utils` is only the safety line for parameter recommendation, not a termination threshold)
- I/O components (embeddings, norms, etc.) must remain on GPU and not be offloaded to CPU RAM
- WebUI settings must exactly match the runtime configuration, with each settings section corresponding to a `config.yaml` field (the app runs standalone without ComfyUI; `config.yaml` is the single source of truth)
- Default parameters (model, resolution, tile size, etc.) in WebUI must match `config.yaml` defaults
- Default resolution follows config.yaml as the single source of truth (currently restore.default_resolution_h=1080, default_resolution_w=1920, resolution=2048); do not hardcode a fixed value in docs
- Folder scanning must pass the security/path_guard.py whitelist check; arbitrary directory traversal is forbidden
- All API responses must converge to the unified {success, data, error} structure

## Engineering Conventions
- 3B model uses model_lib.dit_v2.nadit architecture with num_layers=32, vid_dim=2560, mlp_type=swiglu
- 7B model uses model_lib.dit.nadit architecture with num_layers=36, vid_dim=3072, mlp_type=normal (current status: the vendored model_lib/dit v1 tree is mid-migration — importing it fails, so the 7B path is unavailable for now; see the exemption table in AGENTS.md §3 — maintainer-local file, not shipped with the repo. The 3B path via model_lib/dit_v2 is unaffected)
- window_method list in config must be automatically expanded to match num_layers length
- Web server uses native jinja2.Environment for template rendering instead of Starlette's Jinja2Templates
- Model loading must include memory pre-check: available memory >= 1.5 times model size
- Memory monitoring must include pre-load check and post-inference release
- Memory threshold: 95% hard limit (`_MEMORY_THRESHOLD`), plus `memory_min_available_gb` absolute floor (default 2.0GB, recommended 5% of device total RAM, e.g. 1.6GB for 32GB device)

## Lessons Learned
- Starlette 1.0.0 has compatibility issues with Jinja2 3.1.6 template caching mechanism
- BlockSwap's _protect_model_from_move prevents model.to("cpu") execution, causing VRAM leaks after DiT inference
- swap_io_components=true in config.yaml causes memory overflow by offloading I/O components to CPU RAM
