# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.5] - 2025-01-14

### Changed
- **Default model**: Changed from GPT-5.2 to Claude Opus 4.5
- **Thinking mode**: Now enabled by default, use `-nt` flag to disable
- Updated documentation to reflect new defaults

## [0.3.4] - 2025-01-14

### Added
- **Two-color tool display**: Tool descriptions now appear in YELLOW, directory paths in CYAN
- **New color config**: Added `tool_path` color configuration (default: CYAN) for `(In ...)` paths
- **Smart command detection**: Improved `python -c` pattern detection to show "🐍 Run inline Python" instead of truncated code
- **Better interrupt handling**: Added missing `_log_end_turn()` method to handle Ctrl+C gracefully

### Fixed
- **KeyboardInterrupt crash**: Fixed AttributeError when pressing Ctrl+C during execution
- **Divider placement**: Fixed divider positioning before "Continuing interrupted turn..." message
- **Tuple unpacking**: Fixed tuple unpacking in CLI display history building
- **Display history**: Fixed two-color formatting in session resume/replay

### Improved
- Tool execution display now uses dual-color formatting for better readability
- Display history replay applies correct color formatting to tool entries with paths

### Refactored
- Updated `get_tool_description()` to return `(description, path)` tuple
- Updated `tool_exec.py` to handle tuple-based descriptions
- Updated `agent.py` `_print()` callback for dual-color output
- Added `_describe_tool_exec_tuple()` helper function in `tools.py`

## [0.3.3] - 2025-01-13

### Fixed
- Restored Shift+Enter newline functionality via terminal mapping
- Cleaned up tool execution history display

## [0.3.2] - 2025-01-13

### Added
- Smart reactive compaction for context overflow handling
- Multi-provider support (GPT-5.2, Claude Opus/Sonnet/Haiku)

### Changed
- Moved UI colors and prefixes to config.yaml for easier customization
- Streamlined guidelines and enhanced token counting logic

## [0.3.0-0.3.1] - 2025-01-12

### Added
- Initial public release
- Docker-first file operations
- Multi-provider support (OpenAI, Anthropic)
- Session resume functionality
- Auto-compact context management
- Extended thinking mode
- Cost tracking and token usage display
- Incremental debug logging
